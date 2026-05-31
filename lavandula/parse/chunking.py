"""Section and table extraction from Docling DoclingDocument.

Converts Docling's rich document model into flat dicts suitable for
insertion into the lava_parse schema.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lavandula.parse import config
from lavandula.parse.config import filter_metadata

logger = logging.getLogger(__name__)

_BYTES_PER_MB = 1_000_000  # decimal MB, matching how corpus file sizes are reported

# Strip C0 control characters that PostgreSQL rejects in text/jsonb columns,
# preserving legitimate whitespace (\t 0x09, \n 0x0a, \r 0x0d). Matches the
# control-char policy in faithfulness/grounding.py and reports/classify.py.
# Docling's OCR/table extraction can emit NUL (0x00) and other control chars;
# any that reach an INSERT raise "A string literal cannot contain NUL".
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _scrub(value: Any) -> Any:
    """Recursively strip control characters from strings in str/list/dict.

    Single producer-side sanitizer for every Docling-derived text field that
    flows into lava_parse (body, heading, parent_headings, table caption,
    markdown, data_json cells, metadata). Non-string scalars pass through.
    """
    if isinstance(value, str):
        return _CTRL_RE.sub("", value)
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    return value


class DoclingParseError(Exception):
    """Raised when Docling fails to parse a PDF."""


# ---------------------------------------------------------------------------
# Spec 0058 — pre-parse triage, conditional-OCR detector, pipeline options.
#
# Everything below the DoclingParseError is PURE and unit-testable (no docling,
# no DB, no GPU) EXCEPT build_converter()/parse_pdf() which lazily import
# docling and are exercised by the integration tier. The worker (Phase 3) wires
# the triage decision + the chosen detector source + the spike-decided timeout
# into ParseOptions and threads them through parse_pdf().
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParseOptions:
    """Bounded, explicit Docling pipeline configuration for one document.

    Built by build_parse_options() from the triage decision + config. Pure data
    — build_converter() turns it into a real DocumentConverter on the GPU.
    """

    do_ocr: bool = True
    images_scale: float = config.IMAGES_SCALE_CAP
    table_mode_fast: bool = config.TABLEFORMER_FAST
    do_cell_matching: bool = True
    max_num_pages: int = config.MAX_NUM_PAGES
    document_timeout: float | None = None
    generate_page_images: bool = False
    # Observability: was this doc triaged, and why (parse_outcome='downgraded').
    downgrade: bool = False
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class OcrSignal:
    """The text-layer signals available for the conditional-OCR decision.

    text_source is the 0060 classification ('text_native'|'scanned'|
    'pdftotext_failed'|None-if-no-row); pdftotext_char_count is from
    lava_parse.pdftotext; first_page_text_len is the corpus fallback signal.
    """

    text_source: str | None = None
    pdftotext_char_count: int | None = None
    first_page_text_len: int | None = None


def compute_triage(
    file_size_bytes: int | None,
    page_count: int | None,
    text_signal: int | None,
    *,
    mb_per_page_threshold: float = config.POISON_MB_PER_PAGE,
    text_floor: int = config.POISON_TEXT_FLOOR,
    abs_file_size_cap: int = config.ABS_FILE_SIZE_CAP,
    abs_page_cap: int = config.ABS_PAGE_CAP,
) -> dict:
    """Cheap, deterministic poison-triage (spec §3.1). Never raises.

    Returns {downgrade: bool, reasons: [str], mb_per_page: float|None}.
    A True downgrade means parse with reduced images_scale (NOT a skip) — the
    per-doc timeout applies regardless. Absolute ceilings fire independent of
    the mb/page ratio so a bypass (blank pages dropping the ratio) is still caught.
    """
    reasons: list[str] = []
    pages = page_count if (isinstance(page_count, int) and page_count > 0) else None
    size = file_size_bytes if (isinstance(file_size_bytes, int) and file_size_bytes > 0) else None
    text = text_signal if isinstance(text_signal, int) and text_signal >= 0 else 0

    mb_per_page: float | None = None
    if pages is not None and size is not None:
        mb_per_page = (size / _BYTES_PER_MB) / pages
        if mb_per_page > mb_per_page_threshold and text < text_floor:
            reasons.append("poison_profile")

    # Absolute, ratio-independent ceilings.
    if size is not None and size > abs_file_size_cap:
        reasons.append("abs_file_size")
    if pages is not None and pages > abs_page_cap:
        reasons.append("abs_page_count")

    return {"downgrade": bool(reasons), "reasons": reasons, "mb_per_page": mb_per_page}


def choose_detector_source(
    backfill_fraction: float | None,
    *,
    min_backfill: float = config.OCR_DETECTOR_BACKFILL_MIN,
) -> str:
    """Pick the run-level OCR detector source (Codex determinism guard, §3.4).

    The 0060 pdftotext signal is preferred, but only once the backfill is
    substantially complete — otherwise the OCR decision would flip run-to-run as
    the backfill fills in. Below the threshold, use the first_page_text fallback
    uniformly. The chosen source is recorded in parse_runs.stats_json.
    """
    if backfill_fraction is not None and backfill_fraction >= min_backfill:
        return "pdftotext"
    return "first_page_text"


def decide_skip_ocr(
    signal: OcrSignal,
    *,
    detector_source: str = "first_page_text",
    text_floor: int = config.OCR_TEXT_FLOOR,
) -> bool:
    """Conditional-OCR decision (spec §3.4). Returns True to SKIP OCR.

    Precedence (first available wins), fail-toward-completeness:
      1. 0060 pdftotext (only when it is the chosen run-level source):
         - an explicit 'scanned'/'pdftotext_failed' text_source -> keep OCR;
         - otherwise a healthy pdftotext char_count (>= floor) -> skip OCR. The
           char_count is the primary signal because it is populated corpus-wide
           by the 0060 backfill, so it is available even on a doc's FIRST parse
           (text_source lives on documents and only exists once a doc is parsed);
         - an explicit 'text_native' with no char_count -> skip OCR.
      2. Fallback (or when a doc has no 0060 signal at all): first_page_text
         length >= floor -> skip OCR.
      3. Signals absent / uncertain -> keep OCR ON (a wrongly-OCR'd text doc is
         just slower; a wrongly-skipped scanned doc loses all content).
    """
    if detector_source == "pdftotext":
        if signal.text_source in ("scanned", "pdftotext_failed"):
            return False  # explicit negative -> keep OCR
        if signal.pdftotext_char_count is not None:
            return signal.pdftotext_char_count >= text_floor
        if signal.text_source == "text_native":
            return True

    if signal.first_page_text_len is not None and signal.first_page_text_len >= text_floor:
        return True

    return False  # default: keep OCR ON


def build_parse_options(
    *,
    skip_ocr: bool,
    downgrade: bool,
    reasons: tuple[str, ...] | list[str] = (),
    document_timeout: float | None = None,
    images_scale_cap: float | None = None,
    images_scale_downgrade: float | None = None,
    table_mode_fast: bool | None = None,
    max_num_pages: int | None = None,
    generate_page_images: bool = False,
) -> ParseOptions:
    """Assemble bounded ParseOptions from the triage + OCR decisions (pure)."""
    cap = config.IMAGES_SCALE_CAP if images_scale_cap is None else images_scale_cap
    downscale = config.IMAGES_SCALE_DOWNGRADE if images_scale_downgrade is None else images_scale_downgrade
    fast = config.TABLEFORMER_FAST if table_mode_fast is None else table_mode_fast
    pages_cap = config.MAX_NUM_PAGES if max_num_pages is None else max_num_pages

    return ParseOptions(
        do_ocr=not skip_ocr,
        images_scale=downscale if downgrade else cap,
        table_mode_fast=fast,
        do_cell_matching=True,
        max_num_pages=pages_cap,
        document_timeout=document_timeout,
        generate_page_images=generate_page_images,
        downgrade=downgrade,
        reasons=tuple(reasons),
    )


def build_converter(options: ParseOptions) -> Any:
    """Construct a configured Docling DocumentConverter (integration-only).

    Lazily imports docling so the module imports without it. TableFormer FAST is
    set only when options.table_mode_fast is True (A/B-gated); otherwise the
    table mode is left at Docling's default to preserve current behavior.
    """
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = options.do_ocr
    pipeline_options.images_scale = options.images_scale
    pipeline_options.generate_page_images = options.generate_page_images
    # document_timeout is the native hang bound (set only on the §3.2 native path,
    # decided by the Phase-0 spike). Harmless to set when provided.
    if options.document_timeout is not None:
        pipeline_options.document_timeout = options.document_timeout

    if options.table_mode_fast:
        from docling.datamodel.pipeline_options import (
            TableFormerMode,
            TableStructureOptions,
        )

        pipeline_options.table_structure_options = TableStructureOptions(
            mode=TableFormerMode.FAST,
            do_cell_matching=options.do_cell_matching,
        )

    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )


def parse_pdf(pdf_path: Path, options: ParseOptions | None = None) -> Any:
    """Run Docling DocumentConverter on a PDF. Returns DoclingDocument.

    options=None preserves the current bare-converter behavior (the worker still
    calls parse_pdf(pdf_path) until Phase 3 wires explicit options). When options
    are supplied, a bounded converter is built and the hard max_num_pages ceiling
    is applied to convert() on every doc.
    """
    from docling.document_converter import DocumentConverter

    if options is None:
        converter = DocumentConverter()
        result = converter.convert(str(pdf_path))
        return result.document

    converter = build_converter(options)
    result = converter.convert(str(pdf_path), max_num_pages=options.max_num_pages)
    return result.document


def extract_sections(doc: Any) -> list[dict]:
    """Run HierarchicalChunker on a DoclingDocument.

    Returns list of section dicts ready for DB insertion.
    """
    from docling.chunking import HierarchicalChunker

    chunker = HierarchicalChunker()
    chunks = list(chunker.chunk(doc))

    sections = []
    for i, chunk in enumerate(chunks):
        body = _scrub(chunk.text) if chunk.text else ""
        heading = _scrub(_extract_heading(chunk))
        sections.append(
            {
                "section_index": i,
                "heading": heading,
                "heading_level": _extract_heading_level(chunk),
                "body_text": body,
                "char_count": len(body),
                "page_start": _get_page_start(chunk),
                "page_end": _get_page_end(chunk),
                "parent_headings": _scrub(_get_parent_headings(chunk)),
            }
        )
    return sections


def extract_tables(doc: Any, sections: list[dict]) -> list[dict]:
    """Extract tables from DoclingDocument and link to containing sections.

    Linkage rule: table assigned to section whose page range contains
    the table's page AND whose document position most closely precedes
    the table's position. If no match, section_index = None.
    """
    tables = []
    doc_tables = getattr(doc, "tables", None) or []

    for i, table in enumerate(doc_tables):
        page_number = _get_table_page(table)
        section_index = _find_containing_section(page_number, table, sections)

        data_rows = _table_to_rows(table)
        row_count = len(data_rows)
        col_count = max((len(r) for r in data_rows), default=0)

        tables.append(
            {
                "table_index": i,
                "section_index": section_index,
                "page_number": page_number,
                "caption": _scrub(_get_table_caption(table)),
                "row_count": row_count,
                "col_count": col_count,
                "data_json": _scrub(data_rows),
                "markdown": _scrub(_table_to_markdown(table)),
            }
        )
    return tables


def get_document_metadata(doc: Any) -> dict:
    """Extract page_count, figure_count, and filtered metadata_json."""
    page_count = 0
    if hasattr(doc, "pages") and doc.pages:
        page_count = len(doc.pages)
    elif hasattr(doc, "num_pages"):
        page_count = doc.num_pages

    figure_count = len(getattr(doc, "pictures", None) or [])

    raw_metadata = {}
    if hasattr(doc, "metadata") and doc.metadata:
        if hasattr(doc.metadata, "model_dump"):
            raw_metadata = doc.metadata.model_dump()
        elif isinstance(doc.metadata, dict):
            raw_metadata = doc.metadata

    return {
        "page_count": page_count,
        "figure_count": figure_count,
        "metadata": _scrub(raw_metadata),
    }


def _extract_heading(chunk: Any) -> str | None:
    """Get the most specific heading for this chunk."""
    meta = getattr(chunk, "meta", None)
    if meta is None:
        return None
    headings = getattr(meta, "headings", None)
    if headings:
        return headings[-1]
    return None


def _extract_heading_level(chunk: Any) -> int | None:
    """Get heading depth (1=H1, 2=H2, etc.)."""
    meta = getattr(chunk, "meta", None)
    if meta is None:
        return None
    headings = getattr(meta, "headings", None)
    if headings:
        return len(headings)
    return None


def _get_parent_headings(chunk: Any) -> list[str]:
    """Get full ancestor heading chain."""
    meta = getattr(chunk, "meta", None)
    if meta is None:
        return []
    headings = getattr(meta, "headings", None)
    return list(headings) if headings else []


def _get_page_start(chunk: Any) -> int | None:
    """Get first page number this chunk appears on."""
    pages = _get_chunk_pages(chunk)
    return min(pages) if pages else None


def _get_page_end(chunk: Any) -> int | None:
    """Get last page number this chunk appears on."""
    pages = _get_chunk_pages(chunk)
    return max(pages) if pages else None


def _get_chunk_pages(chunk: Any) -> list[int]:
    """Extract page numbers from chunk provenance."""
    meta = getattr(chunk, "meta", None)
    if meta is None:
        return []
    doc_items = getattr(meta, "doc_items", None)
    if not doc_items:
        return []
    pages = []
    for item in doc_items:
        prov = getattr(item, "prov", None)
        if prov:
            for p in prov if isinstance(prov, list) else [prov]:
                page_no = getattr(p, "page_no", None) or getattr(p, "page", None)
                if page_no is not None:
                    pages.append(int(page_no))
    return pages


def _get_table_page(table: Any) -> int | None:
    """Get the page number a table appears on."""
    prov = getattr(table, "prov", None)
    if prov:
        items = prov if isinstance(prov, list) else [prov]
        for p in items:
            page_no = getattr(p, "page_no", None) or getattr(p, "page", None)
            if page_no is not None:
                return int(page_no)
    return None


def _find_containing_section(
    page_number: int | None, table: Any, sections: list[dict]
) -> int | None:
    """Find the section that contains this table based on page overlap."""
    if page_number is None:
        return None

    candidates = []
    for s in sections:
        ps = s.get("page_start")
        pe = s.get("page_end")
        if ps is not None and pe is not None:
            if ps <= page_number <= pe:
                candidates.append(s)
        elif ps is not None and ps == page_number:
            candidates.append(s)

    if not candidates:
        return None

    # Pick the section with highest section_index that precedes/contains the table
    candidates.sort(key=lambda s: s["section_index"])
    return candidates[-1]["section_index"]


def _get_table_caption(table: Any) -> str | None:
    """Extract table caption if available."""
    caption = getattr(table, "caption", None)
    if caption:
        text = getattr(caption, "text", None) or str(caption)
        return text if text else None
    return None


def _table_to_rows(table: Any) -> list[list]:
    """Convert a Docling table to list of row-lists."""
    try:
        df = table.export_to_dataframe()
        return [df.columns.tolist()] + df.values.tolist()
    except Exception:
        pass

    # Fallback: try to extract from table_cells or data attribute
    data = getattr(table, "data", None)
    if data and isinstance(data, list):
        return data
    return []


def _table_to_markdown(table: Any) -> str | None:
    """Convert a Docling table to markdown."""
    try:
        return table.export_to_markdown()
    except Exception:
        return None

"""Section and table extraction from Docling DoclingDocument.

Converts Docling's rich document model into flat dicts suitable for
insertion into the lava_parse schema.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from lavandula.parse.config import filter_metadata

logger = logging.getLogger(__name__)


class DoclingParseError(Exception):
    """Raised when Docling fails to parse a PDF."""


def parse_pdf(pdf_path: Path) -> Any:
    """Run Docling DocumentConverter on a PDF. Returns DoclingDocument."""
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
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
        body = chunk.text.replace("\x00", "") if chunk.text else ""
        heading = _extract_heading(chunk)
        if heading:
            heading = heading.replace("\x00", "")
        sections.append(
            {
                "section_index": i,
                "heading": heading,
                "heading_level": _extract_heading_level(chunk),
                "body_text": body,
                "char_count": len(body),
                "page_start": _get_page_start(chunk),
                "page_end": _get_page_end(chunk),
                "parent_headings": _get_parent_headings(chunk),
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
                "caption": _get_table_caption(table),
                "row_count": row_count,
                "col_count": col_count,
                "data_json": data_rows,
                "markdown": _table_to_markdown(table),
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
        "metadata": raw_metadata,
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

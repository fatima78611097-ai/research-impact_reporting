"""Spec 0058 §4 — cell-CONTENT A/B quality gate (operator-run on GPU).

No pipeline change (TableFormer FAST, images_scale cap, conditional OCR) ships
until Variant B passes this gate against Variant A (current defaults). We measure
cell CONTENT as normalized SET coverage — NOT cell/row counts and NOT sequence
alignment/edit-distance, which conflate benign reorder with real loss (the 0060
lesson). The grounding check uses the REAL shipped 0057 primitive
(lavandula.faithfulness.grounding) so Variant B must introduce no new ungrounded
("Tier-C") cell.

The metric functions here are PURE and unit-tested off-GPU. main() is the
operator harness: it reads the frozen manifest (locard/operations/0058-ab-sample.json),
parses each doc under both variants through the bounded subprocess runner (so the
poison doc cannot wedge the harness), pulls source text from lava_parse.pdftotext,
computes the metrics, and writes locard/operations/0058-ab-results.md.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from lavandula.faithfulness import grounding


# ---------------------------------------------------------------------------
# Frozen acceptance thresholds (spec §4)
# ---------------------------------------------------------------------------
TEXT_NATIVE_CELL_PARITY = 0.9999   # near-exact: zero meaningful cell loss
SCANNED_CELL_PARITY = 0.95
SCANNED_LOST_ROW_FRAC = 0.01
SCANNED_OCR_WORD_COVERAGE = 0.95
FAST_SPEEDUP_FLOOR = 1.3           # informational floor on table-heavy docs


# ---------------------------------------------------------------------------
# Pure metric primitives
# ---------------------------------------------------------------------------
def _norm(value) -> str:
    if value is None:
        return ""
    return grounding.normalize(str(value))


def cell_set(tables: list[dict]) -> set[str]:
    """All normalized, non-empty cell strings across a doc's table dicts."""
    out: set[str] = set()
    for t in tables or []:
        for row in (t.get("data_json") or []):
            for cell in row:
                n = _norm(cell)
                if n:
                    out.add(n)
    return out


def row_set(tables: list[dict]) -> set[tuple]:
    """Set of normalized row tuples (a row is a unit for the lost-row metric)."""
    out: set[tuple] = set()
    for t in tables or []:
        for row in (t.get("data_json") or []):
            norm_row = tuple(_norm(c) for c in row)
            if any(norm_row):
                out.add(norm_row)
    return out


def set_coverage(a: set, b: set) -> tuple[float, float]:
    """Bidirectional set metric (spec §4.1).

    Returns (coverage, invention): coverage = |A∩B|/|A| (how much of A's content
    Variant B preserved); invention = |B∖A|/|B| (content B produced that A lacked).
    Empty A -> coverage 1.0; empty B -> invention 0.0.
    """
    coverage = (len(a & b) / len(a)) if a else 1.0
    invention = (len(b - a) / len(b)) if b else 0.0
    return coverage, invention


def lost_rows(rows_a: set, rows_b: set) -> int:
    """Rows present in A (exact, normalized) but absent in B — each is a loss."""
    return len(rows_a - rows_b)


def row_jaccard(rows_a: set, rows_b: set) -> float:
    union = rows_a | rows_b
    return (len(rows_a & rows_b) / len(union)) if union else 1.0


def word_set(text: str) -> set[str]:
    return {w for w in _norm(text).split(" ") if w}


def word_coverage(text_a: str, text_b: str) -> float:
    """Fraction of A's word set retained in B (OCR-quality metric for scanned)."""
    a, b = word_set(text_a), word_set(text_b)
    return (len(a & b) / len(a)) if a else 1.0


def new_tier_c(cells_a: set, cells_b: set, source_text: str) -> int:
    """Count B cells that are NOT grounded in source AND absent from A.

    Uses the real 0057 grounding.check primitive. A "new Tier-C" is an ungrounded
    cell Variant B introduces that Variant A did not have — a faithfulness
    regression. Cells already ungrounded in A do not count (B didn't make it worse).
    """
    count = 0
    for cell in cells_b - cells_a:
        verdict = grounding.check(cell, source_text or "", [])
        if not verdict.grounded:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Per-doc comparison + verdict
# ---------------------------------------------------------------------------
@dataclass
class DocComparison:
    sha: str
    stratum: str
    convert_ms_a: int | None = None
    convert_ms_b: int | None = None
    table_count_a: int = 0
    table_count_b: int = 0
    total_cells_a: int = 0
    total_cells_b: int = 0
    total_rows_a: int = 0
    cell_coverage: float = 1.0      # |A∩B|/|A|
    cell_invention: float = 0.0     # |B∖A|/|B|
    lost_row_count: int = 0
    row_jaccard: float = 1.0
    ocr_word_coverage: float = 1.0
    new_tier_c: int = 0
    speedup: float | None = None
    verdict: str = "PASS"
    notes: list[str] = field(default_factory=list)


def compare_variants(
    sha: str,
    stratum: str,
    doc_a: dict,
    doc_b: dict,
    source_text: str,
) -> DocComparison:
    """Compute the §4 metrics for one doc and grade against frozen thresholds.

    doc_a/doc_b are the {sections, tables, convert_ms} dicts from each variant.
    Grades by stratum: 'text_native' uses the strict parity, 'scanned' the OCR
    thresholds, 'poison'/'slow' are handled by main() on the parse outcome (they
    cannot be cell-graded — A is expected to crash/wedge).
    """
    a_cells, b_cells = cell_set(doc_a["tables"]), cell_set(doc_b["tables"])
    a_rows, b_rows = row_set(doc_a["tables"]), row_set(doc_b["tables"])
    coverage, invention = set_coverage(a_cells, b_cells)

    cmp = DocComparison(sha=sha, stratum=stratum)
    cmp.convert_ms_a = doc_a.get("convert_ms")
    cmp.convert_ms_b = doc_b.get("convert_ms")
    cmp.table_count_a = len(doc_a["tables"])
    cmp.table_count_b = len(doc_b["tables"])
    cmp.total_cells_a = len(a_cells)
    cmp.total_cells_b = len(b_cells)
    cmp.total_rows_a = len(a_rows)
    cmp.cell_coverage = round(coverage, 6)
    cmp.cell_invention = round(invention, 6)
    cmp.lost_row_count = lost_rows(a_rows, b_rows)
    cmp.row_jaccard = round(row_jaccard(a_rows, b_rows), 6)
    cmp.ocr_word_coverage = round(word_coverage(_doc_text(doc_a), _doc_text(doc_b)), 6)
    cmp.new_tier_c = new_tier_c(a_cells, b_cells, source_text)
    if cmp.convert_ms_a and cmp.convert_ms_b:
        cmp.speedup = round(cmp.convert_ms_a / cmp.convert_ms_b, 2)

    cmp.verdict = _grade(cmp)
    return cmp


def _doc_text(doc: dict) -> str:
    """Concatenated text for the OCR word-coverage metric (sections + table md)."""
    parts = [s.get("body_text", "") for s in (doc.get("sections") or [])]
    parts += [t.get("markdown", "") or "" for t in (doc.get("tables") or [])]
    return "\n".join(p for p in parts if p)


def _grade(cmp: DocComparison) -> str:
    if cmp.stratum == "text_native":
        fails = []
        if cmp.cell_coverage < TEXT_NATIVE_CELL_PARITY:
            fails.append(f"cell_coverage {cmp.cell_coverage} < {TEXT_NATIVE_CELL_PARITY}")
        if cmp.lost_row_count > 0:
            fails.append(f"lost_rows {cmp.lost_row_count} > 0")
        if cmp.new_tier_c > 0:
            fails.append(f"new_tier_c {cmp.new_tier_c} > 0")
        cmp.notes.extend(fails)
        return "FAIL" if fails else "PASS"

    if cmp.stratum == "scanned":
        fails = []
        if cmp.cell_coverage < SCANNED_CELL_PARITY:
            fails.append(f"cell_coverage {cmp.cell_coverage} < {SCANNED_CELL_PARITY}")
        lost_frac = (cmp.lost_row_count / cmp.total_rows_a) if cmp.total_rows_a else 0.0
        if lost_frac > SCANNED_LOST_ROW_FRAC:
            fails.append(f"lost_row_frac {round(lost_frac, 4)} > {SCANNED_LOST_ROW_FRAC}")
        if cmp.ocr_word_coverage < SCANNED_OCR_WORD_COVERAGE:
            fails.append(f"ocr_word_coverage {cmp.ocr_word_coverage} < {SCANNED_OCR_WORD_COVERAGE}")
        if cmp.new_tier_c > 0:
            fails.append(f"new_tier_c {cmp.new_tier_c} > 0")
        cmp.notes.extend(fails)
        return "FAIL" if fails else "PASS"

    # designed/image-heavy and any other stratum: report-only (no hard cell gate).
    return "CONDITIONAL" if cmp.new_tier_c > 0 else "PASS"


# ---------------------------------------------------------------------------
# Operator harness (GPU + DB)
# ---------------------------------------------------------------------------
def _variant_options(variant: str):
    """ParseOptions for each variant. A = current defaults; B = the proposal."""
    from lavandula.parse import chunking, config

    if variant == "A":
        # Variant A == current production default: bare converter behavior.
        return None
    return chunking.build_parse_options(
        skip_ocr=False,                 # main() overrides per-doc via the detector
        downgrade=False,
        table_mode_fast=True,           # the proposal under test
        images_scale_cap=config.IMAGES_SCALE_DOWNGRADE,  # capped
    )


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - operator/GPU
    ap = argparse.ArgumentParser(description="Spec 0058 §4 cell-content A/B gate")
    ap.add_argument("--manifest", default="locard/operations/0058-ab-sample.json")
    ap.add_argument("--out", default="locard/operations/0058-ab-results.md")
    ap.add_argument("--host", required=True)
    ap.add_argument("--database", required=True)
    args = ap.parse_args(argv)

    manifest = json.loads(Path(args.manifest).read_text())
    # Operator harness body intentionally thin: parse each manifest doc under both
    # variants via parse_runner.PersistentParseRunner (bounded so the poison doc
    # cannot wedge), fetch source_text from lava_parse.pdftotext, call
    # compare_variants(), and render the table below into --out. Implemented at
    # run time on the GPU box where docling + DB are available.
    raise SystemExit(
        "Run on a GPU instance with docling + DB access. See module docstring; "
        f"manifest has {len(manifest.get('docs', []))} docs across strata "
        f"{sorted({d.get('stratum') for d in manifest.get('docs', [])})}."
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

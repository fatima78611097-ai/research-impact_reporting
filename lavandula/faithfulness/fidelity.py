"""Fidelity scoring module (Spec 0060 Phase 2).

Pure module — computes bidirectional word-set coverage between Docling text
and pdftotext text. No DB dependency.

Word-set tokenization: lowercase → Unicode NFC → collapse whitespace → split
on whitespace → discard tokens ≤ 1 char. Coverage = |A ∩ B| / |A|.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from lavandula.faithfulness.pdftotext_extract import ExtractResult

_WS_RE = re.compile(r"\s+")

COVERAGE_FLOOR = 0.50


@dataclass(frozen=True)
class FidelityScore:
    forward: float   # Docling → pdftotext (catches Docling invention)
    reverse: float   # pdftotext → Docling (catches Docling omission)


def word_set(text: str) -> set[str]:
    """Normalize and tokenize text into a word set for coverage comparison."""
    if not text:
        return set()
    text = text.lower()
    text = unicodedata.normalize("NFC", text)
    text = _WS_RE.sub(" ", text).strip()
    return {tok for tok in text.split(" ") if len(tok) > 1}


def coverage(source: set[str], target: set[str]) -> float:
    """Fraction of source words present in target. 0.0 if source is empty."""
    if not source:
        return 0.0
    return len(source & target) / len(source)


def score_fidelity(docling_text: str, pdftotext_text: str) -> FidelityScore:
    """Compute bidirectional word-set coverage between Docling and pdftotext."""
    docling_ws = word_set(docling_text)
    pdftotext_ws = word_set(pdftotext_text)
    return FidelityScore(
        forward=coverage(docling_ws, pdftotext_ws),
        reverse=coverage(pdftotext_ws, docling_ws),
    )


def classify_text_source(
    extract_result: ExtractResult,
    fidelity_score: FidelityScore | None,
) -> str:
    """Classify a document's text source type.

    Returns 'text_native', 'scanned', or 'pdftotext_failed'.
    """
    if extract_result.failed:
        return "pdftotext_failed"

    if extract_result.is_scanned:
        return "scanned"

    if fidelity_score is not None:
        if (fidelity_score.forward < COVERAGE_FLOOR
                and fidelity_score.reverse < COVERAGE_FLOOR):
            return "pdftotext_failed"

    return "text_native"

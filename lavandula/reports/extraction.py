"""Multi-page PDF text extraction (Spec 0035).

Extracts pages 1-5 from a PDF using pypdf, inline (no subprocess sandbox).
"""
from __future__ import annotations

import io
from dataclasses import dataclass

from pypdf import PdfReader

_MAX_PAGES = 5
_MAX_TEXT_LEN = 16_000


@dataclass
class ExtractionResult:
    pages_text: str
    pages_extracted: int
    total_pages: int | None
    extraction_method: str
    text_length: int


def extract_pages(pdf_bytes: bytes, max_pages: int = _MAX_PAGES) -> ExtractionResult:
    """Extract text from up to max_pages of a PDF.

    Returns ExtractionResult with extraction_method indicating success or
    failure mode (failed:encrypted, failed:corrupt, pypdf:empty, etc.).
    """
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:
        exc_name = type(exc).__name__.lower()
        if "encrypt" in exc_name or "password" in str(exc).lower():
            return ExtractionResult("", 0, None, "failed:encrypted", 0)
        return ExtractionResult("", 0, None, "failed:corrupt", 0)

    if reader.is_encrypted:
        tp = len(reader.pages) if reader.pages else None
        return ExtractionResult("", 0, tp, "failed:encrypted", 0)

    total_pages = len(reader.pages)
    pages_to_extract = min(max_pages, total_pages)
    page_texts = []
    pages_extracted = 0

    for i in range(pages_to_extract):
        try:
            text = reader.pages[i].extract_text() or ""
        except Exception:
            text = ""
        page_texts.append(f"\n--- PAGE {i + 1} ---\n{text}")
        pages_extracted += 1

    combined = "".join(page_texts).strip()

    stripped = combined
    for marker_prefix in ("--- PAGE 1 ---", "--- PAGE 2 ---", "--- PAGE 3 ---",
                          "--- PAGE 4 ---", "--- PAGE 5 ---"):
        stripped = stripped.replace(marker_prefix, "")
    if not stripped.strip():
        return ExtractionResult(
            "", pages_extracted, total_pages, "pypdf:empty", 0,
        )

    if len(combined) > _MAX_TEXT_LEN:
        combined = combined[:_MAX_TEXT_LEN]

    return ExtractionResult(
        pages_text=combined, pages_extracted=pages_extracted,
        total_pages=total_pages, extraction_method="pypdf",
        text_length=len(combined),
    )


__all__ = ["ExtractionResult", "extract_pages"]

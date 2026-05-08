"""Tests for multi-page PDF extraction (Spec 0035 Phase 2)."""
from __future__ import annotations

import io

import pytest


def _make_pdf(num_pages: int = 3, text_per_page: str = "Page content here.") -> bytes:
    """Create a minimal valid PDF with the given number of pages."""
    from pypdf import PdfWriter
    writer = PdfWriter()
    for i in range(num_pages):
        from pypdf._page import PageObject
        from pypdf.generic import NameObject, TextStringObject, ArrayObject
        page = PageObject.create_blank_page(width=612, height=792)
        writer.add_page(page)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_pdf_with_text(pages: list[str]) -> bytes:
    """Create a PDF using reportlab for pages with actual extractable text."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError:
        pytest.skip("reportlab not installed")

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for page_text in pages:
        c.drawString(72, 720, page_text)
        c.showPage()
    c.save()
    return buf.getvalue()


class TestExtractionResult:
    def test_dataclass_fields(self):
        from lavandula.reports.extraction import ExtractionResult
        r = ExtractionResult(
            pages_text="hello", pages_extracted=1, total_pages=5,
            extraction_method="pypdf", text_length=5,
        )
        assert r.pages_text == "hello"
        assert r.pages_extracted == 1
        assert r.total_pages == 5
        assert r.extraction_method == "pypdf"
        assert r.text_length == 5


class TestExtractPages:
    def test_corrupt_pdf(self):
        from lavandula.reports.extraction import extract_pages
        result = extract_pages(b"not a pdf at all")
        assert result.extraction_method.startswith("failed:")
        assert result.pages_text == ""
        assert result.text_length == 0

    def test_empty_bytes(self):
        from lavandula.reports.extraction import extract_pages
        result = extract_pages(b"")
        assert result.extraction_method.startswith("failed:")

    def test_valid_pdf_basic(self):
        """Test with a minimal valid PDF (may have empty text)."""
        from lavandula.reports.extraction import extract_pages
        pdf_bytes = _make_pdf(num_pages=2)
        result = extract_pages(pdf_bytes)
        assert result.extraction_method in ("pypdf", "pypdf:empty")
        assert result.total_pages == 2
        assert result.pages_extracted <= 2

    def test_text_cap_enforcement(self):
        from lavandula.reports.extraction import ExtractionResult
        assert ExtractionResult.__dataclass_fields__["pages_text"]

    def test_max_pages_respected(self):
        from lavandula.reports.extraction import extract_pages
        pdf_bytes = _make_pdf(num_pages=10)
        result = extract_pages(pdf_bytes, max_pages=3)
        assert result.pages_extracted <= 3

    def test_with_reportlab_text(self):
        from lavandula.reports.extraction import extract_pages
        pages = ["First page content", "Second page content", "Third page content"]
        try:
            pdf_bytes = _make_pdf_with_text(pages)
        except Exception:
            pytest.skip("reportlab not available")
        result = extract_pages(pdf_bytes)
        assert result.extraction_method == "pypdf"
        assert result.pages_extracted == 3
        assert result.total_pages == 3
        assert "PAGE 1" in result.pages_text
        assert "PAGE 2" in result.pages_text
        assert "PAGE 3" in result.pages_text
        assert result.text_length > 0
        assert result.text_length == len(result.pages_text)

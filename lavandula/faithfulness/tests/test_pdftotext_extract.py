"""Tests for pdftotext extraction module (Spec 0060 Phase 1)."""
from __future__ import annotations

import subprocess
import unittest.mock as mock

import pytest

from lavandula.faithfulness.pdftotext_extract import (
    ExtractResult,
    MAX_OUTPUT_BYTES,
    PDFTOTEXT_BIN,
    SCANNED_CHAR_THRESHOLD,
    extract_text,
    get_pdftotext_version,
)


# ============================================================
# Version detection
# ============================================================

class TestGetVersion:
    def test_parses_version_string(self):
        version = get_pdftotext_version()
        assert "pdftotext" in version.lower() or "version" in version.lower() or version != ""

    def test_version_is_cached(self):
        v1 = get_pdftotext_version()
        v2 = get_pdftotext_version()
        assert v1 == v2

    def test_missing_binary_raises(self):
        import lavandula.faithfulness.pdftotext_extract as mod
        old_bin = mod.PDFTOTEXT_BIN
        old_cached = mod._cached_version
        try:
            mod.PDFTOTEXT_BIN = "/nonexistent/pdftotext"
            mod._cached_version = None
            with pytest.raises(FileNotFoundError, match="poppler-utils"):
                get_pdftotext_version()
        finally:
            mod.PDFTOTEXT_BIN = old_bin
            mod._cached_version = old_cached


# ============================================================
# Text extraction
# ============================================================

class TestExtractText:
    def _make_minimal_pdf(self, text_content: str = "Hello World") -> bytes:
        """Create a minimal valid PDF with embedded text."""
        content = (
            f"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj "
            f"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
            f"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
            f"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj "
            f"4 0 obj<</Length {len(text_content) + 30}>>stream\n"
            f"BT /F1 12 Tf 100 700 Td ({text_content}) Tj ET\n"
            f"endstream endobj "
            f"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj "
        )
        xref_offset = len(b"%PDF-1.4\n") + len(content.encode())
        pdf = (
            f"%PDF-1.4\n{content}"
            f"xref\n0 6\n"
            f"0000000000 65535 f \n"
            f"0000000009 00000 n \n"
            f"0000000058 00000 n \n"
            f"0000000115 00000 n \n"
            f"0000000266 00000 n \n"
            f"0000000{len(content) + 9:03d} 00000 n \n"
            f"trailer<</Size 6/Root 1 0 R>>\n"
            f"startxref\n{xref_offset}\n%%EOF"
        )
        return pdf.encode("latin-1")

    def test_text_native_pdf(self):
        pdf = self._make_minimal_pdf("This is a test document with enough text to pass threshold")
        result = extract_text(pdf)
        assert not result.failed
        assert not result.is_scanned
        assert result.char_count > 0
        assert result.version != ""

    def test_empty_pdf_is_scanned(self):
        pdf = self._make_minimal_pdf("")
        result = extract_text(pdf)
        assert not result.failed
        assert result.is_scanned
        assert result.char_count < SCANNED_CHAR_THRESHOLD

    def test_nul_bytes_stripped(self):
        pdf = self._make_minimal_pdf("Hello\x00World test content with enough chars to pass threshold")
        result = extract_text(pdf)
        assert "\x00" not in result.text

    def test_timeout_handling(self):
        with mock.patch("lavandula.faithfulness.pdftotext_extract.subprocess.Popen") as mock_popen:
            proc_mock = mock.MagicMock()
            proc_mock.communicate.side_effect = [
                subprocess.TimeoutExpired(cmd="pdftotext", timeout=30),
            ]
            # second communicate() call in except handler (after kill)
            proc_mock.communicate.side_effect = subprocess.TimeoutExpired(cmd="pdftotext", timeout=30)
            # Need two calls: first raises, second (in except) returns normally
            call_count = [0]
            def comm_side_effect(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise subprocess.TimeoutExpired(cmd="pdftotext", timeout=30)
                return (b"", b"")
            proc_mock.communicate.side_effect = comm_side_effect
            mock_popen.return_value = proc_mock

            result = extract_text(b"%PDF-1.4 test")
            assert result.failed
            assert result.error == "timeout"
            proc_mock.kill.assert_called_once()

    def test_oversized_output_rejected(self):
        # Output now lands on disk (not buffered in RAM); oversize is detected by
        # the on-disk file size, so a bomb PDF can't OOM the worker.
        with mock.patch("lavandula.faithfulness.pdftotext_extract.subprocess.Popen") as mock_popen, \
             mock.patch("lavandula.faithfulness.pdftotext_extract.os.path.getsize") as mock_size:
            proc_mock = mock.MagicMock()
            proc_mock.communicate.return_value = (b"", b"")
            mock_popen.return_value = proc_mock
            mock_size.return_value = MAX_OUTPUT_BYTES + 1  # pretend pdftotext wrote a huge file

            result = extract_text(b"%PDF-1.4 test")
            assert result.failed
            assert result.error == "output_exceeded_10mb"

    def test_failed_result_fields(self):
        result = ExtractResult(
            text="", version="test", char_count=0,
            is_scanned=False, failed=True, error="test_error",
        )
        assert result.failed
        assert result.error == "test_error"

    def test_scanned_threshold(self):
        short_text = "x" * (SCANNED_CHAR_THRESHOLD - 1)
        r1 = ExtractResult(text=short_text, version="v", char_count=len(short_text), is_scanned=True)
        assert r1.is_scanned

        long_text = "x" * SCANNED_CHAR_THRESHOLD
        r2 = ExtractResult(text=long_text, version="v", char_count=len(long_text), is_scanned=False)
        assert not r2.is_scanned

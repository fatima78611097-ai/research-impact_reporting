"""Unit tests for lavandula.parse.worker — main loop logic."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Pre-inject docling and httpx mocks so worker module can be imported
import types
# NOTE: deliberately do NOT set _mock_docling.__version__ — the real installed
# docling has no __version__ attribute. Version is resolved via
# importlib.metadata.version('docling') in worker._parse_version(). Setting a
# fake __version__ here previously masked a crash (BUG 1) in the error path.
_mock_docling = types.ModuleType("docling")
sys.modules["docling"] = _mock_docling
sys.modules.setdefault("docling.document_converter", MagicMock())
sys.modules.setdefault("docling.chunking", MagicMock())
sys.modules.setdefault("httpx", MagicMock())

from lavandula.parse.worker import (
    _process_one,
    _download_batch,
    _spot_termination_pending,
    _record_error,
    _parse_version,
    _handle_transient,
    parse_args,
    PermanentError,
    TransientError,
)


class TestParseArgs:
    def test_required_args(self):
        args = parse_args(["--run-id", "1", "--host", "localhost", "--database", "test"])
        assert args.run_id == 1
        assert args.host == "localhost"
        assert args.database == "test"
        assert args.priority == "annual,impact"
        assert args.batch_size == 500

    def test_custom_priority(self):
        args = parse_args([
            "--run-id", "1", "--host", "h", "--database", "d",
            "--priority", "newsletter,program_description",
        ])
        assert args.priority == "newsletter,program_description"


class TestProcessOne:
    @patch("lavandula.parse.worker.chunking")
    @patch("lavandula.parse.worker.config")
    def test_returns_structured_result(self, mock_config, mock_chunking):
        mock_config.filter_metadata.return_value = {"title": "Report"}

        mock_doc = MagicMock()
        mock_chunking.parse_pdf.return_value = mock_doc
        mock_chunking.extract_sections.return_value = [
            {"section_index": 0, "char_count": 100, "heading": "Intro",
             "heading_level": 1, "body_text": "x" * 100,
             "page_start": 1, "page_end": 2, "parent_headings": ["Intro"]},
        ]
        mock_chunking.extract_tables.return_value = []
        mock_chunking.get_document_metadata.return_value = {
            "page_count": 5, "figure_count": 1, "metadata": {"title": "Report"},
        }

        item = {"content_sha256": "a" * 64, "source_org_ein": "12-345"}
        result = _process_one(Path("/tmp/test.pdf"), item)

        assert result["sha"] == "a" * 64
        assert result["org_ein"] == "12-345"
        # Version comes from importlib.metadata (or "docling-unknown" fallback) —
        # never from docling.__version__, which does not exist in production.
        assert result["parse_version"].startswith("docling-")
        assert result["page_count"] == 5
        assert result["section_count"] == 1
        assert result["table_count"] == 0
        assert result["total_text_chars"] == 100
        assert result["error"] is None

    @patch("lavandula.parse.worker.chunking")
    def test_raises_permanent_on_parse_failure(self, mock_chunking):
        mock_chunking.parse_pdf.side_effect = RuntimeError("corrupt PDF")

        item = {"content_sha256": "a" * 64, "source_org_ein": "12-345"}
        with pytest.raises(PermanentError, match="docling_parse_failed"):
            _process_one(Path("/tmp/test.pdf"), item)

    @patch("lavandula.parse.worker.chunking")
    def test_raises_permanent_on_empty_parse(self, mock_chunking):
        mock_doc = MagicMock()
        mock_chunking.parse_pdf.return_value = mock_doc
        mock_chunking.extract_sections.return_value = []
        mock_chunking.extract_tables.return_value = []
        mock_chunking.get_document_metadata.return_value = {
            "page_count": 1, "figure_count": 0, "metadata": {},
        }

        item = {"content_sha256": "a" * 64, "source_org_ein": "12-345"}
        with pytest.raises(PermanentError, match="empty_parse"):
            _process_one(Path("/tmp/test.pdf"), item)


class TestDownloadBatch:
    def test_downloads_valid_pdfs(self, tmp_path):
        mock_s3 = MagicMock()

        def _fake_download(bucket, key, path):
            Path(path).write_bytes(b"%PDF-1.4")

        mock_s3.download_file.side_effect = _fake_download

        sha = "a" * 64
        batch = [{"content_sha256": sha, "source_org_ein": "12-345"}]

        results = _download_batch(mock_s3, batch, tmp_path)

        assert sha in results
        assert results[sha].exists()

    def test_returns_none_for_failed_downloads(self, tmp_path):
        mock_s3 = MagicMock()
        mock_s3.download_file.side_effect = Exception("S3 timeout")

        sha = "b" * 64
        batch = [{"content_sha256": sha, "source_org_ein": "12-345"}]

        with patch("lavandula.parse.config.TRANSIENT_RETRY_COUNT", 1):
            with patch("lavandula.parse.config.TRANSIENT_RETRY_BASE_SECONDS", 0):
                results = _download_batch(mock_s3, batch, tmp_path)

        assert sha not in results

    def test_skips_invalid_sha(self, tmp_path):
        mock_s3 = MagicMock()
        batch = [{"content_sha256": "invalid", "source_org_ein": "12-345"}]

        results = _download_batch(mock_s3, batch, tmp_path)
        assert results == {}
        mock_s3.download_file.assert_not_called()


class TestSpotTerminationPending:
    def test_returns_true_on_200(self):
        mock_httpx = sys.modules["httpx"]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_httpx.get.return_value = mock_resp

        assert _spot_termination_pending() is True

    def test_returns_false_on_404(self):
        mock_httpx = sys.modules["httpx"]
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_httpx.get.return_value = mock_resp

        assert _spot_termination_pending() is False

    def test_returns_false_on_exception(self):
        mock_httpx = sys.modules["httpx"]
        mock_httpx.get.side_effect = ConnectionError("no metadata service")

        result = _spot_termination_pending()
        mock_httpx.get.side_effect = None  # reset
        assert result is False


class TestRecordError:
    @patch("lavandula.parse.worker.db")
    def test_records_error_row(self, mock_db):
        mock_conn = MagicMock()
        item = {"content_sha256": "c" * 64, "source_org_ein": "12-345"}

        _record_error(mock_conn, item, "download_failed: not in S3")

        mock_db.insert_document.assert_called_once()
        doc = mock_db.insert_document.call_args[0][1]
        assert doc["sha"] == "c" * 64
        assert doc["error"] == "download_failed: not in S3"
        assert doc["page_count"] == 0
        assert doc["sections"] == []
        assert doc["tables"] == []
        # Regression guard for BUG 1: the error path must resolve a version
        # without touching docling.__version__ (which would raise AttributeError
        # in production and kill the worker on the first failed document).
        assert doc["parse_version"].startswith("docling-")


class TestParseVersion:
    def test_returns_docling_prefixed_string(self):
        # Never touches docling.__version__; uses importlib.metadata with a
        # safe fallback, so it must always return a "docling-" prefixed string.
        v = _parse_version()
        assert isinstance(v, str)
        assert v.startswith("docling-")

    def test_never_raises_when_metadata_missing(self):
        with patch("importlib.metadata.version", side_effect=Exception("boom")):
            assert _parse_version() == "docling-unknown"


class TestHandleTransient:
    def _args(self):
        a = MagicMock()
        a.run_id = 7
        return a

    @patch("lavandula.parse.worker.db")
    def test_unclaims_below_cap(self, mock_db):
        # Mirror the production stats dict, which always pre-seeds these keys.
        stats = {"failed": 0, "transient_skipped": 0}
        attempts = {}
        _handle_transient(MagicMock(), self._args(), "a" * 64, "download_failed",
                          attempts, max_attempts=3, stats=stats)
        # First failure: row returned to pool for retry, not completed.
        mock_db.unclaim_work_item.assert_called_once()
        mock_db.complete_work_item.assert_not_called()
        assert attempts["a" * 64] == 1
        assert stats["transient_skipped"] == 1

    @patch("lavandula.parse.worker.db")
    def test_marks_errored_at_cap(self, mock_db):
        sha = "b" * 64
        stats = {"failed": 0, "transient_skipped": 0}
        attempts = {sha: 2}  # already failed twice; this is the 3rd
        _handle_transient(MagicMock(), self._args(), sha, "download_failed",
                          attempts, max_attempts=3, stats=stats)
        # At the cap: recorded as errored so it is never left stranded.
        mock_db.complete_work_item.assert_called_once()
        assert "transient_exhausted" in mock_db.complete_work_item.call_args[1]["error"]
        assert stats["failed"] == 1

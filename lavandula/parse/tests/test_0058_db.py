"""Unit tests for Spec 0058 DB additions — observability columns + parse signals."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from lavandula.parse import db


def _doc_cursor():
    """A mock conn/cursor wired for insert_document's `with conn:`+cursor."""
    conn = MagicMock()
    cur = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn, cur


class TestInsertDocumentOutcomeColumns:
    @patch("lavandula.parse.db.execute_values")
    def test_insert_includes_new_columns(self, _ev):
        conn, cur = _doc_cursor()
        doc = {
            "sha": "a" * 64, "org_ein": "12-3456789", "parse_version": "docling-2.93.0",
            "page_count": 3, "section_count": 1, "table_count": 0, "figure_count": 0,
            "total_text_chars": 100, "parse_duration_ms": 200, "error": None,
            "metadata_json": None, "sections": [], "tables": [],
            "docling_convert_ms": 150, "parse_outcome": "downgraded",
        }
        db.insert_document(conn, doc)
        sql, params = cur.execute.call_args_list[0][0]
        assert "docling_convert_ms" in sql
        assert "parse_outcome" in sql
        assert params["docling_convert_ms"] == 150
        assert params["parse_outcome"] == "downgraded"

    @patch("lavandula.parse.db.execute_values")
    def test_legacy_doc_without_new_keys_defaults_null(self, _ev):
        conn, cur = _doc_cursor()
        doc = {
            "sha": "b" * 64, "org_ein": "12-3456789", "parse_version": "docling-2.93.0",
            "page_count": 3, "section_count": 1, "table_count": 0, "figure_count": 0,
            "total_text_chars": 100, "parse_duration_ms": 200, "error": None,
            "metadata_json": None, "sections": [], "tables": [],
            # NOTE: no docling_convert_ms / parse_outcome -> must default to NULL
        }
        db.insert_document(conn, doc)
        _sql, params = cur.execute.call_args_list[0][0]
        assert params["docling_convert_ms"] is None
        assert params["parse_outcome"] is None


class TestFetchParseSignals:
    def test_empty_shas_returns_empty_no_query(self):
        conn = MagicMock()
        assert db.fetch_parse_signals(conn, []) == {}
        conn.cursor.assert_not_called()

    def test_joins_corpus_documents_pdftotext(self):
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cur.fetchall.return_value = [
            {"content_sha256": "a" * 64, "file_size_bytes": 5_600_000, "page_count": 4,
             "first_page_text_len": 19, "text_source": None, "pdftotext_char_count": None},
        ]
        out = db.fetch_parse_signals(conn, ["a" * 64])
        sql = cur.execute.call_args[0][0]
        assert "lava_corpus.corpus" in sql
        assert "LEFT JOIN lava_parse.documents" in sql
        assert "LEFT JOIN lava_parse.pdftotext" in sql
        assert out["a" * 64]["file_size_bytes"] == 5_600_000
        assert out["a" * 64]["first_page_text_len"] == 19


class TestPopulateWorkQueueExclusion:
    def test_anti_joins_parse_blocklist(self):
        conn, cur = _doc_cursor()
        cur.rowcount = 3
        db.populate_work_queue(conn, 1, ["annual"])
        sql = cur.execute.call_args[0][0]
        assert "lava_parse.parse_blocklist" in sql
        assert "NOT IN" in sql


class TestQuarantine:
    def test_quarantine_rejects_invalid_sha(self):
        import pytest
        with pytest.raises(ValueError, match="invalid sha256"):
            db.quarantine_doc(MagicMock(), "nope", "r", "operator")

    def test_quarantine_writes_audit_fields(self):
        conn, cur = _doc_cursor()
        db.quarantine_doc(conn, "a" * 64, "segfault exit 139", "operator:ronp",
                          evidence={"exit_code": 139})
        sql, params = cur.execute.call_args[0]
        assert "INSERT INTO lava_parse.parse_blocklist" in sql
        assert params["reason"] == "segfault exit 139"
        assert params["by"] == "operator:ronp"
        assert '"exit_code": 139' in params["ev"]

    def test_unquarantine_returns_true_when_removed(self):
        conn, cur = _doc_cursor()
        cur.rowcount = 1
        assert db.unquarantine_doc(conn, "a" * 64) is True

    def test_unquarantine_returns_false_when_absent(self):
        conn, cur = _doc_cursor()
        cur.rowcount = 0
        assert db.unquarantine_doc(conn, "a" * 64) is False

    def test_is_quarantined(self):
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cur.fetchone.return_value = (1,)
        assert db.is_quarantined(conn, "a" * 64) is True
        cur.fetchone.return_value = None
        assert db.is_quarantined(conn, "a" * 64) is False

    def test_get_quarantined_docs(self):
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cur.fetchall.return_value = [
            {"content_sha256": "a" * 64, "reason": "segfault", "quarantined_at": "t",
             "quarantined_by": "operator", "evidence_json": {"exit_code": 139}},
        ]
        out = db.get_quarantined_docs(conn)
        assert out[0]["reason"] == "segfault"


class TestGetRunPdftotextBackfill:
    def test_fraction(self):
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cur.fetchone.return_value = (100, 78)
        assert db.get_run_pdftotext_backfill(conn, 7) == 0.78

    def test_zero_when_empty_queue(self):
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cur.fetchone.return_value = (0, 0)
        assert db.get_run_pdftotext_backfill(conn, 7) == 0.0

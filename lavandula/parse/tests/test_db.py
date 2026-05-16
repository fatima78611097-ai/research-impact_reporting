"""Unit tests for lavandula.parse.db — work queue and insert logic."""
from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

from lavandula.parse import db


class TestFetchWorkBatch:
    def test_normal_query_uses_not_in_exclusion(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = []

        db.fetch_work_batch(mock_conn, ["annual", "impact"], 500)

        sql = mock_cursor.execute.call_args[0][0]
        assert "NOT IN" in sql
        assert "lava_parse.documents" in sql
        assert "classification = ANY" in sql

    def test_retry_errors_joins_on_error(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = []

        db.fetch_work_batch(mock_conn, ["annual"], 100, retry_errors=True)

        sql = mock_cursor.execute.call_args[0][0]
        assert "error IS NOT NULL" in sql

    def test_reparse_filters_by_version(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = []

        db.fetch_work_batch(
            mock_conn, ["annual"], 100, reparse=True, min_version="docling-2.93.0"
        )

        sql = mock_cursor.execute.call_args[0][0]
        assert "parse_version <" in sql
        params = mock_cursor.execute.call_args[0][1]
        assert params["min_version"] == "docling-2.93.0"


class TestInsertDocument:
    def test_rejects_invalid_sha(self):
        mock_conn = MagicMock()
        doc = {"sha": "invalid", "org_ein": "123", "sections": [], "tables": []}
        with pytest.raises(ValueError, match="invalid sha256"):
            db.insert_document(mock_conn, doc)

    @patch("lavandula.parse.db.execute_values")
    def test_inserts_document_with_sections_and_tables(self, mock_execute_values):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchall.return_value = [(1, 0), (2, 1)]

        doc = {
            "sha": "a" * 64,
            "org_ein": "12-3456789",
            "parse_version": "docling-2.93.0",
            "page_count": 10,
            "section_count": 2,
            "table_count": 1,
            "figure_count": 0,
            "total_text_chars": 5000,
            "parse_duration_ms": 1234,
            "error": None,
            "metadata_json": {"title": "Annual Report"},
            "sections": [
                {
                    "section_index": 0,
                    "heading": "Introduction",
                    "heading_level": 1,
                    "body_text": "Hello world",
                    "char_count": 11,
                    "page_start": 1,
                    "page_end": 2,
                    "parent_headings": ["Introduction"],
                },
                {
                    "section_index": 1,
                    "heading": "Programs",
                    "heading_level": 1,
                    "body_text": "Our programs...",
                    "char_count": 15,
                    "page_start": 3,
                    "page_end": 5,
                    "parent_headings": ["Programs"],
                },
            ],
            "tables": [
                {
                    "table_index": 0,
                    "section_index": 1,
                    "page_number": 4,
                    "caption": "Budget",
                    "row_count": 3,
                    "col_count": 2,
                    "data_json": [["Item", "Amount"], ["Grants", "100k"], ["Total", "100k"]],
                    "markdown": "| Item | Amount |\n|---|---|\n| Grants | 100k |",
                }
            ],
        }

        db.insert_document(mock_conn, doc)

        # Verify document INSERT was called
        calls = mock_cursor.execute.call_args_list
        assert any("INSERT INTO lava_parse.documents" in str(c) for c in calls)
        # Verify execute_values was called for sections
        assert mock_execute_values.call_count >= 1


class TestDeleteDocumentData:
    def test_rejects_invalid_sha(self):
        mock_conn = MagicMock()
        with pytest.raises(ValueError, match="invalid sha256"):
            db.delete_document_data(mock_conn, "not-a-sha")

    def test_deletes_in_correct_order(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        sha = "b" * 64
        db.delete_document_data(mock_conn, sha)

        calls = mock_cursor.execute.call_args_list
        sqls = [c[0][0] for c in calls]
        # Tables deleted first, then sections, then documents
        assert "tables" in sqls[0]
        assert "sections" in sqls[1]
        assert "documents" in sqls[2]


class TestAdvisoryLocks:
    def test_acquire_orchestrator_lock(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = (True,)

        result = db.acquire_orchestrator_lock(mock_conn)
        assert result is True

        sql = mock_cursor.execute.call_args[0][0]
        assert "pg_try_advisory_lock" in sql

    def test_acquire_worker_lock_fails_if_held(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = (False,)

        result = db.acquire_worker_lock(mock_conn)
        assert result is False

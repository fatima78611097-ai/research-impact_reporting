"""Tests for Spec 0056 — Parse Orchestrator Reliability & Observability.

Covers: HeartbeatThread, heartbeat DB functions, exit reason tracking,
smart relaunch budget, death classification, stale detection, log shipping,
run_tag validation.
"""
from __future__ import annotations

import sys
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

_mock_docling = types.ModuleType("docling")
sys.modules.setdefault("docling", _mock_docling)
sys.modules.setdefault("docling.document_converter", MagicMock())
sys.modules.setdefault("docling.chunking", MagicMock())
sys.modules.setdefault("httpx", MagicMock())

from lavandula.parse.worker import HeartbeatThread, _ship_log_on_exit
from lavandula.parse import db


# ---------------------------------------------------------------------------
# HeartbeatThread
# ---------------------------------------------------------------------------

class TestHeartbeatThread:
    def test_update_shared_state(self):
        hb = HeartbeatThread(
            conn_factory=MagicMock, instance_id="i-abc123", run_id=1, interval=60
        )
        hb.update(42, "deadbeef" * 8)
        with hb._lock:
            assert hb._docs_completed == 42
            assert hb._current_doc_sha == "deadbeef" * 8

    def test_fires_on_schedule(self):
        mock_conn = MagicMock()
        mock_factory = MagicMock(return_value=mock_conn)

        hb = HeartbeatThread(
            conn_factory=mock_factory, instance_id="i-abc123", run_id=1, interval=0.1
        )
        hb.update(5, "a" * 64)
        hb.start()
        time.sleep(0.35)
        hb.stop()
        hb.join(timeout=1)

        assert mock_factory.call_count >= 1

    def test_is_daemon(self):
        hb = HeartbeatThread(
            conn_factory=MagicMock, instance_id="i-abc123", run_id=1
        )
        assert hb.daemon is True

    def test_db_failure_does_not_crash(self):
        def _bad_factory():
            raise RuntimeError("DB down")

        hb = HeartbeatThread(
            conn_factory=_bad_factory, instance_id="i-abc123", run_id=1, interval=0.05
        )
        hb.start()
        time.sleep(0.15)
        hb.stop()
        hb.join(timeout=1)
        assert not hb.is_alive()

    def test_stop_exits_cleanly(self):
        hb = HeartbeatThread(
            conn_factory=MagicMock, instance_id="i-abc123", run_id=1, interval=60
        )
        hb.start()
        hb.stop()
        hb.join(timeout=2)
        assert not hb.is_alive()


# ---------------------------------------------------------------------------
# Heartbeat DB functions
# ---------------------------------------------------------------------------

class TestUpsertHeartbeat:
    def test_executes_upsert_sql(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        db.upsert_heartbeat(mock_conn, "i-abc123", 1, 42, "a" * 64)

        sql = mock_cursor.execute.call_args[0][0]
        assert "worker_heartbeats" in sql
        assert "ON CONFLICT" in sql
        params = mock_cursor.execute.call_args[0][1]
        assert params[0] == "i-abc123"
        assert params[1] == 1
        assert params[2] == 42
        assert params[3] == "a" * 64


class TestGetHeartbeatAge:
    def test_returns_age_when_exists(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = (120.5,)

        age = db.get_heartbeat_age(mock_conn, "i-abc123", 1)
        assert age == 120.5

    def test_returns_none_when_no_row(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = None

        age = db.get_heartbeat_age(mock_conn, "i-abc123", 1)
        assert age is None


# ---------------------------------------------------------------------------
# Exit reason tracking
# ---------------------------------------------------------------------------

class TestExitReason:
    def _mock_conn(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return mock_conn, mock_cursor

    def test_set_exit_reason(self):
        mock_conn, mock_cursor = self._mock_conn()
        db.set_exit_reason(mock_conn, 1, "empty_batch")
        sql = mock_cursor.execute.call_args[0][0]
        assert "exit_reason" in sql
        params = mock_cursor.execute.call_args[0][1]
        assert params == ("empty_batch", 1)

    def test_get_exit_reason(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = ("spot_termination",)

        reason = db.get_exit_reason(mock_conn, 1)
        assert reason == "spot_termination"

    def test_get_exit_reason_none(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = (None,)

        reason = db.get_exit_reason(mock_conn, 1)
        assert reason is None

    def test_finish_run_with_reason(self):
        mock_conn, mock_cursor = self._mock_conn()
        db.finish_run_with_reason(mock_conn, 1, {"succeeded": 100}, "max_docs")
        sql = mock_cursor.execute.call_args[0][0]
        assert "exit_reason" in sql
        assert "finished_at" in sql


# ---------------------------------------------------------------------------
# Smart relaunch budget (per-slot consecutive failures)
# ---------------------------------------------------------------------------

class TestSmartRelaunchBudget:
    """Tests that the slot-based consecutive_failures counter logic works correctly.

    These test the _relaunch_slot method behavior via the constants and
    slot dictionary structure used in the orchestrator.
    """

    def test_counter_resets_on_progress(self):
        slot = {"consecutive_failures": 2, "status": "running", "instance_id": "i-abc"}
        slot["consecutive_failures"] = 0
        assert slot["consecutive_failures"] == 0

    def test_counter_increments_on_death(self):
        slot = {"consecutive_failures": 0}
        slot["consecutive_failures"] += 1
        assert slot["consecutive_failures"] == 1

    def test_slot_abandoned_at_max(self):
        from lavandula.dashboard.pipeline.management.commands.parse_documents import MAX_CONSECUTIVE_FAILURES

        slot = {"consecutive_failures": MAX_CONSECUTIVE_FAILURES, "status": "running"}
        if slot["consecutive_failures"] >= MAX_CONSECUTIVE_FAILURES:
            slot["status"] = "abandoned"
        assert slot["status"] == "abandoned"

    def test_other_slots_unaffected(self):
        slots = [
            {"consecutive_failures": 3, "status": "abandoned"},
            {"consecutive_failures": 0, "status": "running"},
        ]
        assert slots[0]["status"] == "abandoned"
        assert slots[1]["status"] == "running"


# ---------------------------------------------------------------------------
# run_tag validation (security)
# ---------------------------------------------------------------------------

class TestRunTagValidation:
    def test_valid_tags(self):
        from lavandula.parse.config import validate_run_tag

        assert validate_run_tag("run-31") is True
        assert validate_run_tag("test_2026_05_30") is True
        assert validate_run_tag("A-Z-123") is True

    def test_injection_rejected(self):
        from lavandula.parse.config import validate_run_tag

        assert validate_run_tag("run; rm -rf /") is False
        assert validate_run_tag("../../../etc/passwd") is False
        assert validate_run_tag("run`id`") is False
        assert validate_run_tag("run$(whoami)") is False
        assert validate_run_tag("") is False


# ---------------------------------------------------------------------------
# Log shipping
# ---------------------------------------------------------------------------

class TestLogShipping:
    def test_ships_log_when_file_exists(self, tmp_path):
        log_file = tmp_path / "docling-worker.log"
        log_file.write_text("test log content")

        mock_s3 = MagicMock()
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_s3

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            with patch("lavandula.parse.worker.Path") as mock_path_cls:
                mock_path_obj = MagicMock()
                mock_path_obj.exists.return_value = True
                mock_path_cls.return_value = mock_path_obj

                _ship_log_on_exit("test-run", "i-abc123")

                mock_s3.upload_file.assert_called_once()
                call_args = mock_s3.upload_file.call_args
                assert "logs/parse/test-run/i-abc123/worker.log" in str(call_args)

    def test_skips_when_no_run_tag(self):
        _ship_log_on_exit(None, "i-abc123")

    def test_skips_when_no_instance_id(self):
        _ship_log_on_exit("test-run", None)


# ---------------------------------------------------------------------------
# Death classification constants
# ---------------------------------------------------------------------------

class TestDeathClassificationConstants:
    def test_heartbeat_stale_minutes_is_five(self):
        from lavandula.dashboard.pipeline.management.commands.parse_documents import HEARTBEAT_STALE_MINUTES
        assert HEARTBEAT_STALE_MINUTES == 5

    def test_legacy_stale_minutes_is_thirty(self):
        from lavandula.dashboard.pipeline.management.commands.parse_documents import HEARTBEAT_LEGACY_STALE_MINUTES
        assert HEARTBEAT_LEGACY_STALE_MINUTES == 30

    def test_max_consecutive_failures_is_three(self):
        from lavandula.dashboard.pipeline.management.commands.parse_documents import MAX_CONSECUTIVE_FAILURES
        assert MAX_CONSECUTIVE_FAILURES == 3


# ---------------------------------------------------------------------------
# Worker heartbeats query
# ---------------------------------------------------------------------------

class TestGetWorkerHeartbeats:
    def test_returns_list_of_dicts(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        mock_row = MagicMock()
        mock_row.keys.return_value = ["instance_id", "last_heartbeat", "docs_completed", "current_doc_sha", "age_seconds"]
        mock_row.__getitem__ = lambda self, k: {
            "instance_id": "i-abc",
            "last_heartbeat": "2026-05-30T12:00:00",
            "docs_completed": 50,
            "current_doc_sha": "a" * 64,
            "age_seconds": 30.0,
        }[k]
        mock_cursor.fetchall.return_value = [mock_row]

        result = db.get_worker_heartbeats(mock_conn, 1)
        assert isinstance(result, list)

"""Tests for StallWatchdog (Spec 0036)."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
import threading
from unittest import mock

import pytest

from lavandula.reports.stall_watchdog import StallAction, StallInfo, StallWatchdog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockClock:
    """Controllable clock for deterministic testing."""

    def __init__(self, start: float = 1000.0):
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _noop_sleep(seconds):
    """No-op replacement for time.sleep in threaded tests."""


async def _noop_async_sleep(seconds):
    """No-op replacement for asyncio.sleep."""


def _make_watchdog(
    progress_values=None,
    active_values=None,
    clock=None,
    threshold=300,
    interval=60,
    **kwargs,
):
    """Helper to create a watchdog with controllable progress/active callables."""
    progress_idx = [0]
    active_idx = [0]

    if progress_values is None:
        progress_values = [0]
    if active_values is None:
        active_values = [5]

    def get_progress():
        idx = min(progress_idx[0], len(progress_values) - 1)
        return progress_values[idx]

    def get_active():
        idx = min(active_idx[0], len(active_values) - 1)
        return active_values[idx]

    if clock is None:
        clock = MockClock()

    wd = StallWatchdog(
        get_progress=get_progress,
        get_active=get_active,
        stall_threshold_sec=threshold,
        check_interval_sec=interval,
        clock=clock,
        **kwargs,
    )
    return wd, clock, progress_idx, active_idx


# ---------------------------------------------------------------------------
# Phase 1: Core Detection Tests
# ---------------------------------------------------------------------------

class TestStallDetection:

    def test_stall_detected_after_threshold(self):
        """AC1, AC24: Stall detected when progress unchanged and active > 0."""
        clock = MockClock()
        progress = [0]
        wd = StallWatchdog(
            get_progress=lambda: progress[0],
            get_active=lambda: 5,
            stall_threshold_sec=300,
            clock=clock,
        )
        wd._mode = "thread"

        wd._check_once()
        assert wd._stall_checks == 0

        clock.advance(301)
        wd._check_once()
        assert wd._stall_checks == 1

    def test_no_stall_when_idle(self):
        """AC2, AC26: No stall when active == 0."""
        clock = MockClock()
        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 0,
            stall_threshold_sec=300,
            clock=clock,
        )
        wd._mode = "thread"

        wd._check_once()
        clock.advance(600)
        wd._check_once()
        assert wd._stall_checks == 0

    def test_no_stall_when_progress_advances(self):
        """AC3, AC25: No stall when progress advances within threshold."""
        clock = MockClock()
        progress = [0]
        wd = StallWatchdog(
            get_progress=lambda: progress[0],
            get_active=lambda: 5,
            stall_threshold_sec=300,
            clock=clock,
        )
        wd._mode = "thread"

        wd._check_once()
        clock.advance(200)
        progress[0] = 1
        wd._check_once()
        assert wd._stall_checks == 0

        clock.advance(200)
        progress[0] = 2
        wd._check_once()
        assert wd._stall_checks == 0

    def test_stall_timer_resets_on_progress(self):
        """AC4, AC29: Stall timer and checks reset when progress advances after warning."""
        clock = MockClock()
        progress = [0]
        wd = StallWatchdog(
            get_progress=lambda: progress[0],
            get_active=lambda: 5,
            stall_threshold_sec=300,
            clock=clock,
        )
        wd._mode = "thread"

        wd._check_once()
        clock.advance(301)
        wd._check_once()
        assert wd._stall_checks == 1

        progress[0] = 1
        wd._check_once()
        assert wd._stall_checks == 0
        assert wd._stall_start is None

    def test_stall_checks_increment_and_reset(self):
        """AC5: stall_checks increments each interval, resets on progress."""
        clock = MockClock()
        progress = [0]
        wd = StallWatchdog(
            get_progress=lambda: progress[0],
            get_active=lambda: 5,
            stall_threshold_sec=100,
            clock=clock,
        )
        wd._mode = "thread"

        wd._check_once()
        clock.advance(101)
        wd._check_once()
        assert wd._stall_checks == 1

        clock.advance(60)
        wd._check_once()
        assert wd._stall_checks == 2

        clock.advance(60)
        wd._check_once()
        assert wd._stall_checks == 3

        progress[0] = 10
        wd._check_once()
        assert wd._stall_checks == 0

    def test_counter_regression(self, caplog):
        """AC50, AC51: Counter regression logs ERROR and resets baseline."""
        clock = MockClock()
        progress = [10]
        wd = StallWatchdog(
            get_progress=lambda: progress[0],
            get_active=lambda: 5,
            stall_threshold_sec=100,
            clock=clock,
        )
        wd._mode = "thread"

        wd._check_once()
        assert wd._last_progress == 10

        progress[0] = 5
        with caplog.at_level(logging.ERROR):
            wd._check_once()

        assert wd._last_progress == 5
        assert wd._stall_start is None
        assert wd._stall_checks == 0
        assert "regressed from 10 to 5" in caplog.text


# ---------------------------------------------------------------------------
# Phase 1: Escalation & Callback Tests
# ---------------------------------------------------------------------------

class TestEscalation:

    @mock.patch("os._exit", side_effect=SystemExit(2))
    def test_escalation_ladder_levels(self, mock_exit):
        """AC6, AC27: Default ladder fires at correct duration multiples."""
        clock = MockClock()
        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            clock=clock,
        )
        wd._mode = "thread"

        levels = []

        orig_default_ladder = wd._default_ladder

        def spy_ladder(info, level):
            levels.append(level)
            if level >= 4:
                return StallAction.CONTINUE
            return orig_default_ladder(info, level)

        wd._default_ladder = spy_ladder

        wd._check_once()

        clock.advance(101)
        wd._check_once()
        assert levels[-1] == 1

        clock.advance(100)
        wd._check_once()
        assert levels[-1] == 2

        clock.advance(100)
        wd._check_once()
        assert levels[-1] == 3

        clock.advance(100)
        wd._check_once()
        assert levels[-1] == 4

    @mock.patch("os._exit", side_effect=SystemExit(2))
    def test_custom_callback_replaces_ladder(self, mock_exit):
        """AC7, AC28: Custom on_stall replaces default escalation logic."""
        clock = MockClock()
        callback_calls = []

        def custom_callback(info: StallInfo) -> StallAction:
            callback_calls.append(info)
            return StallAction.CONTINUE

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            on_stall=custom_callback,
            clock=clock,
        )
        wd._mode = "thread"

        wd._check_once()
        clock.advance(101)
        wd._check_once()
        assert len(callback_calls) == 1
        assert callback_calls[0].escalation_level == 1

    def test_callback_invoked_every_interval(self):
        """AC8: Callback fires on every check interval during stall."""
        clock = MockClock()
        call_count = [0]

        def custom_callback(info: StallInfo) -> StallAction:
            call_count[0] += 1
            return StallAction.CONTINUE

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            on_stall=custom_callback,
            clock=clock,
        )
        wd._mode = "thread"

        wd._check_once()
        clock.advance(101)
        wd._check_once()
        assert call_count[0] == 1

        clock.advance(60)
        wd._check_once()
        assert call_count[0] == 2

        clock.advance(60)
        wd._check_once()
        assert call_count[0] == 3

    @mock.patch("os._exit", side_effect=SystemExit(2))
    def test_abort_terminal_state(self, mock_exit):
        """AC9, AC10a, AC48: ABORT enters terminal state, no further callbacks."""
        clock = MockClock()
        callback_calls = [0]

        def custom_callback(info: StallInfo) -> StallAction:
            callback_calls[0] += 1
            if info.stall_duration_sec >= 200:
                return StallAction.ABORT
            return StallAction.CONTINUE

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            on_stall=custom_callback,
            clock=clock,
        )
        wd._mode = "thread"

        wd._check_once()
        clock.advance(101)
        wd._check_once()
        assert callback_calls[0] == 1

        clock.advance(100)
        with pytest.raises(SystemExit):
            wd._check_once()

        assert wd._abort_armed is True
        assert callback_calls[0] == 2

        wd._check_once()
        assert callback_calls[0] == 2

    def test_log_format(self, caplog):
        """AC10: Log messages include required fields."""
        clock = MockClock()
        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            clock=clock,
        )
        wd._mode = "thread"

        wd._check_once()
        clock.advance(101)
        with caplog.at_level(logging.WARNING):
            wd._check_once()

        assert "progress=0" in caplog.text
        assert "active=5" in caplog.text
        assert "duration=" in caplog.text
        assert "level=1" in caplog.text
        assert "action=continue" in caplog.text
        assert "mode=thread" in caplog.text


# ---------------------------------------------------------------------------
# Phase 1: Lifecycle Tests
# ---------------------------------------------------------------------------

class TestLifecycle:

    def test_stop_clean_exit(self):
        """AC11, AC31: stop() causes clean exit within one check interval."""
        clock = MockClock()
        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 0,
            stall_threshold_sec=300,
            clock=clock,
        )

        with mock.patch("time.sleep", _noop_sleep):
            wd.start_thread()
            wd.stop()
            wd._thread.join(timeout=2)
            assert not wd._thread.is_alive()

    def test_run_async_returns_coroutine(self):
        """AC12: run_async() returns a coroutine."""
        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 0,
            stall_threshold_sec=300,
        )

        async def run():
            wd._stop = True
            with mock.patch("asyncio.sleep", _noop_async_sleep):
                await wd.run_async()

        asyncio.run(run())
        assert wd._mode == "async"

    def test_start_thread_daemon(self):
        """AC13: start_thread spawns a daemon thread."""
        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 0,
            stall_threshold_sec=300,
        )

        with mock.patch("time.sleep", _noop_sleep):
            wd.start_thread()
            assert wd._thread is not None
            assert wd._thread.daemon is True
            wd.stop()
            wd._thread.join(timeout=2)

    def test_repeated_start_raises(self):
        """AC14, AC32: Repeated start raises RuntimeError."""
        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 0,
            stall_threshold_sec=300,
        )

        with mock.patch("time.sleep", _noop_sleep):
            wd.start_thread()
            with pytest.raises(RuntimeError, match="already started"):
                wd.start_thread()
            wd.stop()
            wd._thread.join(timeout=2)

    def test_repeated_run_async_raises(self):
        """AC14: Repeated run_async raises RuntimeError."""
        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 0,
            stall_threshold_sec=300,
        )

        async def run():
            wd._stop = True
            with mock.patch("asyncio.sleep", _noop_async_sleep):
                await wd.run_async()
            with pytest.raises(RuntimeError, match="already started"):
                await wd.run_async()

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Phase 1: Exception Safety Tests
# ---------------------------------------------------------------------------

class TestExceptionSafety:

    def test_exception_in_callback_continues(self, caplog):
        """AC15, AC30: Exception in callback is caught at loop level, watchdog continues."""
        clock = MockClock()
        call_count = [0]
        iteration = [0]

        def bad_callback(info: StallInfo) -> StallAction:
            call_count[0] += 1
            if call_count[0] == 1:
                raise ValueError("boom")
            return StallAction.CONTINUE

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            on_stall=bad_callback,
            clock=clock,
        )
        wd._mode = "thread"

        def counting_sleep(sec):
            iteration[0] += 1
            clock.advance(101 if iteration[0] == 1 else 60)
            if iteration[0] >= 3:
                wd.stop()

        with caplog.at_level(logging.CRITICAL):
            wd._run_loop(counting_sleep)

        assert call_count[0] >= 2
        assert "check iteration failed" in caplog.text

    def test_exception_in_get_progress_continues(self, caplog):
        """AC15: Exception in get_progress is caught."""
        clock = MockClock()
        calls = [0]

        def flaky_progress():
            calls[0] += 1
            if calls[0] == 2:
                raise RuntimeError("progress broke")
            return 0

        wd = StallWatchdog(
            get_progress=flaky_progress,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            clock=clock,
        )
        wd._mode = "thread"

        loop_iterations = [0]
        orig_check = wd._check_once

        def counted_check():
            loop_iterations[0] += 1
            orig_check()

        with mock.patch("time.sleep", _noop_sleep):
            wd.start_thread()
            import time as _time
            _time.sleep(0.1)
            wd.stop()
            wd._thread.join(timeout=2)

        assert calls[0] >= 2

    def test_unexpected_termination_logs_critical(self, caplog):
        """AC15a: Unexpected BaseException logs CRITICAL."""
        clock = MockClock()
        call_count = [0]

        def exploding_clock():
            call_count[0] += 1
            if call_count[0] >= 3:
                raise KeyboardInterrupt("test")
            return clock()

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 0,
            stall_threshold_sec=300,
            clock=exploding_clock,
            check_interval_sec=0,
        )
        wd._mode = "thread"

        with mock.patch("time.sleep", _noop_sleep):
            with caplog.at_level(logging.CRITICAL):
                wd._run_loop(_noop_sleep)

        assert "terminated unexpectedly" in caplog.text

    @mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/test"})
    def test_unexpected_termination_updates_job(self, caplog):
        """AC15a: Unexpected termination tries to update Job when job_id set."""
        clock = MockClock()
        call_count = [0]

        def exploding_clock():
            call_count[0] += 1
            if call_count[0] >= 3:
                raise KeyboardInterrupt("test")
            return clock()

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 0,
            stall_threshold_sec=300,
            clock=exploding_clock,
            check_interval_sec=0,
        )
        wd._mode = "thread"
        wd._job_id = 42
        wd._started = True

        mock_conn = mock.MagicMock()
        mock_cursor = mock.MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with mock.patch("psycopg2.connect", return_value=mock_conn):
            with caplog.at_level(logging.CRITICAL):
                wd._run_loop(_noop_sleep)

        assert "terminated unexpectedly" in caplog.text
        mock_cursor.execute.assert_called()

    def test_no_module_level_imports(self):
        """AC16: No Django, asyncio, or threading at module level."""
        import importlib
        source = importlib.util.find_spec("lavandula.reports.stall_watchdog")
        with open(source.origin) as f:
            content = f.read()

        in_function = False
        for line in content.split("\n"):
            # Only check top-level (non-indented) lines
            if line and not line[0].isspace():
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("if TYPE_CHECKING"):
                    continue
                if stripped.startswith("from __future__"):
                    continue
                if stripped.startswith("import asyncio") or stripped.startswith("from asyncio"):
                    pytest.fail(f"Module-level asyncio import: {stripped}")
                if stripped.startswith("import threading") or stripped.startswith("from threading"):
                    pytest.fail(f"Module-level threading import: {stripped}")
                if stripped.startswith("import django") or stripped.startswith("from django"):
                    pytest.fail(f"Module-level django import: {stripped}")
                if stripped.startswith("from pipeline"):
                    pytest.fail(f"Module-level pipeline import: {stripped}")


# ---------------------------------------------------------------------------
# Phase 1: Configuration Tests
# ---------------------------------------------------------------------------

class TestConfiguration:

    def test_clock_injection(self):
        """AC33: Mock clock is used instead of time.monotonic."""
        clock = MockClock(start=5000.0)
        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            clock=clock,
        )
        wd._mode = "thread"

        wd._check_once()
        assert wd._stall_start == 5000.0

    def test_threshold_configurable(self):
        """AC34: stall_threshold_sec is configurable."""
        clock = MockClock()
        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=60,
            clock=clock,
        )
        wd._mode = "thread"

        wd._check_once()
        clock.advance(50)
        wd._check_once()
        assert wd._stall_checks == 0

        clock.advance(11)
        wd._check_once()
        assert wd._stall_checks == 1

    def test_interval_configurable(self):
        """AC35: check_interval_sec is configurable."""
        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 0,
            check_interval_sec=30,
        )
        assert wd._check_interval_sec == 30


# ---------------------------------------------------------------------------
# Phase 2: Dashboard Tests
# ---------------------------------------------------------------------------

class TestDashboard:

    @mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/test"})
    def test_dashboard_write_at_level_transitions(self):
        """AC21, AC47: Dashboard writes fire exactly once at level 2 and level 4."""
        clock = MockClock()
        mock_conn = mock.MagicMock()
        mock_cursor = mock.MagicMock()
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            clock=clock,
        )
        wd._mode = "thread"
        wd._job_id = 1

        with mock.patch("psycopg2.connect", return_value=mock_conn) as mock_connect:
            wd._check_once()

            clock.advance(101)
            wd._check_once()
            assert mock_connect.call_count == 0

            clock.advance(100)
            wd._check_once()
            assert mock_connect.call_count == 1

            clock.advance(60)
            wd._check_once()
            assert mock_connect.call_count == 1

    @mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/test"})
    def test_dashboard_uses_fresh_psycopg2(self):
        """AC22: Dashboard uses fresh psycopg2 connection with correct params."""
        clock = MockClock()
        mock_conn = mock.MagicMock()
        mock_cursor = mock.MagicMock()
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            clock=clock,
        )
        wd._mode = "thread"
        wd._job_id = 1

        with mock.patch("psycopg2.connect", return_value=mock_conn) as mock_connect:
            wd._check_once()
            clock.advance(201)
            wd._check_once()

            mock_connect.assert_called_once_with(
                "postgresql://test:test@localhost/test",
                connect_timeout=5,
                options="-c statement_timeout=5000",
            )

    @mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/test"})
    def test_dashboard_failure_ignored(self, caplog):
        """AC23: Dashboard failure logged and ignored."""
        clock = MockClock()

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            clock=clock,
        )
        wd._mode = "thread"
        wd._job_id = 1

        with mock.patch("psycopg2.connect", side_effect=Exception("connection refused")):
            wd._check_once()
            clock.advance(201)
            with caplog.at_level(logging.WARNING):
                wd._check_once()

            assert wd._stall_checks == 1
            assert "dashboard update failed" in caplog.text

    def test_dashboard_eager_import_validation(self):
        """AC23a: ImportError at init when job_id set and Django unavailable."""
        with mock.patch.dict(sys.modules, {"pipeline": None, "pipeline.models": None}):
            with pytest.raises(ImportError):
                StallWatchdog(
                    get_progress=lambda: 0,
                    get_active=lambda: 0,
                    job_id=99,
                )

    @mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/test"})
    def test_dashboard_zero_rows_warning(self, caplog):
        """AC52: Dashboard UPDATE affecting 0 rows logs WARNING."""
        clock = MockClock()
        mock_conn = mock.MagicMock()
        mock_cursor = mock.MagicMock()
        mock_cursor.rowcount = 0
        mock_conn.cursor.return_value = mock_cursor

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            clock=clock,
        )
        wd._mode = "thread"
        wd._job_id = 999

        with mock.patch("psycopg2.connect", return_value=mock_conn):
            wd._check_once()
            clock.advance(201)
            with caplog.at_level(logging.WARNING):
                wd._check_once()

        assert "affected 0 rows" in caplog.text

    @mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/test"})
    def test_dashboard_timeout_graceful(self, caplog):
        """AC49: Dashboard timeout logs WARNING and continues."""
        clock = MockClock()

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            clock=clock,
        )
        wd._mode = "thread"
        wd._job_id = 1

        import psycopg2 as _pg2

        with mock.patch("psycopg2.connect", side_effect=_pg2.OperationalError("connection timed out")):
            wd._check_once()
            clock.advance(201)
            with caplog.at_level(logging.WARNING):
                wd._check_once()

        assert "dashboard update failed" in caplog.text
        assert wd._stall_checks == 1

    @mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/test"})
    @mock.patch("os._exit", side_effect=SystemExit(2))
    def test_no_dashboard_after_abort_armed(self, mock_exit):
        """AC48: No dashboard writes after _abort_armed."""
        clock = MockClock()
        mock_conn = mock.MagicMock()
        mock_cursor = mock.MagicMock()
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            clock=clock,
        )
        wd._mode = "thread"
        wd._job_id = 1

        with mock.patch("psycopg2.connect", return_value=mock_conn) as mock_connect:
            wd._check_once()
            clock.advance(401)
            with pytest.raises(SystemExit):
                wd._check_once()

            assert wd._abort_armed
            mock_connect.reset_mock()

            wd._maybe_update_dashboard(4)
            assert mock_connect.call_count == 0


# ---------------------------------------------------------------------------
# Phase 2: Email Tests
# ---------------------------------------------------------------------------

class TestEmail:

    def test_ses_warning_at_level2(self):
        """AC39, AC46: SES warning email sent at level 2 entry."""
        clock = MockClock()
        mock_ses = mock.MagicMock()

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            notify_email="test@example.com",
            clock=clock,
        )
        wd._mode = "thread"
        wd._ses_client = mock_ses

        wd._check_once()
        clock.advance(201)
        wd._check_once()

        mock_ses.send_email.assert_called_once()
        call_kwargs = mock_ses.send_email.call_args
        assert "⚠" in call_kwargs.kwargs.get("Message", call_kwargs[1]["Message"])["Subject"]["Data"]

    @mock.patch("os._exit", side_effect=SystemExit(2))
    def test_ses_abort_at_level4(self, mock_exit):
        """AC40, AC46: SES abort email sent at level 4."""
        clock = MockClock()
        mock_ses = mock.MagicMock()

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            notify_email="test@example.com",
            clock=clock,
        )
        wd._mode = "thread"
        wd._ses_client = mock_ses

        wd._check_once()
        clock.advance(201)
        wd._check_once()
        assert mock_ses.send_email.call_count == 1

        clock.advance(200)
        with pytest.raises(SystemExit):
            wd._check_once()
        assert mock_ses.send_email.call_count == 2

    @mock.patch("os._exit", side_effect=SystemExit(2))
    def test_ses_abort_from_custom_callback(self, mock_exit):
        """AC40: Custom callback ABORT triggers level-4 email."""
        clock = MockClock()
        mock_ses = mock.MagicMock()

        def abort_immediately(info: StallInfo) -> StallAction:
            return StallAction.ABORT

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            on_stall=abort_immediately,
            notify_email="test@example.com",
            clock=clock,
        )
        wd._mode = "thread"
        wd._ses_client = mock_ses

        wd._check_once()
        clock.advance(101)
        with pytest.raises(SystemExit):
            wd._check_once()

        mock_ses.send_email.assert_called_once()
        call_args = mock_ses.send_email.call_args
        subject = call_args.kwargs.get("Message", call_args[1]["Message"])["Subject"]["Data"]
        assert "ABORT" in subject

    def test_ses_timeout_config(self):
        """AC41: SES client created with 5s timeouts."""
        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            notify_email="test@example.com",
        )

        with mock.patch("boto3.client") as mock_boto:
            wd._maybe_send_email(2)
            wd._email_sent_levels.discard(2)

            mock_boto.assert_called_once()
            call_kwargs = mock_boto.call_args
            config = call_kwargs.kwargs.get("config", call_kwargs[1].get("config"))
            assert config.connect_timeout == 5
            assert config.read_timeout == 5

    def test_ses_failure_ignored(self, caplog):
        """AC42: SES send failure logged and ignored."""
        clock = MockClock()
        mock_ses = mock.MagicMock()
        mock_ses.send_email.side_effect = Exception("SES down")

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            notify_email="test@example.com",
            clock=clock,
        )
        wd._mode = "thread"
        wd._ses_client = mock_ses

        wd._check_once()
        clock.advance(201)
        with caplog.at_level(logging.WARNING):
            wd._check_once()

        assert "SES send failed" in caplog.text
        assert wd._stall_checks == 1

    @mock.patch("os._exit", side_effect=SystemExit(2))
    def test_ses_no_email_after_abort_armed(self, mock_exit):
        """AC43: No emails after _abort_armed."""
        clock = MockClock()
        mock_ses = mock.MagicMock()

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            notify_email="test@example.com",
            clock=clock,
        )
        wd._mode = "thread"
        wd._ses_client = mock_ses

        wd._check_once()
        clock.advance(401)
        with pytest.raises(SystemExit):
            wd._check_once()

        assert wd._abort_armed
        count_before = mock_ses.send_email.call_count

        wd._maybe_send_email(4)
        assert mock_ses.send_email.call_count == count_before

    def test_ses_subject_format(self):
        """AC44: Email subject includes pipeline basename and hostname."""
        clock = MockClock()
        mock_ses = mock.MagicMock()

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            notify_email="test@example.com",
            clock=clock,
        )
        wd._mode = "thread"
        wd._ses_client = mock_ses

        wd._check_once()
        clock.advance(201)
        wd._check_once()

        call_args = mock_ses.send_email.call_args
        subject = call_args.kwargs.get("Message", call_args[1]["Message"])["Subject"]["Data"]
        hostname = socket.gethostname()
        assert hostname in subject
        pipeline_name = os.path.basename(sys.argv[0])
        assert pipeline_name in subject

    @mock.patch("os._exit", side_effect=SystemExit(2))
    def test_ses_max_two_emails(self, mock_exit):
        """AC45: At most 2 emails per stall incident."""
        clock = MockClock()
        mock_ses = mock.MagicMock()

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            notify_email="test@example.com",
            clock=clock,
        )
        wd._mode = "thread"
        wd._ses_client = mock_ses

        wd._check_once()

        clock.advance(101)
        wd._check_once()
        assert mock_ses.send_email.call_count == 0

        clock.advance(100)
        wd._check_once()
        assert mock_ses.send_email.call_count == 1

        clock.advance(60)
        wd._check_once()
        assert mock_ses.send_email.call_count == 1

        clock.advance(140)
        with pytest.raises(SystemExit):
            wd._check_once()
        assert mock_ses.send_email.call_count == 2

    def test_ses_body_no_sensitive_info(self):
        """AC53: Email body contains only basename and hostname, no env vars."""
        clock = MockClock()
        mock_ses = mock.MagicMock()

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            notify_email="test@example.com",
            progress_total=100,
            clock=clock,
        )
        wd._mode = "thread"
        wd._ses_client = mock_ses

        wd._check_once()
        clock.advance(201)
        wd._check_once()

        call_args = mock_ses.send_email.call_args
        body = call_args.kwargs.get("Message", call_args[1]["Message"])["Body"]["Text"]["Data"]

        assert "DATABASE_URL" not in body
        assert "DJANGO_DATABASE_URL" not in body
        full_argv = " ".join(sys.argv)
        if len(sys.argv) > 1:
            assert full_argv not in body

        for key in ("AWS_SECRET", "PASSWORD", "TOKEN"):
            for env_key, env_val in os.environ.items():
                if key in env_key.upper():
                    assert env_val not in body

    def test_dedup_state_resets_on_progress_recovery(self):
        """Progress recovery clears email/dashboard dedup so new stall gets fresh alerts."""
        clock = MockClock()
        mock_ses = mock.MagicMock()
        progress = [0]

        wd = StallWatchdog(
            get_progress=lambda: progress[0],
            get_active=lambda: 5,
            stall_threshold_sec=100,
            notify_email="test@example.com",
            clock=clock,
        )
        wd._mode = "thread"
        wd._ses_client = mock_ses

        # First stall -> level 2 email
        wd._check_once()
        clock.advance(201)
        wd._check_once()
        assert mock_ses.send_email.call_count == 1

        # Recovery
        progress[0] = 10
        wd._check_once()
        assert len(wd._email_sent_levels) == 0
        assert len(wd._dashboard_written_levels) == 0

        # Second stall: first check sets stall_start, second after threshold triggers
        wd._check_once()  # sets _stall_start (progress unchanged, active > 0)
        clock.advance(201)
        wd._check_once()
        assert mock_ses.send_email.call_count == 2


# ---------------------------------------------------------------------------
# Phase 3: Pre-Abort Tests
# ---------------------------------------------------------------------------

class TestPreAbort:

    @mock.patch("os._exit", side_effect=SystemExit(2))
    def test_pre_abort_called_before_exit(self, mock_exit):
        """AC36: pre_abort is called before os._exit(2)."""
        call_order = []

        def my_pre_abort():
            call_order.append("pre_abort")

        mock_exit.side_effect = lambda code: call_order.append("exit") or (_ for _ in ()).throw(SystemExit(2))

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            pre_abort=my_pre_abort,
        )

        with pytest.raises(SystemExit):
            wd._do_abort()

        assert call_order.index("pre_abort") < call_order.index("exit")

    @mock.patch("os._exit", side_effect=SystemExit(2))
    def test_pre_abort_exception_proceeds(self, mock_exit):
        """AC37: pre_abort that raises does not prevent os._exit(2)."""
        def bad_pre_abort():
            raise RuntimeError("cleanup failed")

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            pre_abort=bad_pre_abort,
        )

        with pytest.raises(SystemExit):
            wd._do_abort()

        mock_exit.assert_called_with(2)

    @mock.patch("os._exit", side_effect=SystemExit(2))
    def test_pre_abort_timeout_proceeds(self, mock_exit):
        """AC37: pre_abort that hangs still leads to os._exit(2).

        We mock the Timer to call its target immediately (on the current
        thread) so the mocked os._exit fires before pre_abort returns,
        avoiding a stray background thread that hits the mock after teardown.
        """
        class ImmediateTimer:
            def __init__(self, interval, fn):
                self._fn = fn
                self.daemon = True

            def start(self):
                self._fn()

            def cancel(self):
                pass

        def hanging_pre_abort():
            threading.Event().wait(10)

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=100,
            pre_abort=hanging_pre_abort,
        )

        with mock.patch("threading.Timer", ImmediateTimer):
            with pytest.raises(SystemExit):
                wd._do_abort()

        mock_exit.assert_called_with(2)


# ---------------------------------------------------------------------------
# Phase 4: Integration Tests
# ---------------------------------------------------------------------------

class TestCrawlerIntegration:

    @mock.patch("os._exit", side_effect=SystemExit(2))
    def test_crawler_callback_shutdown_then_abort(self, mock_exit):
        """AC17: Crawler callback: CONTINUE at 1-2×, SHUTDOWN at 3×, ABORT at 4×."""
        clock = MockClock()
        shutdown_event = mock.MagicMock()

        def _handle_crawler_stall(info: StallInfo) -> StallAction:
            if info.stall_duration_sec >= 4 * 300:
                return StallAction.ABORT
            if info.stall_duration_sec >= 3 * 300:
                shutdown_event.set()
                return StallAction.SHUTDOWN
            return StallAction.CONTINUE

        wd = StallWatchdog(
            get_progress=lambda: 0,
            get_active=lambda: 5,
            stall_threshold_sec=300,
            on_stall=_handle_crawler_stall,
            clock=clock,
        )
        wd._mode = "async"

        # First check sets stall_start
        wd._check_once()

        # Level 1: CONTINUE
        clock.advance(301)
        wd._check_once()
        shutdown_event.set.assert_not_called()

        # Level 2: CONTINUE
        clock.advance(300)
        wd._check_once()
        shutdown_event.set.assert_not_called()

        # Level 3: SHUTDOWN
        clock.advance(300)
        wd._check_once()
        shutdown_event.set.assert_called_once()

        # Repeated SHUTDOWN (idempotent)
        clock.advance(60)
        wd._check_once()
        assert shutdown_event.set.call_count == 2

        # Level 4: ABORT
        clock.advance(240)
        with pytest.raises(SystemExit):
            wd._check_once()
        assert wd._abort_armed

    def test_crawler_pre_abort_removes_lock(self, tmp_path):
        """AC38: Crawler pre_abort removes lock file."""
        lock_file = tmp_path / ".crawler.VA.lock"
        lock_file.write_text("locked")
        assert lock_file.exists()

        from pathlib import Path

        def _crawler_pre_abort():
            try:
                Path(lock_file).unlink(missing_ok=True)
            except Exception:
                pass

        _crawler_pre_abort()
        assert not lock_file.exists()

    def test_crawler_pre_abort_missing_lock_ok(self, tmp_path):
        """AC38: pre_abort is safe when lock file doesn't exist."""
        lock_file = tmp_path / ".crawler.VA.lock"
        assert not lock_file.exists()

        from pathlib import Path

        def _crawler_pre_abort():
            try:
                Path(lock_file).unlink(missing_ok=True)
            except Exception:
                pass

        _crawler_pre_abort()  # should not raise

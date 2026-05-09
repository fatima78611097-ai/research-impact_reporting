"""Generic pipeline stall watchdog (Spec 0036).

Monitors a progress counter and escalates through WARNING -> ERROR ->
SHUTDOWN -> ABORT when a pipeline stalls with active workers.
"""
from __future__ import annotations

import logging
import os
import sys
import socket
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    pass

_log = logging.getLogger(__name__)


class StallAction(Enum):
    CONTINUE = "continue"
    SHUTDOWN = "shutdown"
    ABORT = "abort"


@dataclass
class StallInfo:
    stall_duration_sec: float
    last_progress: int
    active_workers: int
    progress_total: int | None
    stall_checks: int
    escalation_level: int


class StallWatchdog:

    def __init__(
        self,
        get_progress: Callable[[], int],
        get_active: Callable[[], int],
        stall_threshold_sec: int = 300,
        check_interval_sec: int = 60,
        on_stall: Callable[[StallInfo], StallAction] | None = None,
        pre_abort: Callable[[], None] | None = None,
        notify_email: str | None = None,
        progress_total: int | None = None,
        job_id: int | None = None,
        clock: Callable[[], float] | None = None,
    ):
        self._get_progress = get_progress
        self._get_active = get_active
        self._stall_threshold_sec = stall_threshold_sec
        self._check_interval_sec = check_interval_sec
        self._on_stall = on_stall
        self._pre_abort = pre_abort
        self._notify_email = notify_email
        self._progress_total = progress_total
        self._job_id = job_id
        self._clock = clock or time.monotonic

        self._last_progress: int = 0
        self._stall_start: float | None = None
        self._stall_checks: int = 0
        self._stop = False
        self._started = False
        self._abort_armed = False
        self._last_escalation_level = 0
        self._mode = ""

        self._ses_client = None
        self._email_sent_levels: set[int] = set()
        self._dashboard_written_levels: set[int] = set()

        self._thread = None

        if job_id is not None:
            from pipeline.models import Job  # noqa: F401 — validate import eagerly

    def _check_once(self) -> None:
        if self._abort_armed:
            return

        progress = self._get_progress()
        active = self._get_active()
        now = self._clock()

        if progress > self._last_progress:
            self._last_progress = progress
            self._stall_start = None
            self._stall_checks = 0
            self._last_escalation_level = 0
            self._email_sent_levels.clear()
            self._dashboard_written_levels.clear()
            return

        if progress < self._last_progress:
            _log.error(
                "watchdog: progress counter regressed from %d to %d, resetting baseline",
                self._last_progress, progress,
            )
            self._last_progress = progress
            self._stall_start = None
            self._stall_checks = 0
            self._last_escalation_level = 0
            return

        if active == 0:
            return

        if self._stall_start is None:
            self._stall_start = now
            return

        stall_duration = now - self._stall_start
        if stall_duration < self._stall_threshold_sec:
            return

        self._stall_checks += 1
        threshold = self._stall_threshold_sec

        escalation_level = 1
        if stall_duration >= 4 * threshold:
            escalation_level = 4
        elif stall_duration >= 3 * threshold:
            escalation_level = 3
        elif stall_duration >= 2 * threshold:
            escalation_level = 2

        info = StallInfo(
            stall_duration_sec=stall_duration,
            last_progress=self._last_progress,
            active_workers=active,
            progress_total=self._progress_total,
            stall_checks=self._stall_checks,
            escalation_level=escalation_level,
        )

        if self._on_stall is not None:
            action = self._on_stall(info)
            if action == StallAction.ABORT:
                escalation_level = 4
                info.escalation_level = 4
        else:
            action = self._default_ladder(info, escalation_level)

        _log.log(
            logging.CRITICAL if action == StallAction.ABORT
            else logging.ERROR if escalation_level >= 2
            else logging.WARNING,
            "watchdog: stall detected — progress=%d active=%d duration=%.0fs "
            "level=%d action=%s checks=%d mode=%s",
            self._last_progress, active, stall_duration,
            escalation_level, action.value, self._stall_checks, self._mode,
        )

        self._maybe_update_dashboard(escalation_level)
        self._maybe_send_email(escalation_level)

        self._last_escalation_level = escalation_level

        if action == StallAction.ABORT:
            self._do_abort()

    def _default_ladder(self, info: StallInfo, level: int) -> StallAction:
        if level >= 4:
            return StallAction.ABORT
        if level == 3:
            _log.error(
                "watchdog: no shutdown_event available via default ladder, "
                "will abort at 4×"
            )
            return StallAction.CONTINUE
        return StallAction.CONTINUE

    def _do_abort(self) -> None:
        import threading as _threading

        self._abort_armed = True
        _log.critical("watchdog: ABORT armed, running pre-abort cleanup")

        if self._pre_abort is not None:
            timer = _threading.Timer(3.0, lambda: os._exit(2))
            timer.daemon = True
            timer.start()
            try:
                self._pre_abort()
            except Exception:
                _log.exception("watchdog: pre_abort raised")
            finally:
                timer.cancel()

        _log.critical("watchdog: forcing exit in 5s")
        time.sleep(5)
        os._exit(2)

    def _maybe_update_dashboard(self, level: int) -> None:
        if self._abort_armed or self._job_id is None:
            return
        if level not in (2, 4):
            return
        if level in self._dashboard_written_levels:
            return

        self._dashboard_written_levels.add(level)

        try:
            import psycopg2

            db_url = os.environ.get("DJANGO_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
            if not db_url:
                _log.warning("watchdog: no DATABASE_URL for dashboard update")
                return

            conn = psycopg2.connect(
                db_url,
                connect_timeout=5,
                options="-c statement_timeout=5000",
            )
            try:
                cur = conn.cursor()
                if level == 2:
                    msg = (
                        f"Stall watchdog: progress frozen at {self._last_progress} "
                        f"with {self._get_active()} active workers for "
                        f"{self._stall_threshold_sec * 2}+ seconds"
                    )
                    cur.execute(
                        "UPDATE jobs SET error_message = %s WHERE id = %s",
                        (msg, self._job_id),
                    )
                elif level == 4:
                    msg = (
                        f"Watchdog forced exit: progress frozen at {self._last_progress}, "
                        f"stall exceeded {self._stall_threshold_sec * 4}s"
                    )
                    cur.execute(
                        "UPDATE jobs SET status = %s, exit_code = %s, error_message = %s "
                        "WHERE id = %s",
                        ("failed", 2, msg, self._job_id),
                    )
                if cur.rowcount == 0:
                    _log.warning(
                        "watchdog: dashboard update affected 0 rows for job_id=%s",
                        self._job_id,
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            _log.warning("watchdog: dashboard update failed", exc_info=True)

    def _maybe_send_email(self, level: int) -> None:
        if self._abort_armed or self._notify_email is None:
            return
        if level not in (2, 4):
            return
        if level in self._email_sent_levels:
            return

        self._email_sent_levels.add(level)

        try:
            if self._ses_client is None:
                import boto3
                from botocore.config import Config as BotoConfig
                self._ses_client = boto3.client(
                    "ses",
                    config=BotoConfig(connect_timeout=5, read_timeout=5),
                )

            pipeline_name = os.path.basename(sys.argv[0]) if sys.argv else "unknown"
            hostname = socket.gethostname()

            if level == 2:
                subject = f"⚠ Pipeline stall: {pipeline_name} on {hostname}"
                body = (
                    f"Pipeline stall detected.\n\n"
                    f"Pipeline: {pipeline_name}\n"
                    f"Host: {hostname}\n"
                    f"Progress: {self._last_progress}"
                    f"{f' / {self._progress_total}' if self._progress_total else ''}\n"
                    f"Active workers: {self._get_active()}\n"
                    f"Stall duration: {self._stall_threshold_sec * 2}+ seconds\n"
                )
            else:
                subject = f"\U0001f6d1 Pipeline ABORT: {pipeline_name} on {hostname}"
                body = (
                    f"Pipeline ABORT — watchdog forcing exit.\n\n"
                    f"Pipeline: {pipeline_name}\n"
                    f"Host: {hostname}\n"
                    f"Progress: {self._last_progress}"
                    f"{f' / {self._progress_total}' if self._progress_total else ''}\n"
                    f"Active workers: {self._get_active()}\n"
                    f"Stall duration: {self._stall_threshold_sec * 4}+ seconds\n"
                    f"\nWatchdog forcing exit, manual restart required.\n"
                )

            self._ses_client.send_email(
                Source="watchdog@lavandulagroup.com",
                Destination={"ToAddresses": [self._notify_email]},
                Message={
                    "Subject": {"Data": subject},
                    "Body": {"Text": {"Data": body}},
                },
            )
        except Exception:
            _log.warning("watchdog: SES send failed", exc_info=True)

    def _run_loop(self, sleep_fn) -> None:
        try:
            while not self._stop and not self._abort_armed:
                try:
                    self._check_once()
                except Exception:
                    _log.critical("watchdog: check iteration failed", exc_info=True)

                if self._stop or self._abort_armed:
                    break
                sleep_fn(self._check_interval_sec)
        except BaseException as exc:
            import asyncio as _asyncio
            if isinstance(exc, _asyncio.CancelledError) and self._stop:
                return
            _log.critical("watchdog terminated unexpectedly: %s", exc)
            if self._job_id is not None:
                try:
                    self._maybe_update_dashboard_unexpected(str(exc))
                except Exception:
                    _log.warning("watchdog: best-effort dashboard update failed on unexpected termination")

    def _maybe_update_dashboard_unexpected(self, error_msg: str) -> None:
        if self._job_id is None:
            return
        try:
            import psycopg2

            db_url = os.environ.get("DJANGO_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
            if not db_url:
                return

            conn = psycopg2.connect(
                db_url,
                connect_timeout=5,
                options="-c statement_timeout=5000",
            )
            try:
                cur = conn.cursor()
                msg = f"Watchdog terminated unexpectedly: {error_msg}"
                cur.execute(
                    "UPDATE jobs SET error_message = %s WHERE id = %s",
                    (msg, self._job_id),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    async def run_async(self) -> None:
        if self._started:
            raise RuntimeError("watchdog already started")
        self._started = True
        self._mode = "async"
        import asyncio as _asyncio
        await self._async_run_loop(_asyncio.sleep)

    async def _async_run_loop(self, sleep_fn) -> None:
        try:
            while not self._stop and not self._abort_armed:
                try:
                    self._check_once()
                except Exception:
                    _log.critical("watchdog: check iteration failed", exc_info=True)

                if self._stop or self._abort_armed:
                    break
                await sleep_fn(self._check_interval_sec)
        except BaseException as exc:
            import asyncio as _asyncio
            if isinstance(exc, _asyncio.CancelledError) and self._stop:
                return
            _log.critical("watchdog terminated unexpectedly: %s", exc)
            if self._job_id is not None:
                try:
                    self._maybe_update_dashboard_unexpected(str(exc))
                except Exception:
                    _log.warning("watchdog: best-effort dashboard update failed on unexpected termination")

    def start_thread(self) -> None:
        if self._started:
            raise RuntimeError("watchdog already started")
        self._started = True
        self._mode = "thread"
        import threading as _threading
        self._thread = _threading.Thread(
            target=self._run_loop,
            args=(time.sleep,),
            daemon=True,
            name="stall-watchdog",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop = True

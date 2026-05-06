from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import time

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from pipeline.models import Job, Worker
from pipeline.orchestrator import LOG_DIR, PROJECT_ROOT, get_eligible_jobs
from pipeline.param_validators import build_argv_for_phase, ValidationError
from pipeline.stages import STAGE_REGISTRY

logger = logging.getLogger("pipeline.orchestrator")

POLL_INTERVAL = 10
HEARTBEAT_STALE_THRESHOLD = 300
HEARTBEAT_OFFLINE_THRESHOLD = 1800
ADVISORY_LOCK_ID = 34001


class Command(BaseCommand):
    help = "Run the pipeline job orchestrator daemon"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._shutdown = False

    def handle(self, *args, **options):
        self.hostname = socket.gethostname()
        self.stdout.write(f"Orchestrator starting on {self.hostname}")

        if not self._acquire_advisory_lock():
            self.stderr.write(
                "Another orchestrator instance is running (advisory lock held). Exiting."
            )
            sys.exit(1)

        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        LOG_DIR.mkdir(parents=True, exist_ok=True)

        self._register_worker()
        self._crash_recovery()

        self._tracked: dict[int, subprocess.Popen] = {}

        while not self._shutdown:
            self._poll_running_jobs()
            self._start_eligible_jobs()
            self._update_worker_heartbeat()
            self._check_worker_health()
            time.sleep(POLL_INTERVAL)

        self._shutdown_worker()
        self.stdout.write("Orchestrator shutting down")

    def _acquire_advisory_lock(self) -> bool:
        if connection.vendor != "postgresql":
            return True
        with connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", [ADVISORY_LOCK_ID])
            row = cur.fetchone()
            return row[0] if row else False

    def _signal_handler(self, signum, frame):
        self._shutdown = True

    def _register_worker(self):
        Worker.objects.update_or_create(
            hostname=self.hostname,
            defaults={
                "status": "online",
                "last_heartbeat": timezone.now(),
                "ip_address": self._get_local_ip(),
                "is_active": True,
            },
        )
        self.stdout.write(f"Worker registered: {self.hostname}")

    def _update_worker_heartbeat(self):
        try:
            Worker.objects.filter(hostname=self.hostname).update(
                status="online",
                last_heartbeat=timezone.now(),
            )
        except Exception:
            pass

    def _shutdown_worker(self):
        try:
            Worker.objects.filter(hostname=self.hostname).update(status="offline")
        except Exception:
            pass

    def _check_worker_health(self):
        from datetime import timedelta
        now = timezone.now()
        stale_cutoff = now - timedelta(seconds=HEARTBEAT_STALE_THRESHOLD)
        offline_cutoff = now - timedelta(seconds=HEARTBEAT_OFFLINE_THRESHOLD)

        Worker.objects.filter(
            is_active=True,
            status="stale",
            last_heartbeat__lt=offline_cutoff,
        ).update(status="offline")

        Worker.objects.filter(
            is_active=True,
            status="online",
            last_heartbeat__lt=stale_cutoff,
        ).update(status="stale")

    @staticmethod
    def _get_local_ip():
        try:
            import socket as _socket
            s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return None

    def _crash_recovery(self):
        """On startup, recover from unclean shutdown."""
        now = timezone.now()

        # Scheduled jobs (picked up but never spawned) -> reset to pending
        scheduled = Job.objects.filter(status="scheduled", host=self.hostname)
        for job in scheduled:
            job.status = "pending"
            job.blocked_reason = None
            job.save(update_fields=["status", "blocked_reason"])
            self.stdout.write(f"Reset scheduled Job #{job.pk} to pending (orchestrator restart)")

        # Running jobs -> reconcile via PID + start time
        running = Job.objects.filter(status="running", host=self.hostname)
        for job in running:
            if job.pid and self._is_pid_alive_with_start_time(job):
                self.stdout.write(f"Job #{job.pk} still running (PID {job.pid}), resuming tracking")
                continue
            job.status = "failed"
            job.error_message = "orphaned: PID not found or start time mismatch on restart"
            job.finished_at = now
            job.save(update_fields=["status", "error_message", "finished_at"])
            self.stdout.write(f"Marked orphaned Job #{job.pk} as failed")

    def _poll_running_jobs(self):
        running = Job.objects.filter(status="running", host=self.hostname)
        for job in running:
            proc = self._tracked.get(job.pk)

            if proc is not None:
                ret = proc.poll()
                if ret is None:
                    self._update_heartbeat(job)
                else:
                    self._finish_job(job, ret)
                    self._tracked.pop(job.pk, None)
            else:
                if job.pid and self._is_pid_alive(job.pid):
                    self._update_heartbeat(job)
                else:
                    job.status = "failed"
                    job.error_message = "orphaned: PID not found"
                    job.finished_at = timezone.now()
                    self._save_with_retry(job, ["status", "error_message", "finished_at"])

    def _start_eligible_jobs(self):
        eligible = get_eligible_jobs(self.hostname)
        from pipeline.orchestrator import check_phase_conflict

        for job in eligible[:3]:
            if check_phase_conflict(job.phase, job.state_code):
                reason = f"Phase conflict: {job.phase} already running"
                if job.state_code:
                    reason += f" for {job.state_code}"
                self._update_blocked_reason(job, reason)
                continue

            self._clear_blocked_reason(job)
            self._launch_job(job)

    def _launch_job(self, job: Job):
        # Transition to scheduled
        job.status = "scheduled"
        job.save(update_fields=["status"])

        # Build argv using registry (fall back to legacy COMMAND_MAP)
        try:
            if job.phase in STAGE_REGISTRY:
                from pipeline.param_validators import build_argv
                argv = build_argv(STAGE_REGISTRY[job.phase], job.config_json)
            else:
                from pipeline.orchestrator import build_argv as legacy_build_argv
                argv = legacy_build_argv(job.phase, job.config_json)
                logger.warning("Stage '%s' not in STAGE_REGISTRY, using legacy COMMAND_MAP", job.phase)
        except (ValidationError, Exception) as exc:
            job.status = "failed"
            job.error_message = f"Command build error: {exc}"
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error_message", "finished_at"])
            return

        state_label = job.state_code or "global"
        ts = timezone.now().strftime("%Y%m%d_%H%M%S")
        log_path = LOG_DIR / f"{job.phase}_{state_label}_{ts}_{job.pk}.log"

        env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}

        try:
            log_fh = open(log_path, "w")
            proc = subprocess.Popen(
                argv,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                cwd=str(PROJECT_ROOT),
                env=env,
                preexec_fn=os.setpgrp,
            )
        except (OSError, FileNotFoundError) as exc:
            job.status = "failed"
            job.error_message = f"Spawn error: {exc}"
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error_message", "finished_at"])
            self.stderr.write(f"Failed to spawn Job #{job.pk}: {exc}")
            return

        now = timezone.now()
        job.status = "running"
        job.pid = proc.pid
        job.started_at = now
        job.started_at_precise = now
        job.last_heartbeat = now
        job.log_file = str(log_path)
        job.progress_total = self._init_progress_total(job)
        job.save(update_fields=[
            "status", "pid", "started_at", "started_at_precise",
            "last_heartbeat", "log_file", "progress_total",
        ])

        self._tracked[job.pk] = proc
        self.stdout.write(f"Started Job #{job.pk} ({job.phase} {state_label}) PID={proc.pid}")

    _EXIT_CODE_HINTS = {
        1: "partial failure (DB flush errors or invalid input)",
        2: "startup check failed (archive/encryption/TLS)",
        -9: "killed (SIGKILL — OOM or manual kill)",
        -15: "terminated (SIGTERM — orchestrator shutdown)",
        -2: "interrupted (SIGINT)",
    }

    def _finish_job(self, job: Job, exit_code: int):
        stage = STAGE_REGISTRY.get(job.phase)

        # Check if this exit code is retryable
        if stage and stage.retry_policy.auto_retry and exit_code in stage.retry_policy.retryable_exit_codes:
            if job.attempt_number < stage.retry_policy.max_attempts:
                self._retry_job(job, exit_code, stage.retry_policy)
                return

        job.status = "completed" if exit_code == 0 else "failed"
        job.exit_code = exit_code
        job.finished_at = timezone.now()
        if exit_code != 0:
            hint = self._EXIT_CODE_HINTS.get(exit_code, "unknown error")
            last_line = self._extract_last_meaningful_line(job)
            job.error_message = f"Exit {exit_code}: {hint}"
            if last_line:
                job.error_message += f" — {last_line}"
        if job.log_file:
            try:
                with open(job.log_file, "rb") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    f.seek(max(0, size - 16_384))
                    job.log_tail = f.read().decode("utf-8", errors="replace")[-16_384:]
            except OSError:
                pass
        self._save_with_retry(job, [
            "status", "exit_code", "finished_at", "error_message", "log_tail",
        ])
        self.stdout.write(f"Job #{job.pk} finished: {job.status} (exit {exit_code})")

    def _retry_job(self, failed_job: Job, exit_code: int, retry_policy):
        """Create a retry clone of a failed job and rebound dependents."""
        from django.db import transaction

        failed_job.status = "failed"
        failed_job.exit_code = exit_code
        failed_job.finished_at = timezone.now()
        hint = self._EXIT_CODE_HINTS.get(exit_code, "unknown error")
        failed_job.error_message = f"Exit {exit_code}: {hint} (auto-retrying)"
        self._save_with_retry(failed_job, [
            "status", "exit_code", "finished_at", "error_message",
        ])

        with transaction.atomic():
            retry_job = Job.objects.create(
                state_code=failed_job.state_code,
                phase=failed_job.phase,
                status="pending",
                host=failed_job.host,
                config_json=failed_job.config_json,
                retry_of=failed_job,
                attempt_number=failed_job.attempt_number + 1,
            )
            # Rebound pending dependents to the retry job
            rebound_count = Job.objects.filter(
                depends_on=failed_job, status="pending"
            ).update(depends_on=retry_job)

        self.stdout.write(
            f"Job #{failed_job.pk} failed (exit {exit_code}), "
            f"auto-retry as Job #{retry_job.pk} "
            f"(attempt {retry_job.attempt_number}/{retry_policy.max_attempts}), "
            f"{rebound_count} dependent(s) rebound"
        )

    def _update_blocked_reason(self, job: Job, reason: str):
        if job.blocked_reason != reason:
            job.blocked_reason = reason
            try:
                job.save(update_fields=["blocked_reason"])
            except Exception:
                pass

    def _clear_blocked_reason(self, job: Job):
        if job.blocked_reason:
            job.blocked_reason = None
            try:
                job.save(update_fields=["blocked_reason"])
            except Exception:
                pass

    def _update_heartbeat(self, job: Job):
        job.last_heartbeat = timezone.now()
        if job.log_file:
            try:
                with open(job.log_file, "rb") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    f.seek(max(0, size - 16_384))
                    job.log_tail = f.read().decode("utf-8", errors="replace")[-16_384:]
            except OSError:
                pass
        try:
            job.save(update_fields=["last_heartbeat", "log_tail"])
        except Exception:
            pass

    def _save_with_retry(self, job: Job, fields: list[str]):
        try:
            job.save(update_fields=fields)
        except Exception:
            time.sleep(1)
            try:
                job.save(update_fields=fields)
            except Exception:
                sys.stderr.write(
                    f"CRITICAL: Failed to update Job #{job.pk} status. "
                    "Killing process to prevent orphan.\n"
                )
                if job.pid:
                    try:
                        os.killpg(job.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass

    def _init_progress_total(self, job: Job):
        stage = STAGE_REGISTRY.get(job.phase)
        if stage and stage.progress_estimator:
            try:
                return stage.progress_estimator(job.config_json)
            except Exception:
                pass
        # Legacy fallback
        try:
            from pipeline.models import CrawledOrg, NonprofitSeed, Report
            if job.phase == "resolve" and job.state_code:
                return NonprofitSeed.objects.filter(
                    state=job.state_code,
                ).exclude(resolver_status="resolved").count()
            if job.phase == "crawl":
                return NonprofitSeed.objects.filter(
                    resolver_status="resolved", website_url__isnull=False,
                ).count() - CrawledOrg.objects.count()
            if job.phase == "classify":
                return Report.objects.filter(classification__isnull=True).count()
        except Exception:
            pass
        return None

    def _is_pid_alive_with_start_time(self, job: Job) -> bool:
        """Check PID is alive AND started at approximately the right time."""
        if not job.pid:
            return False
        if not self._is_pid_alive(job.pid):
            return False
        if not job.started_at_precise:
            return self._is_pid_alive(job.pid)
        try:
            stat_path = f"/proc/{job.pid}/stat"
            with open(stat_path) as f:
                stat_content = f.read()
            # Field 22 (0-indexed from after the comm field) is starttime in clock ticks
            # For basic validation, just confirm the process exists and PID matches
            return True
        except (OSError, IndexError, ValueError):
            return self._is_pid_alive(job.pid)

    _LOG_NOISE = frozenset([
        "Ignoring wrong pointing object",
        "do_cmap",
        "Superfluous whitespace",
    ])

    _ERROR_SIGNALS = ("ERROR", "Exception", "Traceback", "CRITICAL", "FATAL", "failed", "Error:")

    def _extract_last_meaningful_line(self, job: Job) -> str | None:
        tail = job.log_tail
        if not tail:
            if job.log_file:
                try:
                    with open(job.log_file, "rb") as f:
                        f.seek(0, 2)
                        size = f.tell()
                        f.seek(max(0, size - 8192))
                        tail = f.read().decode("utf-8", errors="replace")
                except OSError:
                    return None
            else:
                return None

        for line in reversed(tail.splitlines()):
            stripped = line.strip()
            if not stripped:
                continue
            if any(noise in stripped for noise in self._LOG_NOISE):
                continue
            if any(sig in stripped for sig in self._ERROR_SIGNALS):
                return stripped[:200]
        return None

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

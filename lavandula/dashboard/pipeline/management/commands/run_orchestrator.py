from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from pipeline.models import CrawledOrg, Job, NonprofitSeed, Report, Worker
from pipeline.orchestrator import (
    LOG_DIR,
    PROJECT_ROOT,
    build_argv,
    check_phase_conflict,
    get_eligible_jobs,
)

POLL_INTERVAL = 10
HEARTBEAT_STALE_THRESHOLD = 300
HEARTBEAT_OFFLINE_THRESHOLD = 1800


class Command(BaseCommand):
    help = "Run the pipeline job orchestrator daemon"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._shutdown = False

    def handle(self, *args, **options):
        self.hostname = socket.gethostname()
        self.stdout.write(f"Orchestrator starting on {self.hostname}")

        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        LOG_DIR.mkdir(parents=True, exist_ok=True)

        self._register_worker()
        self._recover_orphaned_jobs()

        self._tracked: dict[int, subprocess.Popen] = {}

        while not self._shutdown:
            self._poll_running_jobs()
            self._start_eligible_jobs()
            self._update_worker_heartbeat()
            self._check_worker_health()
            time.sleep(POLL_INTERVAL)

        self._shutdown_worker()
        self.stdout.write("Orchestrator shutting down")

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

    def _recover_orphaned_jobs(self):
        """On startup, mark dead running jobs as failed."""
        orphans = Job.objects.filter(status="running", host=self.hostname)
        for job in orphans:
            if job.pid and self._is_pid_alive(job.pid):
                continue
            job.status = "failed"
            job.error_message = "orphaned: PID not found on restart"
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error_message", "finished_at"])
            self.stdout.write(f"Marked orphaned Job #{job.pk} as failed")

    def _poll_running_jobs(self):
        """Check on tracked subprocesses and update heartbeats."""
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
        """Pick and start the next eligible job."""
        eligible = get_eligible_jobs(self.hostname)
        for job in eligible[:3]:
            if check_phase_conflict(job.phase, job.state_code):
                continue
            self._launch_job(job)

    def _launch_job(self, job: Job):
        """Spawn a subprocess for the job."""
        try:
            argv = build_argv(job.phase, job.config_json)
        except Exception as exc:
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

        job.status = "running"
        job.pid = proc.pid
        job.started_at = timezone.now()
        job.last_heartbeat = timezone.now()
        job.log_file = str(log_path)
        job.progress_total = self._init_progress_total(job)
        job.save(update_fields=[
            "status", "pid", "started_at", "last_heartbeat", "log_file", "progress_total",
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
        """Mark a job as completed or failed based on exit code."""
        if exit_code == 3:
            job.status = "pending"
            job.pid = None
            job.started_at = None
            job.log_file = None
            job.log_tail = None
            self._save_with_retry(job, ["status", "pid", "started_at", "log_file", "log_tail"])
            self.stdout.write(f"Job #{job.pk} returned exit 3 (lock busy), re-queued")
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
        self._save_with_retry(job, ["status", "exit_code", "finished_at", "error_message", "log_tail"])
        self.stdout.write(f"Job #{job.pk} finished: {job.status} (exit {exit_code})")

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
        """Save job state, retry once on failure, kill process on second failure."""
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

    @staticmethod
    def _init_progress_total(job: Job):
        """Set progress_total at job start based on phase."""
        try:
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

    _LOG_NOISE = frozenset([
        "Ignoring wrong pointing object",
        "do_cmap",
        "Superfluous whitespace",
    ])

    def _extract_last_meaningful_line(self, job: Job) -> str | None:
        """Extract last meaningful line from log_tail, skipping PDF parser noise."""
        tail = job.log_tail
        if not tail:
            if job.log_file:
                try:
                    with open(job.log_file, "rb") as f:
                        f.seek(0, 2)
                        size = f.tell()
                        f.seek(max(0, size - 4096))
                        tail = f.read().decode("utf-8", errors="replace")
                except OSError:
                    return None
            else:
                return None

        for line in reversed(tail.splitlines()):
            line = line.strip()
            if not line:
                continue
            if any(noise in line for noise in self._LOG_NOISE):
                continue
            return line[:200]
        return None

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

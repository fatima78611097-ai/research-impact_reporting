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

from pipeline.models import Job, Worker, create_job_event
from pipeline.orchestrator import LOG_DIR, PROJECT_ROOT, get_eligible_jobs
from pipeline.param_validators import ValidationError
from pipeline.protocol_reader import ProtocolReader, create_protocol_pipe, setup_fd3_for_subprocess
from pipeline.provenance import read_provenance_file, update_org_provenance
from pipeline.scheduler import select_next_jobs, is_dependency_satisfied
from pipeline.scheduler_config import load_config as load_scheduler_config
from pipeline.stages import STAGE_REGISTRY

logger = logging.getLogger("pipeline.orchestrator")

POLL_INTERVAL = 10
HEARTBEAT_STALE_THRESHOLD = 300
HEARTBEAT_OFFLINE_THRESHOLD = 1800
ADVISORY_LOCK_BASE = 34001


def _host_lock_id(hostname: str) -> int:
    return ADVISORY_LOCK_BASE + (hash(hostname) % 10000)


class Command(BaseCommand):
    help = "Run the pipeline job orchestrator daemon"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._shutdown = False
        self._protocol_readers: dict[int, ProtocolReader] = {}

    def handle(self, *args, **options):
        self.hostname = socket.gethostname()
        self.stdout.write(f"Orchestrator starting on {self.hostname}")

        if not self._acquire_advisory_lock():
            self.stderr.write(
                f"Another orchestrator is already running on {self.hostname} (advisory lock held). Exiting."
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
            self._poll_host_commands()
            time.sleep(POLL_INTERVAL)

        self._shutdown_worker()
        self.stdout.write("Orchestrator shutting down")

    def _acquire_advisory_lock(self) -> bool:
        if connection.vendor != "postgresql":
            return True
        lock_id = _host_lock_id(self.hostname)
        with connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
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
            cpu_pct, mem_pct = self._read_system_metrics()
            Worker.objects.filter(hostname=self.hostname).update(
                status="online",
                last_heartbeat=timezone.now(),
                cpu_pct=cpu_pct,
                mem_pct=mem_pct,
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
            create_job_event(job, "reset", {"reason": "orchestrator restart, was scheduled"})
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
            create_job_event(job, "failed", {"reason": "orphaned on orchestrator restart"})
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
                    create_job_event(job, "failed", {"reason": "orphaned: PID not found"})

    def _start_eligible_jobs(self):
        from pipeline.models import PipelineConfig
        if PipelineConfig.get().queue_paused:
            return

        eligible = get_eligible_jobs(self.hostname)
        from pipeline.orchestrator import check_phase_conflict

        # Filter by dependency satisfaction
        ready = [j for j in eligible if is_dependency_satisfied(j)]

        # Use resource-aware scheduler
        workers = list(Worker.objects.filter(is_active=True, status="online"))
        config = load_scheduler_config()
        placements = select_next_jobs(ready, workers, config)

        for job, worker in placements:
            if worker.hostname != self.hostname:
                if job.host == self.hostname:
                    pass  # honour the user's explicit host choice
                else:
                    continue

            if check_phase_conflict(job.phase, job.state_code):
                reason = f"Phase conflict: {job.phase} already running"
                if job.state_code:
                    reason += f" for {job.state_code}"
                self._update_blocked_reason(job, reason)
                create_job_event(job, "blocked", {"reason": reason})
                continue

            self._clear_blocked_reason(job)
            self._launch_job(job)

    def _launch_job(self, job: Job):
        # Transition to scheduled
        job.status = "scheduled"
        job.save(update_fields=["status"])
        create_job_event(job, "scheduled", {"host": self.hostname})

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
            create_job_event(job, "failed", {"reason": f"Command build error: {exc}"})
            return

        argv += ["--job-id", str(job.pk)]

        state_label = job.state_code or "global"
        ts = timezone.now().strftime("%Y%m%d_%H%M%S")
        log_path = LOG_DIR / f"{job.phase}_{state_label}_{ts}_{job.pk}.log"

        env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}

        # Set up fd 3 protocol pipe for v1 stages
        stage = STAGE_REGISTRY.get(job.phase)
        protocol_pipe_r = None
        extra_popen_kwargs = {}
        if stage and stage.protocol_version >= 1:
            r_fd, w_fd = create_protocol_pipe()
            protocol_pipe_r = r_fd
            extra_popen_kwargs = setup_fd3_for_subprocess(w_fd)

        try:
            log_fh = open(log_path, "w")
            popen_kwargs = {
                "stdout": log_fh,
                "stderr": subprocess.STDOUT,
                "cwd": str(PROJECT_ROOT),
                "env": env,
            }
            if extra_popen_kwargs:
                popen_kwargs["pass_fds"] = extra_popen_kwargs["pass_fds"]
                popen_kwargs["preexec_fn"] = extra_popen_kwargs["preexec_fn"]
            else:
                popen_kwargs["preexec_fn"] = os.setpgrp

            proc = subprocess.Popen(argv, **popen_kwargs)

            # Close write end of protocol pipe in parent
            if stage and stage.protocol_version >= 1:
                os.close(w_fd)
        except (OSError, FileNotFoundError) as exc:
            if protocol_pipe_r is not None:
                os.close(protocol_pipe_r)
                os.close(w_fd)
            job.status = "failed"
            job.error_message = f"Spawn error: {exc}"
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error_message", "finished_at"])
            create_job_event(job, "failed", {"reason": f"Spawn error: {exc}"})
            self.stderr.write(f"Failed to spawn Job #{job.pk}: {exc}")
            return

        # Start protocol reader for v1 stages
        if protocol_pipe_r is not None:
            reader = ProtocolReader(
                protocol_pipe_r,
                on_progress=lambda p, jid=job.pk: self._handle_protocol_progress(jid, p),
                on_error=lambda e, jid=job.pk: self._handle_protocol_error(jid, e),
                on_warning=lambda w, jid=job.pk: self._handle_protocol_warning(jid, w),
            )
            reader.start()
            self._protocol_readers[job.pk] = reader

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
        create_job_event(job, "started", {
            "pid": proc.pid,
            "log_file": str(log_path),
            "protocol_version": stage.protocol_version if stage else 0,
        })
        self.stdout.write(f"Started Job #{job.pk} ({job.phase} {state_label}) PID={proc.pid}")

    _EXIT_CODE_HINTS = {
        1: "partial failure (DB flush errors or invalid input)",
        2: "startup check failed (archive/TLS)",
        -9: "killed (SIGKILL — OOM or manual kill)",
        -15: "terminated (SIGTERM — orchestrator shutdown)",
        -2: "interrupted (SIGINT)",
    }

    def _finish_job(self, job: Job, exit_code: int):
        stage = STAGE_REGISTRY.get(job.phase)

        # Stop protocol reader if one was running
        reader = self._protocol_readers.pop(job.pk, None)
        if reader:
            reader.stop()

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

        # Collect summary from protocol reader if available
        event_payload = {"exit_code": exit_code}
        if reader and reader.last_summary:
            event_payload["summary"] = reader.last_summary
        if reader and reader.errors:
            event_payload["protocol_errors"] = len(reader.errors)

        self._save_with_retry(job, [
            "status", "exit_code", "finished_at", "error_message", "log_tail",
        ])

        event_type = "completed" if exit_code == 0 else "failed"
        create_job_event(job, event_type, event_payload)

        # Update org provenance on successful completion
        if exit_code == 0 and stage and stage.provenance_column:
            self._update_provenance(job, stage)

        self.stdout.write(f"Job #{job.pk} finished: {job.status} (exit {exit_code})")

    def _retry_job(self, failed_job: Job, exit_code: int, retry_policy):
        """Create a retry clone of a failed job and rebound dependents."""
        from django.db import transaction

        # Stop protocol reader if one was running
        reader = self._protocol_readers.pop(failed_job.pk, None)
        if reader:
            reader.stop()

        failed_job.status = "failed"
        failed_job.exit_code = exit_code
        failed_job.finished_at = timezone.now()
        hint = self._EXIT_CODE_HINTS.get(exit_code, "unknown error")
        failed_job.error_message = f"Exit {exit_code}: {hint} (auto-retrying)"
        self._save_with_retry(failed_job, [
            "status", "exit_code", "finished_at", "error_message",
        ])

        create_job_event(failed_job, "failed", {
            "exit_code": exit_code, "auto_retrying": True,
        })

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

        create_job_event(retry_job, "retried", {
            "retry_of": failed_job.pk,
            "attempt": retry_job.attempt_number,
            "max_attempts": retry_policy.max_attempts,
        })
        if rebound_count:
            create_job_event(retry_job, "dependency_rebound", {"rebound_count": rebound_count})

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
            return True
        try:
            stat_path = f"/proc/{job.pid}/stat"
            with open(stat_path) as f:
                stat_content = f.read()
            # Parse field 22 (starttime in clock ticks since boot).
            # Fields are space-separated but field 2 (comm) can contain spaces/parens,
            # so we split from the last ')' which ends the comm field.
            after_comm = stat_content[stat_content.rfind(")") + 2:]
            fields = after_comm.split()
            # Field 22 in /proc/PID/stat is index 19 after the comm field (0-indexed)
            starttime_ticks = int(fields[19])
            clk_tck = os.sysconf("SC_CLK_TCK")

            # Get system boot time
            with open("/proc/stat") as f:
                for line in f:
                    if line.startswith("btime "):
                        boot_time = int(line.split()[1])
                        break
                else:
                    return True

            proc_start_epoch = boot_time + (starttime_ticks / clk_tck)
            job_start_epoch = job.started_at_precise.timestamp()

            # Allow 5 seconds of skew between recorded start and actual start
            return abs(proc_start_epoch - job_start_epoch) < 5.0
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

    def _update_provenance(self, job: Job, stage):
        """Read provenance file and update org_provenance table."""
        try:
            outcomes = read_provenance_file(job.pk)
            if outcomes:
                count = update_org_provenance(stage.name, outcomes)
                logger.info("Job #%d: updated provenance for %d EINs", job.pk, count)
        except FileNotFoundError:
            logger.debug("Job #%d: no provenance file (stage may not emit one)", job.pk)
        except Exception as exc:
            logger.warning("Job #%d: provenance update failed: %s", job.pk, exc)

    def _handle_protocol_progress(self, job_pk: int, progress: dict):
        """Handle a PROGRESS event from the protocol reader."""
        try:
            job = Job.objects.get(pk=job_pk)
            updated_fields = []
            if "current" in progress:
                job.progress_current = progress["current"]
                updated_fields.append("progress_current")
            if "total" in progress:
                job.progress_total = progress["total"]
                updated_fields.append("progress_total")
            if updated_fields:
                job.save(update_fields=updated_fields)
        except Exception:
            pass

    def _handle_protocol_error(self, job_pk: int, error: dict):
        """Handle an ERROR event from the protocol reader."""
        try:
            job = Job.objects.get(pk=job_pk)
            create_job_event(job, "error", {
                "error_class": error.get("error_class", ""),
                "detail": error.get("error_detail", ""),
            })
        except Exception:
            pass

    def _handle_protocol_warning(self, job_pk: int, warning: dict):
        """Handle a WARNING event from the protocol reader."""
        try:
            job = Job.objects.get(pk=job_pk)
            create_job_event(job, "warning", {
                "warning_class": warning.get("warning_class", ""),
                "detail": warning.get("detail", ""),
            })
        except Exception:
            pass

    @staticmethod
    def _read_system_metrics() -> tuple[float | None, float | None]:
        """Read current CPU and memory utilization from /proc."""
        cpu_pct = None
        mem_pct = None
        try:
            with open("/proc/meminfo") as f:
                meminfo = {}
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        meminfo[parts[0].rstrip(":")] = int(parts[1])
            total = meminfo.get("MemTotal", 0)
            available = meminfo.get("MemAvailable", 0)
            if total > 0:
                mem_pct = round((1 - available / total) * 100, 1)
        except (OSError, ValueError, KeyError):
            pass
        try:
            load1 = os.getloadavg()[0]
            ncpu = os.cpu_count() or 1
            cpu_pct = round(min(load1 / ncpu * 100, 100.0), 1)
        except (OSError, AttributeError):
            pass
        return cpu_pct, mem_pct

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    # ------------------------------------------------------------------
    # Host Command Agent
    # ------------------------------------------------------------------

    def _poll_host_commands(self):
        from datetime import timedelta
        from django.db import transaction
        from pipeline.models import HostCommand

        self._expire_stale_commands()

        with transaction.atomic():
            cmd = (
                HostCommand.objects
                .select_for_update(skip_locked=True)
                .filter(host=self.hostname, status="pending")
                .order_by("created_at")
                .first()
            )
            if cmd is None:
                self._check_restart_recovery()
                return

            cmd.status = "running"
            cmd.started_at = timezone.now()
            cmd.save(update_fields=["status", "started_at"])

        try:
            result = self._execute_host_command(cmd)
            cmd.status = "completed"
            cmd.result_text = _sanitize_result(result)[:4096]
        except Exception as e:
            cmd.status = "failed"
            cmd.result_text = _sanitize_result(str(e))[:4096]
        cmd.finished_at = timezone.now()
        cmd.save(update_fields=["status", "result_text", "finished_at"])

    def _expire_stale_commands(self):
        from datetime import timedelta
        from pipeline.models import HostCommand

        now = timezone.now()
        HostCommand.objects.filter(
            host=self.hostname, status="pending",
            created_at__lt=now - timedelta(seconds=60)
        ).update(
            status="failed", finished_at=now,
            result_text="Timed out: command was pending for > 60 seconds"
        )
        HostCommand.objects.filter(
            host=self.hostname, status="running",
            started_at__lt=now - timedelta(seconds=120)
        ).exclude(
            command__in=["restart-orchestrator", "stop-orchestrator"]
        ).update(
            status="failed", finished_at=now,
            result_text="Timed out: command was running for > 120 seconds"
        )

    _ALLOWED_SERVICES = {
        "orchestrator": "lavandula-orchestrator",
        "dashboard": "lavandula-dashboard",
    }

    _SERVICE_COMMANDS = {
        "start-orchestrator", "stop-orchestrator", "restart-orchestrator",
        "start-dashboard", "stop-dashboard", "restart-dashboard",
    }

    def _execute_host_command(self, cmd):
        if cmd.command in self._SERVICE_COMMANDS:
            return self._exec_service_action(cmd)
        elif cmd.command == "report-status":
            return self._exec_report_status()
        elif cmd.command == "cleanup-locks":
            return self._exec_cleanup_locks(cmd.args_json)
        elif cmd.command == "kill-process":
            return self._exec_kill_process(cmd.args_json)
        else:
            raise ValueError(f"Unknown command: {cmd.command}")

    def _exec_service_action(self, cmd):
        parts = cmd.command.split("-", 1)
        action = parts[0]
        service_key = parts[1]
        service_name = self._ALLOWED_SERVICES[service_key]

        if action not in ("start", "stop", "restart"):
            raise ValueError(f"Invalid service action: {action}")

        argv = ["sudo", "systemctl", action, service_name]

        if cmd.command in ("restart-orchestrator", "stop-orchestrator"):
            subprocess.Popen(argv)
            return f"{action} {service_name} issued (fire-and-forget, this process will restart)"

        subprocess.Popen(argv)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            time.sleep(1)
            try:
                result = subprocess.run(
                    ["sudo", "systemctl", "is-active", service_name],
                    capture_output=True, text=True, timeout=5
                )
                is_active = result.stdout.strip() == "active"
                if action in ("start", "restart") and is_active:
                    return f"{service_name} is active"
                if action == "stop" and not is_active:
                    return f"{service_name} stopped"
            except (subprocess.TimeoutExpired, OSError):
                continue
        return f"{action} {service_name} issued, status check inconclusive after 10s"

    def _check_restart_recovery(self):
        from datetime import timedelta
        from pipeline.models import HostCommand

        now = timezone.now()
        cmds = HostCommand.objects.filter(
            host=self.hostname,
            command__in=["restart-orchestrator", "stop-orchestrator"],
            status="running",
        )
        for cmd in cmds:
            if cmd.started_at and (now - cmd.started_at).total_seconds() <= 120:
                try:
                    result = subprocess.run(
                        ["sudo", "systemctl", "is-active", "lavandula-orchestrator"],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.stdout.strip() == "active":
                        cmd.status = "completed"
                        cmd.result_text = "Orchestrator restarted successfully (confirmed by new process)"
                        cmd.finished_at = now
                        cmd.save(update_fields=["status", "result_text", "finished_at"])
                except (subprocess.TimeoutExpired, OSError):
                    pass
            elif cmd.started_at and (now - cmd.started_at).total_seconds() > 120:
                cmd.status = "failed"
                cmd.result_text = "Timed out: restart command was running for > 120 seconds"
                cmd.finished_at = now
                cmd.save(update_fields=["status", "result_text", "finished_at"])

    def _exec_report_status(self):
        import json
        import shutil

        status = {}
        for svc in ("lavandula-orchestrator", "lavandula-dashboard"):
            try:
                result = subprocess.run(
                    ["sudo", "systemctl", "is-active", svc],
                    capture_output=True, text=True, timeout=5
                )
                status[svc] = result.stdout.strip()
            except (subprocess.TimeoutExpired, OSError):
                status[svc] = "unknown"

        try:
            disk = shutil.disk_usage("/")
            status["disk_total_gb"] = round(disk.total / (1024**3), 1)
            status["disk_used_gb"] = round(disk.used / (1024**3), 1)
            status["disk_free_gb"] = round(disk.free / (1024**3), 1)
            status["disk_used_pct"] = round(disk.used / disk.total * 100, 1)
        except OSError:
            pass

        try:
            with open("/proc/uptime") as f:
                uptime_sec = float(f.read().split()[0])
                status["uptime_hours"] = round(uptime_sec / 3600, 1)
        except (OSError, ValueError):
            pass

        status["running_jobs"] = Job.objects.filter(
            host=self.hostname, status="running"
        ).count()

        return json.dumps(status)

    def _exec_cleanup_locks(self, args):
        dry_run = args.get("dry_run", False)
        if not isinstance(dry_run, bool):
            raise ValueError("dry_run must be a boolean")

        from django.db import connections
        with connections["default"].cursor() as cur:
            cur.execute("""
                SELECT l.pid, a.client_addr, a.state, a.backend_start
                FROM pg_locks l
                JOIN pg_stat_activity a ON a.pid = l.pid
                WHERE l.locktype = 'advisory' AND a.state = 'idle'
                  AND a.usename = current_user
            """)
            lock_rows = cur.fetchall()

        running_host_ips = set(
            Worker.objects.filter(
                hostname__in=Job.objects.filter(status="running")
                    .values_list("host", flat=True),
                is_active=True
            ).values_list("ip_address", flat=True)
        )
        from datetime import timedelta
        active_worker_ips = set(
            Worker.objects.filter(
                is_active=True,
                last_heartbeat__gte=timezone.now() - timedelta(seconds=60)
            ).values_list("ip_address", flat=True)
        )
        protected_ips = {str(ip) for ip in running_host_ips if ip} | {str(ip) for ip in active_worker_ips if ip}

        orphans = []
        for pid, client_addr, state, backend_start in lock_rows:
            if str(client_addr) not in protected_ips:
                orphans.append(pid)

        if dry_run:
            return f"Dry run: found {len(orphans)} orphaned lock(s): PIDs {orphans}"

        released = 0
        failed = 0
        from django.db import connections
        with connections["default"].cursor() as cur:
            for pid in orphans:
                cur.execute("SELECT pg_terminate_backend(%s)", [pid])
                if cur.fetchone()[0]:
                    released += 1
                else:
                    failed += 1

        return f"Released {released} orphaned lock(s), {failed} failed"

    def _exec_kill_process(self, args):
        pid = args.get("pid")
        sig_name = args.get("signal", "TERM")

        if not isinstance(pid, int):
            raise ValueError("pid must be an integer")
        if sig_name not in ("TERM", "KILL"):
            raise ValueError("signal must be TERM or KILL")

        sig = signal.SIGTERM if sig_name == "TERM" else signal.SIGKILL
        try:
            os.kill(pid, sig)
            return f"Sent SIG{sig_name} to PID {pid}"
        except ProcessLookupError:
            return f"PID {pid} not found (already exited)"
        except PermissionError:
            raise ValueError(f"Permission denied sending signal to PID {pid}")


import re

_SECRET_PATTERNS = [
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"(?:aws_)?(?:secret_)?(?:access_)?key\s*[:=]\s*\S+", re.I),
    re.compile(r"(?:password|passwd|token|secret|api.?key)\s*[:=]\s*\S+", re.I),
    re.compile(r"/home/\w+", re.I),
    re.compile(r"(?:export\s+)?\w+(?:KEY|SECRET|TOKEN|PASSWORD)\w*\s*=\s*\S+", re.I),
]


def _sanitize_result(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text[:4096]

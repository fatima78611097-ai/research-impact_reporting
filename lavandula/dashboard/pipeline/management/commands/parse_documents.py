"""Django management command: orchestrate Docling GPU parsing.

Manages a G6.2xlarge spot instance lifecycle and coordinates the
docling_worker process. Runs on cloud2.

Usage:
    manage.py parse_documents <run_tag> [options]
    manage.py parse_documents <run_tag> --dry-run
    manage.py parse_documents <run_tag> --status
    manage.py parse_documents <run_tag> --terminate
"""
from __future__ import annotations

import json
import logging
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.management.base import BaseCommand, CommandError

from lavandula.parse import config, db


logger = logging.getLogger("pipeline.parse")

AVG_PAGES_PER_DOC = 30
SECONDS_PER_PAGE = 0.49
SPOT_RATE_PER_HOUR = 0.60
ONDEMAND_RATE_PER_HOUR = 0.98
POLL_INTERVAL_SECONDS = 60
HEARTBEAT_STALE_MINUTES = 5
HEARTBEAT_LEGACY_STALE_MINUTES = 30
MAX_CONSECUTIVE_FAILURES = 3
LOG_S3_BUCKET = "lavandula-nonprofit-collaterals"
CAPACITY_RETRY_INTERVAL = 300
SSM_AMI_PARAM = "/cloud2.lavandulagroup.com/docling-ami-id"
SUBNET_AZ = [
    ("subnet-0e2008e48d602e945", "us-east-1a"),
    ("subnet-0f77191c6900e912d", "us-east-1b"),
    ("subnet-0f80a930457d062d5", "us-east-1d"),
    ("subnet-0a92608217981d266", "us-east-1c"),
    ("subnet-0f2ce8c36edfe58f5", "us-east-1f"),
]
SECURITY_GROUP_ID = "sg-0d9a6217a104cfe35"
IAM_PROFILE_NAME = "cloud2_lavandulagroup"
INSTANCE_TAG_PURPOSE = "docling-parse"


class Command(BaseCommand):
    help = "Orchestrate Docling GPU document parsing"

    def add_arguments(self, parser):
        parser.add_argument("run_tag", type=str, help="Unique identifier for this parse run")
        parser.add_argument("--priority", default="annual,impact", help="Comma-separated classification filter")
        parser.add_argument("--ntee", default=None, help="NTEE prefix filter (e.g. 'P2%%' for Human Services)")
        parser.add_argument("--instance-type", default="g6.2xlarge")
        parser.add_argument("--no-spot", action="store_true", default=False, help="Use on-demand instead of spot")
        parser.add_argument("--max-hours", type=int, default=12)
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--status", action="store_true")
        parser.add_argument("--terminate", action="store_true")
        parser.add_argument("--max-docs", type=int, default=None,
                            help="Hard cap on documents to process (passed to worker)")
        parser.add_argument("--retry-errors", action="store_true")
        parser.add_argument("--reparse", action="store_true")
        parser.add_argument("--min-version", type=str)
        parser.add_argument("--ami-id", default=None,
                            help="AMI ID to launch (default: read from SSM)")
        parser.add_argument("--start-at", default=None,
                            help="ISO 8601 UTC datetime to delay launch (YYYY-MM-DDTHH:MM)")
        parser.add_argument("--capacity-wait-hours", type=int, default=1,
                            help="Hours to retry when no spot capacity (1-12)")
        parser.add_argument("--job-id", type=int, default=None,
                            help="Dashboard Job ID to update progress on")
        parser.add_argument("--workers", type=int, default=1,
                            help="Number of concurrent GPU instances (1-4)")

    def handle(self, *args, **options):
        run_tag = options["run_tag"]

        if not config.validate_run_tag(run_tag):
            raise CommandError("run_tag must match ^[a-zA-Z0-9_-]{1,64}$")

        if not (1 <= options["max_hours"] <= 24):
            raise CommandError("--max-hours must be 1-24")

        if not (10 <= options["batch_size"] <= 5000):
            raise CommandError("--batch-size must be 10-5000")

        if not (1 <= options["workers"] <= 4):
            raise CommandError("--workers must be 1-4")

        priority_values = [v.strip() for v in options["priority"].split(",")]
        if not config.validate_priority_values(priority_values):
            raise CommandError("--priority values must match ^[a-z_]+$")

        ntee_filter = options.get("ntee")
        if ntee_filter and not all(c.isalnum() or c == '%' for c in ntee_filter):
            raise CommandError("--ntee must be alphanumeric with optional trailing %")

        self._shutdown_requested = False
        self._ec2 = None
        self._slots = []
        self._deploy_cmds = {}

        def _sigterm_handler(signum, frame):
            self._shutdown_requested = True
            self.stderr.write("SIGTERM received — shutting down gracefully\n")
            if self._ec2 and self._slots:
                for slot in self._slots:
                    iid = slot.get("instance_id")
                    if iid and slot.get("status") == "running":
                        try:
                            self._ec2.terminate_instances(InstanceIds=[iid])
                            self.stderr.write(f"SIGTERM: terminated {iid}\n")
                        except Exception:
                            pass

        signal.signal(signal.SIGTERM, _sigterm_handler)

        if options["dry_run"]:
            return self._dry_run(run_tag, priority_values, ntee_filter, options)
        if options["status"]:
            return self._show_status(run_tag)
        if options["terminate"]:
            return self._terminate(run_tag)
        return self._run(run_tag, priority_values, ntee_filter, options)

    def _dry_run(self, run_tag: str, priority: list[str], ntee_filter: str | None, options: dict) -> None:
        conn = self._get_conn()
        try:
            count = db.get_eligible_count(conn, priority, ntee_filter=ntee_filter)
        finally:
            conn.close()

        est_pages = count * AVG_PAGES_PER_DOC
        est_seconds = est_pages * SECONDS_PER_PAGE
        est_hours = est_seconds / 3600
        use_spot = not options["no_spot"]
        rate = SPOT_RATE_PER_HOUR if use_spot else ONDEMAND_RATE_PER_HOUR
        est_cost = est_hours * rate
        pricing_label = f"spot @ ${SPOT_RATE_PER_HOUR}/hr" if use_spot else f"on-demand @ ${ONDEMAND_RATE_PER_HOUR}/hr"

        workers = options.get("workers", 1)
        lines = [
            f"Eligible documents: {count:,}",
            f"Estimated pages:    ~{est_pages:,.0f}",
            f"Estimated GPU time: ~{est_hours:.1f} hours (total compute)",
            f"Estimated cost:     ~${est_cost:.0f} ({pricing_label})",
        ]
        if workers > 1 or count > 0:
            for n in (1, 2, 4):
                wall = est_hours / n
                lines.append(
                    f"  {n} worker{'s' if n > 1 else ''}:  ~{wall:.1f}h wall time, "
                    f"~${est_cost:.0f} ({pricing_label})"
                )
        lines.extend([
            f"Priority filter:    {', '.join(priority)}",
            f"NTEE filter:        {ntee_filter or 'all'}",
            f"Instance type:      {options['instance_type']}",
            f"Workers:            {workers}",
        ])
        self.stdout.write("\n".join(lines) + "\n")

    def _show_status(self, run_tag: str) -> None:
        conn = self._get_conn()
        try:
            status = db.get_run_status(conn, run_tag)
        finally:
            conn.close()

        if not status:
            self.stderr.write(f"No run found with tag '{run_tag}'\n")
            sys.exit(1)

        self.stdout.write(f"Run tag:     {status['run_tag']}\n")
        self.stdout.write(f"Started:     {status['started_at']}\n")
        self.stdout.write(f"Finished:    {status['finished_at'] or 'in progress'}\n")
        self.stdout.write(f"Instance:    {status['instance_id'] or 'unknown'}\n")

        stats = status.get("stats_json")
        if stats:
            if isinstance(stats, str):
                stats = json.loads(stats)
            total = stats.get("total", 0)
            succeeded = stats.get("succeeded", 0)
            failed = stats.get("failed", 0)
            self.stdout.write(
                f"Progress:    {succeeded:,} succeeded, {failed:,} failed, "
                f"{total:,} total\n"
            )

    def _terminate(self, run_tag: str) -> None:
        import boto3

        ec2 = boto3.client("ec2", region_name="us-east-1")

        # Terminate all instances for all slots
        terminated = 0
        for slot_idx in range(4):
            instance_id = self._find_instance(ec2, run_tag, slot_index=slot_idx)
            if instance_id:
                ec2.terminate_instances(InstanceIds=[instance_id])
                self.stdout.write(f"Terminated instance {instance_id} (slot {slot_idx})\n")
                terminated += 1

        # Also try the old-style tag (no slot index) for backward compat
        instance_id = self._find_instance(ec2, run_tag)
        if instance_id:
            ec2.terminate_instances(InstanceIds=[instance_id])
            self.stdout.write(f"Terminated instance {instance_id}\n")
            terminated += 1

        if terminated == 0:
            self.stdout.write("No running instances found for this run tag.\n")

        conn = self._get_conn()
        try:
            status = db.get_run_status(conn, run_tag)
            if status and not status.get("finished_at"):
                db.finish_run(conn, status["id"], status.get("stats_json") or {})
        finally:
            conn.close()

    def _run(self, run_tag: str, priority: list[str], ntee_filter: str | None, options: dict) -> None:
        import boto3

        conn = self._get_conn()

        if not db.acquire_orchestrator_lock(conn):
            conn.close()
            raise CommandError(
                "Another orchestrator is already running (advisory lock held). "
                "Use --status to check progress or --terminate to stop it."
            )

        try:
            self._execute_run(conn, run_tag, priority, ntee_filter, options)
        finally:
            # B5: guarantee GPU instances are terminated even if _execute_run
            # raises (an uncaught poll-loop error would otherwise orphan the
            # whole fleet running with no max_hours backstop). _terminate_all is
            # idempotent, and the tag-based reaper catches instances whose id
            # never made it onto a slot (e.g. a launch that failed mid-way).
            try:
                if self._ec2 is not None and self._slots:
                    self._terminate_all(self._ec2, self._slots)
                    self._reap_orphans(self._ec2, run_tag, self._slots, options.get("workers", 1))
            except Exception:
                logger.exception("instance cleanup in _run finally failed")
            db.release_orchestrator_lock(conn)
            conn.close()

    def _update_job_progress(self, job_id, succeeded, total, status=None):
        if not job_id:
            return
        try:
            from pipeline.models import Job
            from django.utils import timezone as tz
            updates = {
                "progress_current": succeeded,
                "progress_total": total,
                "last_heartbeat": tz.now(),
            }
            fields = ["progress_current", "progress_total", "last_heartbeat"]
            if status:
                updates["status"] = status
                fields.append("status")
                if status == "running":
                    updates["started_at"] = tz.now()
                    fields.append("started_at")
                elif status in ("completed", "failed"):
                    updates["finished_at"] = tz.now()
                    fields.append("finished_at")
            Job.objects.filter(pk=job_id).update(**updates)
        except Exception:
            logger.exception("Failed to update Job %s progress", job_id)

    def _populate_or_resume_queue(self, conn, run_id, priority, ntee_filter):
        """Populate work queue or resume from existing queue."""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM lava_parse.work_queue WHERE run_id = %s",
                (run_id,),
            )
            existing = cur.fetchone()[0]

        if existing > 0:
            reclaimed = db.reclaim_all_stale_claims(conn, run_id)
            self.stdout.write(
                f"Resuming: {existing} queue items, {reclaimed} stale claims reclaimed\n"
            )
            return existing
        else:
            count = db.populate_work_queue(conn, run_id, priority, ntee_filter)
            self.stdout.write(f"Populated work queue: {count} items\n")
            return count

    def _terminate_all(self, ec2, slots):
        """Terminate all running instances across all slots."""
        for slot in slots:
            if slot["instance_id"] and slot["status"] == "running":
                self._safe_terminate(ec2, slot["instance_id"])
                slot["status"] = "terminated"

    def _reap_orphans(self, ec2, run_tag, slots, workers):
        """Tag-based backstop: terminate any instance tagged for this run that
        is not tracked in a slot (e.g. created by a launch that failed before
        its id was recorded). Scoped tightly to this run_tag's per-slot tags so
        it never reaps a concurrent run's instances."""
        known = {s.get("instance_id") for s in slots if s.get("instance_id")}
        for slot_idx in range(max(int(workers), len(slots))):
            try:
                iid = self._find_instance(ec2, run_tag, slot_index=slot_idx)
            except Exception:
                continue
            if iid and iid not in known:
                self.stderr.write(f"Reaping orphan instance {iid} (slot {slot_idx})\n")
                try:
                    ec2.terminate_instances(InstanceIds=[iid])
                except Exception:
                    logger.exception("failed to reap orphan %s", iid)

    def _get_worker_last_activity(self, conn, run_id, worker_id):
        """Get the most recent completed_at for a worker (heartbeat signal)."""
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT MAX(completed_at)
                FROM lava_parse.work_queue
                WHERE run_id = %s AND claimed_by = %s AND completed_at IS NOT NULL
                """,
                (run_id, worker_id),
            )
            row = cur.fetchone()
            return row[0] if row else None

    def _get_worker_open_claim_age(self, conn, run_id, worker_id):
        """Oldest still-open (claimed, not completed) claim for a worker.

        Liveness fallback for a worker with zero completions: a worker that dies
        before its first completion has no MAX(completed_at) heartbeat, so without
        this it would never be flagged stale and would idle until max_hours.
        """
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT MIN(claimed_at)
                FROM lava_parse.work_queue
                WHERE run_id = %s AND claimed_by = %s AND completed_at IS NULL
                """,
                (run_id, worker_id),
            )
            row = cur.fetchone()
            return row[0] if row else None

    def _update_instance_ids(self, conn, run_id, slots):
        """Update parse_runs with current instance ID list."""
        active_ids = [s["instance_id"] for s in slots if s["instance_id"]]
        first_id = active_ids[0] if active_ids else None
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE lava_parse.parse_runs SET instance_id = %s, instance_ids = %s WHERE id = %s",
                    (first_id, active_ids or None, run_id),
                )

    def _execute_run(self, conn, run_tag: str, priority: list[str], ntee_filter: str | None, options: dict) -> None:
        import boto3

        ec2 = boto3.client("ec2", region_name="us-east-1")
        self._ec2 = ec2
        job_id = options.get("job_id")
        workers = options.get("workers", 1)

        # Scheduled start: wait until start_at time
        start_at_str = options.get("start_at")
        if start_at_str:
            from datetime import datetime, timezone as tz
            start_at = datetime.fromisoformat(start_at_str).replace(tzinfo=tz.utc)
            wait_seconds = (start_at - datetime.now(tz.utc)).total_seconds()
            if wait_seconds > 0:
                self.stdout.write(f"Scheduled start: waiting until {start_at.isoformat()} UTC\n")
                slept = 0
                while slept < wait_seconds:
                    if self._shutdown_requested:
                        self.stdout.write("Shutdown during scheduled wait.\n")
                        self._update_job_progress(job_id, 0, 0, "cancelled")
                        return
                    time.sleep(min(30, wait_seconds - slept))
                    slept += 30

        self._update_job_progress(job_id, 0, 0, "pending")

        batch_size = options["batch_size"]
        if workers > 1 and batch_size > 200:
            batch_size = max(50, batch_size // workers)
            self.stdout.write(
                f"Batch size adjusted to {batch_size} for {workers} workers\n"
            )
        options["batch_size"] = batch_size

        run_config = {
            "priority": priority,
            "ntee_filter": ntee_filter,
            "instance_type": options["instance_type"],
            "batch_size": batch_size,
            "max_hours": options["max_hours"],
            "retry_errors": options["retry_errors"],
            "reparse": options["reparse"],
            "min_version": options.get("min_version"),
            "workers": workers,
        }

        try:
            run_id = db.create_parse_run(conn, run_tag, run_config)
        except db.RunTagConflict as e:
            self._update_job_progress(job_id, 0, 0, "failed")
            raise CommandError(str(e))

        self.stdout.write(f"Parse run {run_id} created/resumed (tag: {run_tag})\n")

        if options["retry_errors"]:
            self._delete_error_rows(conn, priority)
        if options["reparse"] and options.get("min_version"):
            self._delete_old_version_rows(conn, priority, options["min_version"])

        # Populate or resume work queue
        eligible_count = self._populate_or_resume_queue(conn, run_id, priority, ntee_filter)
        if eligible_count == 0:
            self.stdout.write("No eligible documents. Nothing to do.\n")
            db.finish_run(conn, run_id, {"total": 0, "succeeded": 0, "failed": 0})
            self._update_job_progress(job_id, 0, 0, "completed")
            return

        self._update_job_progress(job_id, 0, eligible_count, "running")

        start_time = time.time()
        max_seconds = options["max_hours"] * 3600
        final_status = "completed"

        # Initialize slots (also stored on self for SIGTERM handler access)
        slots = [
            {
                "instance_id": None,
                "status": "pending",
                "consecutive_failures": 0,
                "deploy_ok": False,
            }
            for _ in range(workers)
        ]
        self._slots = slots

        # Launch N instances (parallel across AZs)
        self.stdout.write(f"Launching {workers} worker(s) across AZs...\n")
        launched = 0
        if workers == 1:
            iid = self._launch_with_capacity_retry(
                ec2, conn, run_id, run_tag, priority, ntee_filter, options, slot_index=0
            )
            if iid:
                slots[0]["instance_id"] = iid
                slots[0]["status"] = "running"
                launched = 1
            else:
                slots[0]["status"] = "capacity_exhausted"
                self.stderr.write("[slot 0] Failed to launch — capacity exhausted\n")
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(
                        self._launch_with_capacity_retry,
                        ec2, conn, run_id, run_tag, priority, ntee_filter, options,
                        slot_index=idx,
                    ): idx
                    for idx in range(workers)
                }
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        iid = future.result()
                    except Exception as e:
                        self.stderr.write(f"[slot {idx}] Launch error: {e}\n")
                        iid = None
                    if iid:
                        slots[idx]["instance_id"] = iid
                        slots[idx]["status"] = "running"
                        launched += 1
                    else:
                        slots[idx]["status"] = "capacity_exhausted"
                        self.stderr.write(f"[slot {idx}] Failed to launch — capacity exhausted\n")

        self.stdout.write(f"Launch complete: {launched}/{workers} workers active\n")

        if launched == 0:
            self.stderr.write("Failed to launch any instances. Run failed.\n")
            self._update_job_progress(job_id, 0, eligible_count, "failed")
            db.finish_run(conn, run_id, {"total": 0, "succeeded": 0, "failed": 0})
            return

        self._update_instance_ids(conn, run_id, slots)

        if launched < workers:
            self.stdout.write(
                f"Partial launch: {launched}/{workers} workers active\n"
            )

        # Track last-known completed count per slot for progress-reset logic
        slot_last_completed = [0] * len(slots)

        # Multi-instance poll loop
        while True:
            time.sleep(POLL_INTERVAL_SECONDS)

            if self._shutdown_requested:
                self.stdout.write("Shutdown requested. Terminating all instances.\n")
                db.set_exit_reason(conn, run_id, "cancelled")
                self._terminate_all(ec2, slots)
                final_status = "cancelled"
                break

            elapsed = time.time() - start_time
            if elapsed >= max_seconds:
                self.stdout.write(f"Max hours ({options['max_hours']}) reached. Terminating all.\n")
                db.set_exit_reason(conn, run_id, "max_hours")
                self._terminate_all(ec2, slots)
                final_status = "timeout"
                break

            # DB health check: if our own connection is broken, skip stale
            # detection this cycle (we can't distinguish worker-stale from
            # orchestrator-DB-down).
            db_healthy = self._db_healthy(conn)

            # Check each slot
            for slot_idx, slot in enumerate(slots):
                if slot["status"] != "running":
                    continue
                iid = slot["instance_id"]

                # 1. Check instance state — is it still running?
                state = self._get_instance_state(ec2, iid)

                if state in ("terminated", "shutting-down"):
                    # Check if worker set exit_reason (graceful exit)
                    exit_reason = None
                    try:
                        exit_reason = db.get_exit_reason(conn, run_id)
                    except Exception:
                        pass

                    if exit_reason:
                        classification = "graceful_exit"
                        self.stdout.write(
                            f"[slot {slot_idx}] Worker exited: {exit_reason} "
                            f"(classification={classification})\n"
                        )
                        self._pull_worker_log(iid, run_tag)
                        db.reclaim_stale_claims(conn, run_id, iid)

                        # Cross-check: worker claims empty_batch but queue has work
                        if exit_reason == "empty_batch":
                            progress = db.get_queue_progress(conn, run_id)
                            remaining = progress["total"] - progress["completed"] - progress["errored"]
                            if remaining > 0:
                                logger.warning(
                                    "Worker claimed empty_batch but %d items remain — relaunching",
                                    remaining,
                                )
                                self._relaunch_slot(
                                    ec2, conn, run_id, run_tag, priority, ntee_filter,
                                    options, slot, slot_idx, "empty_batch_mismatch",
                                )
                                continue

                        slot["status"] = "done"
                        continue

                    # No exit_reason — classify the termination
                    classification = self._classify_terminated(ec2, iid)
                    self.stdout.write(
                        f"[slot {slot_idx}] Instance {iid} {state} "
                        f"(classification={classification})\n"
                    )

                    self._pull_worker_log(iid, run_tag)
                    db.reclaim_stale_claims(conn, run_id, iid)

                    progress = db.get_queue_progress(conn, run_id)
                    remaining = progress["total"] - progress["completed"] - progress["errored"]
                    if remaining == 0:
                        slot["status"] = "done"
                        continue

                    slot["consecutive_failures"] += 1
                    self._relaunch_slot(
                        ec2, conn, run_id, run_tag, priority, ntee_filter,
                        options, slot, slot_idx, classification,
                    )
                    continue

                # B7: detect failed code-deploy / worker-start
                if not slot.get("deploy_ok"):
                    dstatus = self._check_deploy_status(iid)
                    if dstatus in ("Failed", "Cancelled", "TimedOut"):
                        self.stderr.write(
                            f"[slot {slot_idx}] Worker deploy {dstatus} on {iid}. "
                            f"Terminating + relaunching.\n"
                        )
                        self._safe_terminate(ec2, iid)
                        db.reclaim_stale_claims(conn, run_id, iid)
                        slot["consecutive_failures"] += 1
                        self._relaunch_slot(
                            ec2, conn, run_id, run_tag, priority, ntee_filter,
                            options, slot, slot_idx, "deploy_failed",
                        )
                        continue
                    if dstatus == "Success":
                        slot["deploy_ok"] = True

                # 3. Check heartbeat (only if DB is healthy)
                if db_healthy:
                    heartbeat_age = db.get_heartbeat_age(conn, iid, run_id)

                    if heartbeat_age is not None:
                        if heartbeat_age > HEARTBEAT_STALE_MINUTES * 60:
                            self.stderr.write(
                                f"[slot {slot_idx}] Worker stale on {iid} "
                                f"(heartbeat age={heartbeat_age:.0f}s). Terminating.\n"
                            )
                            self._pull_worker_log(iid, run_tag)
                            self._safe_terminate(ec2, iid)
                            db.reclaim_stale_claims(conn, run_id, iid)
                            slot["consecutive_failures"] += 1
                            self._relaunch_slot(
                                ec2, conn, run_id, run_tag, priority, ntee_filter,
                                options, slot, slot_idx, "hang",
                            )
                    else:
                        # No heartbeat row — legacy worker fallback
                        last_activity = self._get_worker_last_activity(conn, run_id, iid)
                        oldest_open = self._get_worker_open_claim_age(conn, run_id, iid)
                        heartbeat = last_activity or oldest_open
                        stale = (
                            heartbeat is not None
                            and (time.time() - heartbeat.timestamp()) > HEARTBEAT_LEGACY_STALE_MINUTES * 60
                            and oldest_open is not None
                        )
                        if stale:
                            self.stderr.write(
                                f"[slot {slot_idx}] Worker stale on {iid} "
                                f"(legacy fallback). Terminating.\n"
                            )
                            self._safe_terminate(ec2, iid)
                            db.reclaim_stale_claims(conn, run_id, iid)
                            slot["consecutive_failures"] += 1
                            self._relaunch_slot(
                                ec2, conn, run_id, run_tag, priority, ntee_filter,
                                options, slot, slot_idx, "hang",
                            )

                # Reset consecutive_failures on progress
                progress = db.get_queue_progress(conn, run_id)
                current_completed = progress["completed"]
                if current_completed > slot_last_completed[slot_idx]:
                    slot["consecutive_failures"] = 0
                    slot_last_completed[slot_idx] = current_completed

            # Aggregate progress
            progress = db.get_queue_progress(conn, run_id)
            completed = progress["completed"]
            errored = progress["errored"]
            total = progress["total"]

            self.stdout.write(
                f"  Progress: {completed:,} ok, {errored:,} err, "
                f"{total:,} total ({elapsed/60:.0f}m elapsed)\n"
            )
            self._update_job_progress(job_id, completed, total)

            try:
                conn.rollback()
            except Exception:
                pass

            # All work done?
            if completed + errored >= total:
                self.stdout.write("All work items processed.\n")
                self._terminate_all(ec2, slots)
                break

            # All slots dead/abandoned?
            active = [s for s in slots if s["status"] == "running"]
            if not active:
                self.stderr.write("All worker slots exhausted or done. Stopping.\n")
                final_status = "failed"
                break

        # Cleanup and finalize
        self._terminate_all(ec2, slots)

        progress = db.get_queue_progress(conn, run_id)
        final_completed = progress["completed"]
        final_errored = progress["errored"]
        final_total = progress["total"]
        incomplete = final_total - (final_completed + final_errored)

        if incomplete > 0:
            self.stderr.write(
                f"Run ended with {incomplete} item(s) not completed "
                f"(completed={final_completed}, errored={final_errored}, "
                f"total={final_total}). Leaving work_queue intact for diagnosis.\n"
            )
            if final_status not in ("cancelled", "timeout"):
                final_status = "failed"
        else:
            db.cleanup_work_queue(conn, run_id)

        # Store log paths in stats_json for dashboard
        log_paths = {}
        for slot in slots:
            iid = slot.get("instance_id")
            if iid and run_tag:
                log_paths[iid] = f"logs/parse/{run_tag}/{iid}/worker.log"

        final_stats = {
            "succeeded": final_completed,
            "failed": final_errored,
            "total": final_completed + final_errored,
            "log_paths": log_paths,
        }

        status = db.get_run_status(conn, run_tag)
        if status and not status.get("finished_at"):
            exit_reason = db.get_exit_reason(conn, run_id)
            if not exit_reason:
                if final_status == "cancelled":
                    exit_reason = "cancelled"
                elif final_status == "timeout":
                    exit_reason = "max_hours"
                elif final_status == "completed":
                    exit_reason = "empty_batch"
                else:
                    exit_reason = "unknown"
            db.finish_run_with_reason(conn, status["id"], final_stats, exit_reason)

        # Map internal final_status to a Job status
        if final_status == "cancelled":
            job_status = "cancelled"
        elif final_status in ("failed", "timeout") or incomplete > 0:
            job_status = "failed"
        else:
            job_status = "completed"
        self._update_job_progress(job_id, final_completed, eligible_count, job_status)

        self.stdout.write(
            f"Parse run finished: status={final_status}, "
            f"completed={final_completed}, errored={final_errored}, "
            f"incomplete={incomplete}.\n"
        )

    def _launch_with_capacity_retry(self, ec2, conn, run_id, run_tag, priority, ntee_filter, options, slot_index=0):
        """Wrap _launch_and_start with AZ rotation and capacity retry.

        Tries all AZs starting from the slot's preferred AZ. Only sleeps
        after exhausting every AZ. Returns instance_id or None.
        """
        from botocore.exceptions import ClientError

        capacity_wait_hours = options.get("capacity_wait_hours", 1)
        max_retries = max(1, (capacity_wait_hours * 3600) // CAPACITY_RETRY_INTERVAL)
        num_azs = len(SUBNET_AZ)
        start_az = slot_index % num_azs

        self.stdout.write(f"[slot {slot_index}] Starting launch (preferred AZ: {SUBNET_AZ[start_az][1]})...\n")
        for attempt in range(max_retries + 1):
            for az_offset in range(num_azs):
                if self._shutdown_requested:
                    self.stdout.write(f"[slot {slot_index}] Shutdown requested.\n")
                    return None

                az_idx = (start_az + az_offset) % num_azs
                subnet_id, az_name = SUBNET_AZ[az_idx]

                try:
                    self.stdout.write(f"[slot {slot_index}] Trying {az_name}...\n")
                    return self._launch_and_start(
                        ec2, conn, run_id, run_tag, priority, ntee_filter, options,
                        slot_index=slot_index, subnet_id=subnet_id,
                    )
                except (ClientError, CommandError) as e:
                    err_code = ""
                    if isinstance(e, ClientError):
                        err_code = e.response.get("Error", {}).get("Code", "")
                    is_retryable = err_code in (
                        "InsufficientInstanceCapacity",
                        "SpotMaxPriceTooLow",
                        "MaxSpotInstanceCountExceeded",
                        "Unsupported",
                    )
                    if not is_retryable:
                        self.stderr.write(f"[slot {slot_index}] Launch failed in {az_name}: {e}\n")
                        raise

                    is_quota = err_code == "MaxSpotInstanceCountExceeded"
                    if is_quota:
                        running_count = sum(
                            1 for s in self._slots
                            if s.get("instance_id") and s.get("status") == "running"
                        )
                        if running_count > 0:
                            self.stdout.write(
                                f"[slot {slot_index}] Spot vCPU quota reached "
                                f"({running_count} instance(s) already running)\n"
                            )
                            return None
                        self.stdout.write(
                            f"[slot {slot_index}] Spot vCPU quota rejected but no instances running "
                            f"— quota may be propagating, will retry\n"
                        )
                        break  # skip remaining AZs, go to sleep/retry

                    self.stdout.write(f"[slot {slot_index}] No capacity in {az_name} ({err_code})\n")

            if attempt >= max_retries:
                self.stderr.write(
                    f"[slot {slot_index}] No spot capacity in any AZ after {capacity_wait_hours}h. Giving up.\n"
                )
                return None

            self.stdout.write(
                f"[slot {slot_index}] All AZs exhausted (round {attempt + 1}/{max_retries + 1}). "
                f"Retrying in {CAPACITY_RETRY_INTERVAL}s...\n"
            )
            slept = 0
            while slept < CAPACITY_RETRY_INTERVAL:
                if self._shutdown_requested:
                    self.stdout.write(f"[slot {slot_index}] Shutdown during capacity wait.\n")
                    return None
                time.sleep(min(30, CAPACITY_RETRY_INTERVAL - slept))
                slept += 30

    def _launch_and_start(self, ec2, conn, run_id: int, run_tag: str, priority: list[str], ntee_filter: str | None, options: dict, slot_index: int = 0, subnet_id: str | None = None) -> str:
        """Launch a spot instance and start the worker. Returns instance_id."""
        existing = self._find_instance(ec2, run_tag, slot_index=slot_index)
        if existing:
            self.stdout.write(f"[slot {slot_index}] Terminating existing instance {existing}\n")
            ec2.terminate_instances(InstanceIds=[existing])
            self._wait_for_termination(ec2, existing)
            self.stdout.write(f"[slot {slot_index}] Existing instance terminated\n")

        instance_id = self._launch_instance(ec2, run_tag, options, slot_index=slot_index, subnet_id=subnet_id)
        mode = "on-demand" if options["no_spot"] else "spot"
        self.stdout.write(f"[slot {slot_index}] Launched {mode} instance {instance_id}\n")

        time.sleep(5)
        self.stdout.write(f"[slot {slot_index}] Waiting for running state...\n")
        self._wait_for_running(ec2, instance_id)
        self.stdout.write(f"[slot {slot_index}] Instance {instance_id} is running\n")

        self.stdout.write(f"[slot {slot_index}] Waiting for SSM agent...\n")
        self._wait_for_ssm(instance_id)
        self.stdout.write(f"[slot {slot_index}] SSM connected on {instance_id}\n")

        self._start_worker(ec2, instance_id, run_id, priority, ntee_filter, options)
        self.stdout.write(f"[slot {slot_index}] Worker started on {instance_id}\n")
        return instance_id

    def _safe_terminate(self, ec2, instance_id: str) -> None:
        """Terminate instance if still running."""
        state = self._get_instance_state(ec2, instance_id)
        if state not in ("terminated", "shutting-down"):
            ec2.terminate_instances(InstanceIds=[instance_id])
            self.stdout.write(f"Terminated instance {instance_id}\n")

    def _relaunch_slot(self, ec2, conn, run_id, run_tag, priority, ntee_filter,
                       options, slot, slot_idx, classification):
        """Decide whether to relaunch a slot based on consecutive_failures."""
        if slot["consecutive_failures"] >= MAX_CONSECUTIVE_FAILURES:
            slot["status"] = "abandoned"
            self.stderr.write(
                f"[slot {slot_idx}] Abandoned after {MAX_CONSECUTIVE_FAILURES} "
                f"consecutive failures (last: {classification})\n"
            )
            return

        self.stdout.write(
            f"[slot {slot_idx}] Relaunching (failures={slot['consecutive_failures']}"
            f"/{MAX_CONSECUTIVE_FAILURES}, reason={classification})...\n"
        )
        new_id = self._launch_with_capacity_retry(
            ec2, conn, run_id, run_tag, priority, ntee_filter, options,
            slot_index=slot_idx,
        )
        if new_id:
            slot["instance_id"] = new_id
            slot["status"] = "running"
            slot["deploy_ok"] = False
            self._update_instance_ids(conn, run_id, self._slots)
        else:
            slot["status"] = "capacity_exhausted"

    def _classify_terminated(self, ec2, instance_id: str) -> str:
        """Classify why a terminated instance died: spot_reclaim or crash_or_oom."""
        from botocore.exceptions import ClientError

        try:
            resp = ec2.describe_instances(InstanceIds=[instance_id])
            reservations = resp.get("Reservations", [])
            if not reservations:
                return "crash_or_oom"
            inst = reservations[0]["Instances"][0]
            lifecycle = inst.get("InstanceLifecycle", "")
            if lifecycle != "spot":
                return "crash_or_oom"

            # Check for spot interruption
            try:
                sir_resp = ec2.describe_spot_instance_requests(
                    Filters=[{"Name": "instance-id", "Values": [instance_id]}]
                )
                for req in sir_resp.get("SpotInstanceRequests", []):
                    status_code = req.get("Status", {}).get("Code", "")
                    if "instance-terminated" in status_code:
                        return "spot_reclaim"
            except ClientError:
                pass

            return "crash_or_oom"
        except Exception:
            return "crash_or_oom"

    def _pull_worker_log(self, instance_id: str, run_tag: str) -> None:
        """Pull worker log from instance via SSM before termination. Best-effort."""
        if not run_tag or not config.validate_run_tag(run_tag):
            logger.warning("invalid or missing run_tag for log pull")
            return
        try:
            import boto3

            ssm = boto3.client("ssm", region_name="us-east-1")
            s3_path = f"s3://{LOG_S3_BUCKET}/logs/parse/{run_tag}/{instance_id}/worker.log"
            ssm.send_command(
                InstanceIds=[instance_id],
                DocumentName="AWS-RunShellScript",
                Parameters={
                    "commands": [f"aws s3 cp /var/log/docling-worker.log {s3_path}"],
                },
            )
            time.sleep(5)
        except Exception:
            logger.warning("SSM log pull failed for %s (best-effort)", instance_id)

    def _db_healthy(self, conn) -> bool:
        """Quick check that the orchestrator's own DB connection is working."""
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        except Exception:
            logger.warning("DB health check failed — skipping stale detection this cycle")
            try:
                conn.rollback()
            except Exception:
                pass
            return False

    def _launch_instance(self, ec2, run_tag: str, options: dict, slot_index: int = 0, subnet_id: str | None = None) -> str:
        import boto3

        ami_id = options.get("ami_id")
        if not ami_id:
            ssm = boto3.client("ssm", region_name="us-east-1")
            ami_resp = ssm.get_parameter(Name=SSM_AMI_PARAM)
            ami_id = ami_resp["Parameter"]["Value"]

        if subnet_id is None:
            subnet_id = SUBNET_AZ[slot_index % len(SUBNET_AZ)][0]
        kwargs = dict(
            ImageId=ami_id,
            InstanceType=options["instance_type"],
            MinCount=1,
            MaxCount=1,
            IamInstanceProfile={"Name": IAM_PROFILE_NAME},
            SubnetId=subnet_id,
            SecurityGroupIds=[SECURITY_GROUP_ID],
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "Name", "Value": f"docling-worker-{run_tag}-{slot_index}"},
                        {"Key": "Project", "Value": "lavandula"},
                        {"Key": "Purpose", "Value": INSTANCE_TAG_PURPOSE},
                        {"Key": "Slot", "Value": str(slot_index)},
                    ],
                }
            ],
        )
        if not options["no_spot"]:
            kwargs["InstanceMarketOptions"] = {"MarketType": "spot"}

        response = ec2.run_instances(**kwargs)
        return response["Instances"][0]["InstanceId"]

    def _find_instance(self, ec2, run_tag: str, slot_index: int | None = None) -> str | None:
        """Find running/pending instance tagged for docling-parse."""
        filters = [
            {"Name": "tag:Purpose", "Values": [INSTANCE_TAG_PURPOSE]},
            {"Name": "instance-state-name", "Values": ["running", "pending"]},
        ]
        if slot_index is not None:
            filters.append({"Name": "tag:Name", "Values": [f"docling-worker-{run_tag}-{slot_index}"]})
            filters.append({"Name": "tag:Slot", "Values": [str(slot_index)]})
        else:
            filters.append({"Name": "tag:Name", "Values": [f"docling-worker-{run_tag}"]})
        response = ec2.describe_instances(Filters=filters)
        for reservation in response.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                return instance["InstanceId"]
        return None

    def _wait_for_running(self, ec2, instance_id: str, timeout: int = 300) -> None:
        """Wait for instance to reach 'running' state."""
        start = time.time()
        while time.time() - start < timeout:
            state = self._get_instance_state(ec2, instance_id)
            if state == "running":
                return
            if state in ("terminated", "shutting-down"):
                raise CommandError(f"Instance {instance_id} terminated before reaching running state")
            time.sleep(10)
        raise CommandError(f"Instance {instance_id} did not reach running state within {timeout}s")

    def _wait_for_ssm(self, instance_id: str, timeout: int = 180) -> None:
        """Wait for SSM agent to register the instance."""
        import boto3

        ssm = boto3.client("ssm", region_name="us-east-1")
        start = time.time()
        while time.time() - start < timeout:
            resp = ssm.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
            )
            if resp.get("InstanceInformationList"):
                return
            time.sleep(10)
        raise CommandError(f"SSM agent on {instance_id} did not register within {timeout}s")

    def _wait_for_termination(self, ec2, instance_id: str, timeout: int = 120) -> None:
        """Wait for instance to terminate."""
        start = time.time()
        while time.time() - start < timeout:
            state = self._get_instance_state(ec2, instance_id)
            if state == "terminated":
                return
            time.sleep(5)

    def _get_instance_state(self, ec2, instance_id: str) -> str:
        from botocore.exceptions import ClientError

        try:
            response = ec2.describe_instances(InstanceIds=[instance_id])
        except ClientError as e:
            if "InvalidInstanceID.NotFound" in str(e):
                return "pending"
            raise
        reservations = response.get("Reservations", [])
        if not reservations:
            return "terminated"
        instances = reservations[0].get("Instances", [])
        if not instances:
            return "terminated"
        return instances[0]["State"]["Name"]

    def _start_worker(self, ec2, instance_id: str, run_id: int, priority: list[str], ntee_filter: str | None, options: dict) -> None:
        """Start the worker process on the GPU instance via SSM."""
        import boto3

        from lavandula.common.secrets import get_secret

        host = get_secret("rds-endpoint")
        port = get_secret("rds-port")
        database = get_secret("rds-database")

        max_docs_flag = f" --max-docs {options['max_docs']}" if options["max_docs"] else ""
        ntee_flag = f" --ntee {ntee_filter}" if ntee_filter else ""
        worker_id_flag = f" --worker-id {instance_id}"
        worker_cmd = (
            f"/opt/docling/bin/python -m lavandula.parse.worker "
            f"--run-id {run_id} "
            f"--host {host} "
            f"--port {port} "
            f"--database {database} "
            f"--priority {','.join(priority)} "
            f"--batch-size {options['batch_size']}"
            f"{max_docs_flag}"
            f"{ntee_flag}"
            f"{worker_id_flag}"
        )

        # Deploy worker code and start as a background process
        ssm = boto3.client("ssm", region_name="us-east-1")

        # Step 1: Deploy code from S3
        deploy_commands = [
            "#!/bin/bash",
            "set -ex",
            "cd /opt/docling",
            "aws s3 cp s3://lavandula-nonprofit-collaterals/deploy/worker-code.tar.gz /tmp/worker-code.tar.gz",
            "tar -xzf /tmp/worker-code.tar.gz -C /opt/docling/lib/python3.10/site-packages/",
            f"nohup {worker_cmd} > /var/log/docling-worker.log 2>&1 &",
        ]

        resp = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={
                "commands": deploy_commands,
            },
        )
        try:
            self._deploy_cmds[instance_id] = resp["Command"]["CommandId"]
        except (KeyError, TypeError, AttributeError):
            pass
        self.stdout.write(f"Worker started via SSM on {instance_id}\n")

    def _check_deploy_status(self, instance_id: str) -> str | None:
        """Best-effort SSM deploy/start status for an instance.

        Returns the SSM command Status ('Success'/'Failed'/'InProgress'/...),
        or None if unknown (no command id recorded, or ssm:GetCommandInvocation
        not permitted). Callers must treat None as "no signal", never as failure.
        """
        cmd_id = self._deploy_cmds.get(instance_id)
        if not cmd_id:
            return None
        import boto3

        ssm = boto3.client("ssm", region_name="us-east-1")
        try:
            inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
            return inv.get("Status")
        except Exception:
            return None

    def _delete_error_rows(self, conn, priority: list[str]) -> None:
        """Delete error rows so they can be retried."""
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM lava_parse.tables
                    WHERE content_sha256 IN (
                        SELECT d.content_sha256
                        FROM lava_parse.documents d
                        JOIN lava_corpus.corpus c ON c.content_sha256 = d.content_sha256
                        WHERE d.error IS NOT NULL
                          AND c.classification = ANY(%(priority)s)
                    )
                    """,
                    {"priority": priority},
                )
                cur.execute(
                    """
                    DELETE FROM lava_parse.sections
                    WHERE content_sha256 IN (
                        SELECT d.content_sha256
                        FROM lava_parse.documents d
                        JOIN lava_corpus.corpus c ON c.content_sha256 = d.content_sha256
                        WHERE d.error IS NOT NULL
                          AND c.classification = ANY(%(priority)s)
                    )
                    """,
                    {"priority": priority},
                )
                cur.execute(
                    """
                    DELETE FROM lava_parse.documents
                    WHERE content_sha256 IN (
                        SELECT d.content_sha256
                        FROM lava_parse.documents d
                        JOIN lava_corpus.corpus c ON c.content_sha256 = d.content_sha256
                        WHERE d.error IS NOT NULL
                          AND c.classification = ANY(%(priority)s)
                    )
                    """,
                    {"priority": priority},
                )
        self.stdout.write("Deleted error rows for retry.\n")

    def _delete_old_version_rows(self, conn, priority: list[str], min_version: str) -> None:
        """Delete rows parsed by older versions for reparse."""
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM lava_parse.tables
                    WHERE content_sha256 IN (
                        SELECT d.content_sha256
                        FROM lava_parse.documents d
                        JOIN lava_corpus.corpus c ON c.content_sha256 = d.content_sha256
                        WHERE d.parse_version < %(min_version)s
                          AND c.classification = ANY(%(priority)s)
                    )
                    """,
                    {"min_version": min_version, "priority": priority},
                )
                cur.execute(
                    """
                    DELETE FROM lava_parse.sections
                    WHERE content_sha256 IN (
                        SELECT d.content_sha256
                        FROM lava_parse.documents d
                        JOIN lava_corpus.corpus c ON c.content_sha256 = d.content_sha256
                        WHERE d.parse_version < %(min_version)s
                          AND c.classification = ANY(%(priority)s)
                    )
                    """,
                    {"min_version": min_version, "priority": priority},
                )
                cur.execute(
                    """
                    DELETE FROM lava_parse.documents
                    WHERE content_sha256 IN (
                        SELECT d.content_sha256
                        FROM lava_parse.documents d
                        JOIN lava_corpus.corpus c ON c.content_sha256 = d.content_sha256
                        WHERE d.parse_version < %(min_version)s
                          AND c.classification = ANY(%(priority)s)
                    )
                    """,
                    {"min_version": min_version, "priority": priority},
                )
        self.stdout.write(f"Deleted rows with version < {min_version} for reparse.\n")

    def _get_conn(self):
        """Get a psycopg2 connection using SSM secrets."""
        from lavandula.common.secrets import get_secret

        import boto3

        host = get_secret("rds-endpoint")
        port = int(get_secret("rds-port"))
        database = get_secret("rds-database")

        rds_client = boto3.client("rds", region_name="us-east-1")

        def _get_token():
            return rds_client.generate_db_auth_token(
                DBHostname=host, Port=port, DBUsername="research_app", Region="us-east-1"
            )

        return db.get_connection(
            host=host,
            port=port,
            database=database,
            user="research_app",
            iam_token_fn=_get_token,
        )

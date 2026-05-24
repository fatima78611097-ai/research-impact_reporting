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
import sys
import time

from django.core.management.base import BaseCommand, CommandError

from lavandula.parse import config, db


AVG_PAGES_PER_DOC = 30
SECONDS_PER_PAGE = 0.49
SPOT_RATE_PER_HOUR = 0.60
ONDEMAND_RATE_PER_HOUR = 0.98
POLL_INTERVAL_SECONDS = 60
HEARTBEAT_STALE_MINUTES = 20
MAX_RELAUNCH_ATTEMPTS = 3
SSM_AMI_PARAM = "/cloud2.lavandulagroup.com/docling-ami-id"
SUBNET_ID = "subnet-0e2008e48d602e945"
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

    def handle(self, *args, **options):
        run_tag = options["run_tag"]

        if not config.validate_run_tag(run_tag):
            raise CommandError("run_tag must match ^[a-zA-Z0-9_-]{1,64}$")

        if not (1 <= options["max_hours"] <= 24):
            raise CommandError("--max-hours must be 1-24")

        if not (10 <= options["batch_size"] <= 5000):
            raise CommandError("--batch-size must be 10-5000")

        priority_values = [v.strip() for v in options["priority"].split(",")]
        if not config.validate_priority_values(priority_values):
            raise CommandError("--priority values must match ^[a-z_]+$")

        ntee_filter = options.get("ntee")
        if ntee_filter and not all(c.isalnum() or c == '%' for c in ntee_filter):
            raise CommandError("--ntee must be alphanumeric with optional trailing %")

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

        self.stdout.write(
            f"Eligible documents: {count:,}\n"
            f"Estimated pages:    ~{est_pages:,.0f}\n"
            f"Estimated GPU time: ~{est_hours:.1f} hours\n"
            f"Estimated cost:     ~${est_cost:.0f} ({pricing_label})\n"
            f"Priority filter:    {', '.join(priority)}\n"
            f"NTEE filter:        {ntee_filter or 'all'}\n"
            f"Instance type:      {options['instance_type']}\n"
        )

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
        instance_id = self._find_instance(ec2, run_tag)

        if not instance_id:
            self.stdout.write("No running instance found for this run tag.\n")
            return

        ec2.terminate_instances(InstanceIds=[instance_id])
        self.stdout.write(f"Terminated instance {instance_id}\n")

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
            db.release_orchestrator_lock(conn)
            conn.close()

    def _execute_run(self, conn, run_tag: str, priority: list[str], ntee_filter: str | None, options: dict) -> None:
        import boto3

        ec2 = boto3.client("ec2", region_name="us-east-1")

        run_config = {
            "priority": priority,
            "ntee_filter": ntee_filter,
            "instance_type": options["instance_type"],
            "batch_size": options["batch_size"],
            "max_hours": options["max_hours"],
            "retry_errors": options["retry_errors"],
            "reparse": options["reparse"],
            "min_version": options.get("min_version"),
        }

        try:
            run_id = db.create_parse_run(conn, run_tag, run_config)
        except db.RunTagConflict as e:
            raise CommandError(str(e))

        self.stdout.write(f"Parse run {run_id} created/resumed (tag: {run_tag})\n")

        if options["retry_errors"]:
            self._delete_error_rows(conn, priority)
        if options["reparse"] and options.get("min_version"):
            self._delete_old_version_rows(conn, priority, options["min_version"])

        start_time = time.time()
        max_seconds = options["max_hours"] * 3600
        relaunch_count = 0

        instance_id = self._launch_and_start(ec2, conn, run_id, run_tag, priority, ntee_filter, options)

        last_stats_update = time.time()
        last_total = 0

        while True:
            time.sleep(POLL_INTERVAL_SECONDS)

            elapsed = time.time() - start_time
            if elapsed >= max_seconds:
                self.stdout.write(f"Max hours ({options['max_hours']}) reached. Terminating.\n")
                self._safe_terminate(ec2, instance_id)
                break

            # Check if worker reported completion
            status = db.get_run_status(conn, run_tag)
            if status and status.get("finished_at"):
                self.stdout.write("Worker reported completion.\n")
                self._safe_terminate(ec2, instance_id)
                break

            # Check instance state — spot interruption detection
            state = self._get_instance_state(ec2, instance_id)
            if state in ("terminated", "shutting-down"):
                self.stdout.write(f"Instance {instance_id} terminated (spot reclaimed or crash).\n")

                # Check if there's still work to do
                remaining = db.get_eligible_count(conn, priority)
                if remaining == 0:
                    self.stdout.write("No remaining work. Run complete.\n")
                    break

                # Attempt relaunch
                relaunch_count += 1
                if relaunch_count > MAX_RELAUNCH_ATTEMPTS:
                    self.stderr.write(
                        f"Exceeded max relaunch attempts ({MAX_RELAUNCH_ATTEMPTS}). "
                        f"Stopping. {remaining:,} docs remain.\n"
                    )
                    break

                self.stdout.write(
                    f"Relaunching (attempt {relaunch_count}/{MAX_RELAUNCH_ATTEMPTS}, "
                    f"{remaining:,} docs remaining)...\n"
                )
                instance_id = self._launch_and_start(ec2, conn, run_id, run_tag, priority, ntee_filter, options)
                last_stats_update = time.time()
                continue

            # Heartbeat: detect stale worker (crash without instance termination)
            stats = status.get("stats_json") if status else None
            current_total = 0
            if stats:
                if isinstance(stats, str):
                    stats = json.loads(stats)
                current_total = stats.get("total", 0)
                self.stdout.write(
                    f"  Progress: {stats.get('succeeded', 0):,} ok, "
                    f"{stats.get('failed', 0):,} err, "
                    f"{current_total:,} total "
                    f"({elapsed/60:.0f}m elapsed)\n"
                )

            if current_total > last_total:
                last_stats_update = time.time()
                last_total = current_total
            elif (time.time() - last_stats_update) > HEARTBEAT_STALE_MINUTES * 60:
                self.stderr.write(
                    f"Worker stale — no progress in {HEARTBEAT_STALE_MINUTES} minutes. "
                    f"Terminating instance.\n"
                )
                self._safe_terminate(ec2, instance_id)

                # Treat as crash, attempt relaunch
                remaining = db.get_eligible_count(conn, priority)
                if remaining == 0:
                    break
                relaunch_count += 1
                if relaunch_count > MAX_RELAUNCH_ATTEMPTS:
                    self.stderr.write(f"Exceeded max relaunch attempts. Stopping.\n")
                    break

                self.stdout.write(f"Relaunching after stale worker...\n")
                instance_id = self._launch_and_start(ec2, conn, run_id, run_tag, priority, ntee_filter, options)
                last_stats_update = time.time()
                continue

        # Mark run finished if not already
        status = db.get_run_status(conn, run_tag)
        if status and not status.get("finished_at"):
            db.finish_run(conn, status["id"], status.get("stats_json") or {})

        self.stdout.write("Parse run complete.\n")

    def _launch_and_start(self, ec2, conn, run_id: int, run_tag: str, priority: list[str], ntee_filter: str | None, options: dict) -> str:
        """Launch a spot instance and start the worker. Returns instance_id."""
        # Terminate any existing instance for this purpose
        existing = self._find_instance(ec2, run_tag)
        if existing:
            self.stdout.write(f"Terminating existing instance {existing}\n")
            ec2.terminate_instances(InstanceIds=[existing])
            self._wait_for_termination(ec2, existing)

        instance_id = self._launch_instance(ec2, run_tag, options)
        mode = "on-demand" if options["no_spot"] else "spot"
        self.stdout.write(f"Launched {mode} instance {instance_id}\n")

        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE lava_parse.parse_runs SET instance_id = %s WHERE id = %s",
                    (instance_id, run_id),
                )

        time.sleep(5)  # EC2 eventual consistency — wait before polling
        self._wait_for_running(ec2, instance_id)
        self.stdout.write(f"Instance {instance_id} is running\n")

        self._wait_for_ssm(instance_id)
        self.stdout.write(f"SSM agent connected on {instance_id}\n")

        self._start_worker(ec2, instance_id, run_id, priority, ntee_filter, options)
        return instance_id

    def _safe_terminate(self, ec2, instance_id: str) -> None:
        """Terminate instance if still running."""
        state = self._get_instance_state(ec2, instance_id)
        if state not in ("terminated", "shutting-down"):
            ec2.terminate_instances(InstanceIds=[instance_id])
            self.stdout.write(f"Terminated instance {instance_id}\n")

    def _launch_instance(self, ec2, run_tag: str, options: dict) -> str:
        import boto3

        ssm = boto3.client("ssm", region_name="us-east-1")
        ami_resp = ssm.get_parameter(Name=SSM_AMI_PARAM)
        ami_id = ami_resp["Parameter"]["Value"]

        kwargs = dict(
            ImageId=ami_id,
            InstanceType=options["instance_type"],
            MinCount=1,
            MaxCount=1,
            IamInstanceProfile={"Name": IAM_PROFILE_NAME},
            SubnetId=SUBNET_ID,
            SecurityGroupIds=[SECURITY_GROUP_ID],
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "Name", "Value": f"docling-worker-{run_tag}"},
                        {"Key": "Project", "Value": "lavandula"},
                        {"Key": "Purpose", "Value": INSTANCE_TAG_PURPOSE},
                    ],
                }
            ],
        )
        if not options["no_spot"]:
            kwargs["InstanceMarketOptions"] = {"MarketType": "spot"}

        response = ec2.run_instances(**kwargs)
        return response["Instances"][0]["InstanceId"]

    def _find_instance(self, ec2, run_tag: str) -> str | None:
        """Find running/pending instance tagged for docling-parse."""
        response = ec2.describe_instances(
            Filters=[
                {"Name": "tag:Purpose", "Values": [INSTANCE_TAG_PURPOSE]},
                {"Name": "instance-state-name", "Values": ["running", "pending"]},
            ]
        )
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

        ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={
                "commands": deploy_commands,
            },
        )
        self.stdout.write(f"Worker started via SSM on {instance_id}\n")

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

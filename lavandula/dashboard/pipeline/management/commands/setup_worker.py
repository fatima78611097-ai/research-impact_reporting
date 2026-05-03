from __future__ import annotations

import getpass
import socket
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import connections

from pipeline.models import Worker

PROJECT_ROOT = Path(__file__).resolve().parents[5]


class Command(BaseCommand):
    help = "Verify host connectivity and register as a pipeline worker"

    def add_arguments(self, parser):
        parser.add_argument("--display-name", default="")
        parser.add_argument("--phases", default="", help="Comma-separated phase list")
        parser.add_argument("--gpu", action="store_true")
        parser.add_argument("--skip-s3", action="store_true")

    def handle(self, *args, **options):
        user = getpass.getuser()
        if user == "root":
            self.stderr.write(self.style.ERROR("ERROR: Do not run as root. Use the application user."))
            return

        hostname = socket.gethostname()
        self.stdout.write(f"Host: {hostname}")
        self.stdout.write(f"User: {user}")

        for alias in ("default", "pipeline"):
            try:
                conn = connections[alias]
                conn.ensure_connection()
                self.stdout.write(self.style.SUCCESS(f"  RDS ({alias}): connected"))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"  RDS ({alias}): {e}"))
                return

        if not options["skip_s3"]:
            try:
                import boto3
                boto3.client("s3").head_bucket(Bucket="lavandula-nonprofit-collaterals")
                self.stdout.write(self.style.SUCCESS("  S3: accessible"))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"  S3: {e}"))
                return
        else:
            self.stdout.write("  S3: skipped")

        caps = {}
        if options["phases"]:
            caps["phases"] = [p.strip() for p in options["phases"].split(",") if p.strip()]
        if options["gpu"]:
            caps["gpu"] = True

        worker, created = Worker.objects.update_or_create(
            hostname=hostname,
            defaults={
                "display_name": options["display_name"],
                "capabilities": caps,
                "status": "offline",
                "is_active": True,
            },
        )
        action = "registered" if created else "re-registered"
        name = worker.display_name or hostname
        self.stdout.write(self.style.SUCCESS(f"  Worker {action}: {name}"))

        cwd = PROJECT_ROOT
        self.stdout.write("")
        self.stdout.write("Systemd service file (copy to /etc/systemd/system/lavandula-orchestrator.service):")
        self.stdout.write("-" * 60)
        self.stdout.write(f"""[Unit]
Description=Lavandula Pipeline Orchestrator
After=network.target

[Service]
Type=simple
User={user}
WorkingDirectory={cwd}
ExecStart=/usr/bin/python3 {cwd}/lavandula/dashboard/manage.py run_orchestrator
Restart=on-failure
RestartSec=10
Environment=PYTHONPATH={cwd}

[Install]
WantedBy=multi-user.target""")
        self.stdout.write("-" * 60)
        self.stdout.write("")
        self.stdout.write("Next steps:")
        self.stdout.write("  1. Copy the above to /etc/systemd/system/lavandula-orchestrator.service")
        self.stdout.write("  2. sudo systemctl daemon-reload")
        self.stdout.write("  3. sudo systemctl enable --now lavandula-orchestrator")

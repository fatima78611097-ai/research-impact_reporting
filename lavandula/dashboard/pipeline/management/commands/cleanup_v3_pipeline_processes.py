"""Clean up stale PipelineProcess rows for v3 phases migrated to Job queue.

Safe to run multiple times (idempotent). Run after deploying the v3 job queue code.

Usage:
    python3 manage.py cleanup_v3_pipeline_processes
"""
from django.core.management.base import BaseCommand

from pipeline.models import PipelineProcess
from pipeline.process_manager import _is_pid_alive_and_matches

_V3_PHASES = (
    "extract-context", "reclassify", "compare-classify",
    "resolve-disagree", "promote-classify",
)


class Command(BaseCommand):
    help = "Clean up stale PipelineProcess rows for v3 phases migrated to Job queue"

    def handle(self, *args, **options):
        cleaned = 0
        alive = 0
        for proc in PipelineProcess.objects.filter(name__in=_V3_PHASES, status="running"):
            if not proc.pid or not _is_pid_alive_and_matches(proc.pid, proc.name):
                proc.status = "stopped"
                proc.save(update_fields=["status"])
                self.stdout.write(f"Marked {proc.name} as stopped (PID {proc.pid} dead)")
                cleaned += 1
            else:
                self.stderr.write(
                    f"WARNING: {proc.name} PID {proc.pid} still alive — stop manually"
                )
                alive += 1

        if cleaned == 0 and alive == 0:
            self.stdout.write("No stale v3 PipelineProcess rows found.")
        else:
            self.stdout.write(f"Cleaned {cleaned}, skipped {alive} alive.")

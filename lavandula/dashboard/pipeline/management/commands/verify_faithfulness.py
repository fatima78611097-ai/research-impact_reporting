"""Verify faithfulness of LLM-extracted metrics and stories (Spec 0057).

Runs the deterministic grounding gate over all facts in an extraction run,
assigns verification tiers, and emits a per-run faithfulness score.

Usage:
    python3 manage.py verify_faithfulness <run_tag>
    python3 manage.py verify_faithfulness <run_tag> --dry-run
"""
from __future__ import annotations

import logging
import re

from django.core.management.base import BaseCommand
from sqlalchemy import text

from lavandula.common.db import make_app_engine
from lavandula.faithfulness.gate_runner import RunStats, verify_run
from lavandula.faithfulness.source_provider import DoclingSourceProvider

log = logging.getLogger(__name__)

_RUN_TAG_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class Command(BaseCommand):
    help = "Run faithfulness verification gate over LLM-extracted metrics and stories (Spec 0057)"

    def add_arguments(self, parser):
        parser.add_argument("run_tag", help="Run tag identifying the extraction run to verify")
        parser.add_argument("--dry-run", action="store_true", help="Show counts without modifying data")

    def handle(self, *args, **options):
        run_tag = options["run_tag"]
        dry_run = options["dry_run"]

        if not _RUN_TAG_RE.match(run_tag):
            self.stderr.write("Invalid run_tag: must be 1-64 alphanumeric/hyphen/underscore characters")
            raise SystemExit(1)

        engine = make_app_engine()

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT id FROM lava_vocab.extraction_runs WHERE run_tag = :tag"),
                {"tag": run_tag},
            ).fetchone()

        if not row:
            self.stderr.write(f"No extraction run found with tag {run_tag!r}")
            raise SystemExit(1)

        run_id = row[0]

        with engine.connect() as conn:
            metric_count = conn.execute(
                text("SELECT COUNT(*) FROM lava_vocab.llm_metrics WHERE run_id = :run_id"),
                {"run_id": run_id},
            ).scalar()
            story_count = conn.execute(
                text("SELECT COUNT(*) FROM lava_vocab.llm_stories WHERE run_id = :run_id"),
                {"run_id": run_id},
            ).scalar()

        self.stdout.write(f"Run {run_id} ({run_tag!r}): {metric_count} metrics, {story_count} stories")

        if dry_run:
            self.stdout.write("Dry run — no data modified.")
            return

        if metric_count == 0 and story_count == 0:
            self.stdout.write("No facts to verify.")
            return

        provider = DoclingSourceProvider(engine)

        def progress(stats: RunStats):
            self.stdout.write(
                f"  Progress: {stats.docs_processed} docs, "
                f"{stats.metrics_verified}/{stats.total_metrics} metrics verified, "
                f"{stats.stories_verified}/{stats.total_stories} stories verified "
                f"({stats.elapsed_s:.0f}s)"
            )

        self.stdout.write("Running faithfulness gate...")
        stats = verify_run(engine, provider, run_id, progress_callback=progress)

        self.stdout.write(
            f"\nFaithfulness verification complete ({stats.elapsed_s:.1f}s):\n"
            f"  Metrics: {stats.total_metrics} total\n"
            f"    Verified (Tier A):   {stats.metrics_verified} "
            f"({stats.metrics_grounding_rate:.1%})\n"
            f"    OCR (Tier B):        {stats.metrics_ocr}\n"
            f"    Pending:             {stats.metrics_pending}\n"
            f"    Quarantined (Tier C):{stats.metrics_quarantined}\n"
            f"  Stories: {stats.total_stories} total\n"
            f"    Verified (Tier A):   {stats.stories_verified} "
            f"({stats.stories_grounding_rate:.1%})\n"
            f"    OCR (Tier B):        {stats.stories_ocr}\n"
            f"    Pending:             {stats.stories_pending}\n"
            f"    Quarantined (Tier C):{stats.stories_quarantined}\n"
            f"  Docs: {stats.docs_processed} processed, "
            f"{stats.docs_missing_source} missing source"
        )

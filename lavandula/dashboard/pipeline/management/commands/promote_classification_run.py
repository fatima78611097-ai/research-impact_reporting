"""Promote a validated classification run to canonical corpus columns (Spec 0035).

Usage:
    python3 manage.py promote_classification_run --run-tag v3.1 --confirm
"""
from __future__ import annotations

import getpass
import json
import logging
import os
import socket

from django.core.management.base import BaseCommand
from django.utils import timezone
from sqlalchemy import text

from lavandula.common.db import make_app_engine
from lavandula.reports.taxonomy import material_type_to_legacy

log = logging.getLogger(__name__)

_SCHEMA = "lava_corpus"


class Command(BaseCommand):
    help = "Promote a classification run to canonical corpus columns"

    def add_arguments(self, parser):
        parser.add_argument("--run-tag", required=True, help="Run tag to promote")
        parser.add_argument("--confirm", action="store_true",
                            help="Required safety flag to confirm promotion")

    def handle(self, *args, **options):
        run_tag = options["run_tag"]
        confirm = options["confirm"]

        if not confirm:
            self.stderr.write(
                "Promotion requires --confirm flag. "
                "Review the comparison output before promoting."
            )
            return

        engine = make_app_engine()

        with engine.connect() as conn:
            row = conn.execute(text(
                f"SELECT id, finished_at FROM {_SCHEMA}.classification_runs "
                f"WHERE run_tag = :tag"
            ), {"tag": run_tag}).fetchone()

        if row is None:
            self.stderr.write(f"Run tag {run_tag!r} not found")
            return

        run_id = row[0]
        finished_at = row[1]
        if finished_at is None:
            self.stderr.write(f"Run {run_tag!r} has not finished yet")
            return

        operator = getpass.getuser()
        hostname = socket.gethostname()
        uid = os.getuid()
        ts = timezone.now().isoformat()

        with engine.connect() as conn:
            result_count = conn.execute(text(
                f"SELECT COUNT(*) FROM {_SCHEMA}.classification_results WHERE run_id = :rid"
            ), {"rid": run_id}).scalar()

        self.stdout.write(f"Promoting run {run_tag!r} ({result_count} results)...")

        # Update v3_* columns on corpus
        with engine.begin() as conn:
            conn.execute(text(f"""
                UPDATE {_SCHEMA}.corpus c SET
                    v3_material_type = cr.material_type,
                    v3_confidence = cr.confidence,
                    v3_reasoning = cr.reasoning,
                    v3_classified_at = now(),
                    v3_run_tag = :run_tag,
                    v3_classified_by = cr.classified_by
                FROM {_SCHEMA}.classification_results cr
                WHERE cr.run_id = :run_id
                  AND cr.content_sha256 = c.content_sha256
            """), {"run_id": run_id, "run_tag": run_tag})

        # Update canonical columns from classification_results (bulk)
        from lavandula.reports.taxonomy import _MATERIAL_TYPE_TO_LEGACY
        case_whens = " ".join(
            f"WHEN '{k}' THEN '{v}'" for k, v in _MATERIAL_TYPE_TO_LEGACY.items()
        )
        legacy_case = f"CASE cr.material_type {case_whens} ELSE 'other' END"

        with engine.begin() as conn:
            conn.execute(text(f"""
                UPDATE {_SCHEMA}.corpus c SET
                    material_type = cr.material_type,
                    material_group = cr.material_group,
                    event_type = cr.event_type,
                    classification = {legacy_case},
                    classification_confidence = cr.confidence,
                    reasoning = cr.reasoning,
                    classifier_model = CASE
                        WHEN cr.classified_by LIKE '%%:%%'
                        THEN split_part(cr.classified_by, ':', 2)
                        ELSE cr.classified_by
                    END
                FROM {_SCHEMA}.classification_results cr
                WHERE cr.run_id = :run_id
                  AND cr.content_sha256 = c.content_sha256
            """), {"run_id": run_id})

        # Log promotion
        notes = (
            f"Promoted by {operator}@{hostname} (uid={uid}) at {ts}"
        )
        with engine.begin() as conn:
            conn.execute(text(
                f"UPDATE {_SCHEMA}.classification_runs SET notes = :notes WHERE id = :rid"
            ), {"rid": run_id, "notes": notes})

        self.stdout.write(f"Promoted {result_count} classifications from run {run_tag!r}")
        self.stdout.write(f"Audit: {notes}")

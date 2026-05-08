"""Compare old vs new classifications from a reclassification run (Spec 0035).

Usage:
    python3 manage.py compare_classifications --run-tag v3.1
    python3 manage.py compare_classifications --run-tag v3.1 --show-reasoning
"""
from __future__ import annotations

import json
import logging
import re

from django.core.management.base import BaseCommand
from sqlalchemy import text

from lavandula.common.db import make_app_engine

log = logging.getLogger(__name__)

_SCHEMA = "lava_corpus"
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class Command(BaseCommand):
    help = "Compare old vs new classifications from a reclassification run"

    def add_arguments(self, parser):
        parser.add_argument("--run-tag", required=True, help="Run tag to compare")
        parser.add_argument("--show-reasoning", action="store_true",
                            help="Include reasoning in output")

    def handle(self, *args, **options):
        engine = make_app_engine()
        run_tag = options["run_tag"]
        show_reasoning = options["show_reasoning"]

        run_id = self._get_run_id(engine, run_tag)
        if run_id is None:
            self.stderr.write(f"Run tag {run_tag!r} not found")
            return

        self._print_summary(engine, run_id, run_tag)
        self._print_migration_matrix(engine, run_id)
        self._print_confidence_comparison(engine, run_id)
        self._print_classified_by_breakdown(engine, run_id)
        self._print_suppression_audit(engine, run_id, run_tag)

        if show_reasoning:
            self._print_reasoning_samples(engine, run_id)

    def _get_run_id(self, engine, run_tag):
        with engine.connect() as conn:
            row = conn.execute(text(
                f"SELECT id FROM {_SCHEMA}.classification_runs WHERE run_tag = :tag"
            ), {"tag": run_tag}).fetchone()
            return row[0] if row else None

    def _print_summary(self, engine, run_id, run_tag):
        with engine.connect() as conn:
            total = conn.execute(text(
                f"SELECT COUNT(*) FROM {_SCHEMA}.classification_results WHERE run_id = :rid"
            ), {"rid": run_id}).scalar()

            rule_count = conn.execute(text(
                f"SELECT COUNT(*) FROM {_SCHEMA}.classification_results "
                f"WHERE run_id = :rid AND classified_by LIKE 'rule:%'"
            ), {"rid": run_id}).scalar()

            llm_count = conn.execute(text(
                f"SELECT COUNT(*) FROM {_SCHEMA}.classification_results "
                f"WHERE run_id = :rid AND classified_by LIKE 'llm:%'"
            ), {"rid": run_id}).scalar()

        self.stdout.write(f"\n=== Classification Comparison: {run_tag} ===\n")
        self.stdout.write(f"Total: {total}")
        self.stdout.write(f"Rule-classified: {rule_count} ({rule_count/total*100:.1f}%)" if total else "Rule-classified: 0")
        self.stdout.write(f"LLM-classified: {llm_count} ({llm_count/total*100:.1f}%)" if total else "LLM-classified: 0")

    def _print_migration_matrix(self, engine, run_id):
        sql = (
            f"SELECT c.material_type AS old_type, cr.material_type AS new_type, COUNT(*) AS cnt "
            f"FROM {_SCHEMA}.classification_results cr "
            f"JOIN {_SCHEMA}.corpus c ON c.content_sha256 = cr.content_sha256 "
            f"WHERE cr.run_id = :rid "
            f"  AND c.material_type IS DISTINCT FROM cr.material_type "
            f"GROUP BY c.material_type, cr.material_type "
            f"ORDER BY cnt DESC "
            f"LIMIT 50"
        )
        with engine.connect() as conn:
            rows = conn.execute(text(sql), {"rid": run_id}).fetchall()

        if rows:
            self.stdout.write(f"\nChanges from current classification:")
            for old_type, new_type, cnt in rows:
                old_display = old_type or "(null)"
                new_display = new_type or "(null)"
                self.stdout.write(f"  {old_display:30s} → {new_display:30s}  {cnt:>6d}")
        else:
            self.stdout.write("\nNo classification changes detected.")

    def _print_confidence_comparison(self, engine, run_id):
        sql = (
            f"SELECT "
            f"  AVG(c.classification_confidence) AS avg_old, "
            f"  AVG(cr.confidence) AS avg_new, "
            f"  SUM(CASE WHEN c.classification_confidence < 0.8 THEN 1 ELSE 0 END) AS low_old, "
            f"  SUM(CASE WHEN cr.confidence < 0.8 THEN 1 ELSE 0 END) AS low_new "
            f"FROM {_SCHEMA}.classification_results cr "
            f"JOIN {_SCHEMA}.corpus c ON c.content_sha256 = cr.content_sha256 "
            f"WHERE cr.run_id = :rid"
        )
        with engine.connect() as conn:
            row = conn.execute(text(sql), {"rid": run_id}).fetchone()

        if row:
            avg_old = row[0] or 0
            avg_new = row[1] or 0
            low_old = row[2] or 0
            low_new = row[3] or 0
            self.stdout.write(f"\nConfidence comparison:")
            self.stdout.write(f"  Avg confidence (old): {avg_old:.3f}")
            self.stdout.write(f"  Avg confidence (new): {avg_new:.3f}")
            self.stdout.write(f"  Docs with confidence < 0.8 (old): {low_old:>6d}")
            self.stdout.write(f"  Docs with confidence < 0.8 (new): {low_new:>6d}")

    def _print_classified_by_breakdown(self, engine, run_id):
        sql = (
            f"SELECT classified_by, COUNT(*) "
            f"FROM {_SCHEMA}.classification_results "
            f"WHERE run_id = :rid "
            f"GROUP BY classified_by ORDER BY COUNT(*) DESC"
        )
        with engine.connect() as conn:
            rows = conn.execute(text(sql), {"rid": run_id}).fetchall()

        if rows:
            self.stdout.write(f"\nBreakdown by classified_by:")
            for cb, cnt in rows:
                self.stdout.write(f"  {cb or '(null)':40s}  {cnt:>6d}")

    def _print_suppression_audit(self, engine, run_id, run_tag):
        with engine.connect() as conn:
            row = conn.execute(text(
                f"SELECT stats_json FROM {_SCHEMA}.classification_runs "
                f"WHERE id = :rid"
            ), {"rid": run_id}).fetchone()

        if row and row[0]:
            stats = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            disagreements = stats.get("suppression_sample_disagreements", 0)
            warnings = stats.get("per_domain_suppression_warnings", [])
            self.stdout.write(f"\nSuppression audit:")
            self.stdout.write(f"  Disagreements: {disagreements}")
            if warnings:
                self.stdout.write("  Domain warnings:")
                for w in warnings:
                    self.stdout.write(f"    {w}")

    def _print_reasoning_samples(self, engine, run_id):
        sql = (
            f"SELECT cr.content_sha256, c.material_type, cr.material_type, "
            f"  cr.reasoning, cr.classified_by "
            f"FROM {_SCHEMA}.classification_results cr "
            f"JOIN {_SCHEMA}.corpus c ON c.content_sha256 = cr.content_sha256 "
            f"WHERE cr.run_id = :rid "
            f"  AND c.material_type IS DISTINCT FROM cr.material_type "
            f"ORDER BY random() LIMIT 20"
        )
        with engine.connect() as conn:
            rows = conn.execute(text(sql), {"rid": run_id}).fetchall()

        if rows:
            self.stdout.write(f"\nReasoning samples (changed classifications):")
            for sha, old_mt, new_mt, reasoning, cb in rows:
                clean_reasoning = _CONTROL_CHAR_RE.sub("", reasoning or "")
                self.stdout.write(
                    f"  {sha[:12]} {old_mt or '(null)':20s} → {new_mt or '(null)':20s} "
                    f"[{cb}] {clean_reasoning[:120]}"
                )

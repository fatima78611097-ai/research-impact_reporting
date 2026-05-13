"""Compare v3 classification results against corpus v2 classifications (Spec 0038).

Usage:
    python3 manage.py compare_classifications --run-tag v3.2
    python3 manage.py compare_classifications --run-tag v3.2 --state TX
    python3 manage.py compare_classifications --run-tag v3.2 --show-reasoning
    python3 manage.py compare_classifications --run-tag v3.2 --show-reasoning --limit 100
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict

from django.core.management.base import BaseCommand
from sqlalchemy import text

from lavandula.common.db import make_app_engine

log = logging.getLogger(__name__)

_SCHEMA = "lava_corpus"
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class Command(BaseCommand):
    help = "Compare v3 classification results against corpus v2 classifications"

    def add_arguments(self, parser):
        parser.add_argument("--run-tag", required=True, help="Run tag to compare")
        parser.add_argument("--state", type=str, default=None, help="Two-letter state code")
        parser.add_argument("--show-reasoning", action="store_true",
                            help="Show v2/v3 reasoning side-by-side for disagreements")
        parser.add_argument("--limit", type=int, default=50,
                            help="Max disagreements to show with --show-reasoning")
        parser.add_argument("--job-id", type=int, default=None,
                            help="Job ID for dashboard stats")

    def handle(self, *args, **options):
        engine = make_app_engine()
        run_tag = options["run_tag"]
        state = options["state"]
        show_reasoning = options["show_reasoning"]
        limit = options["limit"]

        run_info = self._get_run(engine, run_tag)
        if run_info is None:
            self.stderr.write(f"Run tag {run_tag!r} not found")
            return

        run_id = run_info["id"]
        is_incomplete = run_info["finished_at"] is None
        if is_incomplete:
            self.stderr.write(f"Warning: run '{run_tag}' is still in progress.")

        rows = self._fetch_comparison(engine, run_id, state)

        v2_null = []
        v3_errors = []
        agree = []
        disagree_rule = []
        disagree_llm = []

        for row in rows:
            if row["classified_by"] == "llm:error":
                v3_errors.append(row)
                continue
            if row["v2_type"] is None:
                v2_null.append(row)
                continue
            if row["v3_type"] == row["v2_type"]:
                agree.append(row)
            else:
                if row["classified_by"].startswith("rule:"):
                    disagree_rule.append(row)
                else:
                    disagree_llm.append(row)

        total_compared = len(agree) + len(disagree_rule) + len(disagree_llm)
        total_disagree = len(disagree_rule) + len(disagree_llm)

        job_id = options.get("job_id")
        if job_id:
            from pipeline.job_stats import merge_stats
            merge_stats(job_id, {
                "processed": total_compared,
                "failed": len(v3_errors),
            })

        self.stdout.write(f"\nClassification Comparison: {run_tag} vs corpus (Haiku v2)")
        filter_str = f" (state={state})" if state else ""
        self.stdout.write(f"  Docs compared: {total_compared:,}{filter_str}")
        self.stdout.write("")

        if total_compared > 0:
            self.stdout.write(f"  Agreement: {len(agree):,} ({len(agree)/total_compared*100:.1f}%)")
            self.stdout.write(f"  Disagreement: {total_disagree:,} ({total_disagree/total_compared*100:.1f}%)")
        else:
            self.stdout.write(f"  Agreement: 0")
            self.stdout.write(f"  Disagreement: 0")

        if v2_null:
            self.stdout.write(f"  v2 unclassified (excluded): {len(v2_null):,}")
        if v3_errors:
            self.stdout.write(f"  v3 errors (excluded): {len(v3_errors):,}")
        self.stdout.write("")

        # Top disagreements (v2 -> v3)
        if total_disagree > 0:
            change_counts = defaultdict(int)
            all_disagree = disagree_rule + disagree_llm
            for row in all_disagree:
                key = (row["v2_type"] or "(null)", row["v3_type"] or "(null)")
                change_counts[key] += 1

            self.stdout.write(f"  Top disagreements (v2 -> v3):")
            for (old, new), cnt in sorted(change_counts.items(), key=lambda x: -x[1])[:20]:
                self.stdout.write(f"    {old:30s} -> {new:30s}  {cnt:>6,}")
            self.stdout.write("")

            # By classification source
            self.stdout.write(f"  By classification source:")
            self.stdout.write(f"    Rule-matched disagreements:  {len(disagree_rule):,}")
            self.stdout.write(f"    LLM disagreements:           {len(disagree_llm):,}")
            self.stdout.write("")

            self.stdout.write(f"  Tiebreaker candidates: {len(disagree_llm):,} (use resolve_disagreements --run-tag {run_tag} to resolve)")

        if show_reasoning and total_disagree > 0:
            self._print_reasoning(disagree_rule + disagree_llm, limit)

    def _get_run(self, engine, run_tag):
        with engine.connect() as conn:
            row = conn.execute(text(
                f"SELECT id, finished_at FROM {_SCHEMA}.classification_runs "
                f"WHERE run_tag = :tag ORDER BY started_at DESC LIMIT 1"
            ), {"tag": run_tag}).fetchone()
            if row is None:
                return None
            return {"id": row[0], "finished_at": row[1]}

    def _fetch_comparison(self, engine, run_id, state):
        sql = (
            f"SELECT cr.content_sha256, cr.material_type AS v3_type, cr.classified_by, "
            f"  cr.reasoning AS v3_reasoning, "
            f"  c.material_type AS v2_type, c.reasoning AS v2_reasoning "
            f"FROM {_SCHEMA}.classification_results cr "
            f"JOIN {_SCHEMA}.classification_runs runs ON runs.id = cr.run_id "
            f"JOIN {_SCHEMA}.corpus c ON c.content_sha256 = cr.content_sha256 "
            f"WHERE runs.id = :run_id"
        )
        params = {"run_id": run_id}

        if state:
            sql += (
                f" AND c.source_org_ein IN "
                f"(SELECT ein FROM {_SCHEMA}.nonprofits_seed WHERE state = :state)"
            )
            params["state"] = state

        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()

        return [
            {
                "content_sha256": r[0],
                "v3_type": r[1],
                "classified_by": r[2] or "",
                "v3_reasoning": r[3] or "",
                "v2_type": r[4],
                "v2_reasoning": r[5] or "",
            }
            for r in rows
        ]

    def _print_reasoning(self, disagreements, limit):
        self.stdout.write(f"\nReasoning samples (disagreements, limit={limit}):")
        for row in disagreements[:limit]:
            sha = row["content_sha256"][:12]
            v2r = _CONTROL_CHAR_RE.sub("", row["v2_reasoning"])[:120]
            v3r = _CONTROL_CHAR_RE.sub("", row["v3_reasoning"])[:120]
            self.stdout.write(
                f"  {sha} {row['v2_type'] or '(null)':20s} -> {row['v3_type'] or '(null)':20s} "
                f"[{row['classified_by']}]"
            )
            if v2r:
                self.stdout.write(f"    v2: {v2r}")
            if v3r:
                self.stdout.write(f"    v3: {v3r}")

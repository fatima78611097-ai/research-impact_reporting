"""Compute TF-IDF keyness scores for extracted terms (Spec 0049 Step 2).

Usage:
    python3 manage.py score_keyness p20-pilot-v1
    python3 manage.py score_keyness p20-pilot-v1 --ntee P2%
    python3 manage.py score_keyness p20-pilot-v1 --min-docs 3
"""
from __future__ import annotations

import json
import logging
import re

from django.core.management.base import BaseCommand
from sqlalchemy import text

from lavandula.common.db import make_app_engine
from lavandula.nlp.keyness import aggregate_cvalue_scores, compute_tfidf_scores

log = logging.getLogger(__name__)

_RUN_TAG_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_NTEE_RE = re.compile(r"^[A-Z][0-9]*%?$")


class Command(BaseCommand):
    help = "Compute TF-IDF keyness scores for extracted vocabulary (Spec 0049)"

    def add_arguments(self, parser):
        parser.add_argument("run_tag", help="Run tag from extract_terms")
        parser.add_argument("--ntee", default="P2%", help="Target vertical (default: P2%%)")
        parser.add_argument("--min-docs", type=int, default=3,
                            help="Minimum docs a term must appear in")

    def handle(self, *args, **options):
        run_tag = options["run_tag"]
        ntee = options["ntee"]
        min_docs = options["min_docs"]

        if not _RUN_TAG_RE.match(run_tag):
            self.stderr.write(
                "Invalid run_tag: must be 1-64 alphanumeric/hyphen/underscore characters"
            )
            raise SystemExit(1)

        if not _NTEE_RE.match(ntee):
            self.stderr.write("Invalid NTEE filter: must match ^[A-Z][0-9]*%?$")
            raise SystemExit(1)

        engine = make_app_engine()

        with engine.connect() as conn:
            run_row = conn.execute(
                text("SELECT id, finished_at FROM lava_vocab.extraction_runs WHERE run_tag = :tag"),
                {"tag": run_tag},
            ).fetchone()

        if not run_row:
            self.stderr.write(f"Run tag {run_tag!r} not found. Run extract_terms first.")
            raise SystemExit(1)

        run_id = run_row[0]
        finished_at = run_row[1]

        if finished_at is None:
            self.stderr.write(
                "WARNING: extraction run not yet complete — scoring partial data"
            )

        with engine.connect() as conn:
            obs_count = conn.execute(
                text("SELECT COUNT(*) FROM lava_vocab.observations WHERE run_id = :run_id"),
                {"run_id": run_id},
            ).scalar()

        if not obs_count:
            self.stderr.write(f"No observations for run {run_tag!r}. Nothing to score.")
            return

        self.stdout.write(f"Scoring keyness for run {run_tag!r} ({obs_count} observations)")

        tfidf_results = compute_tfidf_scores(engine, run_id, ntee, min_docs=min_docs)
        self.stdout.write(f"Computed TF-IDF for {len(tfidf_results)} terms")

        if tfidf_results:
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM lava_vocab.tfidf_scores WHERE run_id = :run_id AND ntee_prefix = :ntee"),
                    {"run_id": run_id, "ntee": ntee.rstrip("%")},
                )
                for row in tfidf_results:
                    conn.execute(text("""
                        INSERT INTO lava_vocab.tfidf_scores
                            (run_id, term, ntee_prefix, doc_frequency,
                             corpus_doc_frequency, tfidf_within, tfidf_keyness)
                        VALUES (:run_id, :term, :ntee_prefix, :doc_frequency,
                                :corpus_doc_frequency, :tfidf_within, :tfidf_keyness)
                    """), row)

        cvalue_results = aggregate_cvalue_scores(engine, run_id, ntee)
        self.stdout.write(f"Aggregated C-value for {len(cvalue_results)} terms")

        if cvalue_results:
            ntee_clean = ntee.rstrip("%")
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM lava_vocab.cvalue_terms WHERE run_id = :run_id AND ntee_prefix = :ntee"),
                    {"run_id": run_id, "ntee": ntee_clean},
                )
                for row in cvalue_results:
                    conn.execute(text("""
                        INSERT INTO lava_vocab.cvalue_terms
                            (run_id, term, ntee_prefix, word_count,
                             cvalue, nc_value, doc_frequency)
                        VALUES (:run_id, :term, :ntee_prefix, :word_count,
                                :cvalue, :nc_value, :doc_frequency)
                    """), row)

        self._print_summary(tfidf_results, cvalue_results, ntee)

    def _print_summary(self, tfidf_results, cvalue_results, ntee):
        self.stdout.write(f"\n=== Keyness Summary: {ntee} ===")

        if tfidf_results:
            has_keyness = [r for r in tfidf_results if r.get("tfidf_keyness") is not None]
            if has_keyness:
                top_keyness = sorted(has_keyness, key=lambda r: -(r["tfidf_keyness"] or 0))[:20]
                self.stdout.write(f"\nTop 20 terms by keyness:")
                for r in top_keyness:
                    self.stdout.write(
                        f"  {r['term']:<40} keyness={r['tfidf_keyness']:.2f} "
                        f"df={r['doc_frequency']}"
                    )
            else:
                self.stdout.write("\n(Cross-vertical keyness not available — single vertical)")

            top_tfidf = sorted(tfidf_results, key=lambda r: -r["tfidf_within"])[:20]
            self.stdout.write(f"\nTop 20 terms by TF-IDF within vertical:")
            for r in top_tfidf:
                self.stdout.write(
                    f"  {r['term']:<40} tfidf={r['tfidf_within']:.4f} "
                    f"df={r['doc_frequency']}"
                )

        if cvalue_results:
            top_cvalue = sorted(cvalue_results, key=lambda r: -r["cvalue"])[:20]
            self.stdout.write(f"\nTop 20 C-value terms:")
            for r in top_cvalue:
                self.stdout.write(
                    f"  {r['term']:<40} C={r['cvalue']:.2f} "
                    f"NC={r['nc_value']:.2f} df={r['doc_frequency']}"
                )

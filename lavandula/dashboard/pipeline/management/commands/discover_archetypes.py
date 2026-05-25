"""Discover organizational sub-archetypes via FP-Growth + clustering (Spec 0049 Step 3).

Usage:
    python3 manage.py discover_archetypes p20-pilot-v1
    python3 manage.py discover_archetypes p20-pilot-v1 --ntee P2%
    python3 manage.py discover_archetypes p20-pilot-v1 --k 6
    python3 manage.py discover_archetypes p20-pilot-v1 --min-support 0.05 --min-lift 1.5
"""
from __future__ import annotations

import json
import logging
import re

from django.core.management.base import BaseCommand
from sqlalchemy import text

from lavandula.common.db import make_app_engine
from lavandula.nlp.archetypes import (
    auto_label,
    build_org_term_matrix,
    compute_lift_per_term,
    find_clusters,
    run_fp_growth,
)

log = logging.getLogger(__name__)

_RUN_TAG_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_NTEE_RE = re.compile(r"^[A-Z][0-9]*%?$")


class Command(BaseCommand):
    help = "Discover organizational sub-archetypes (Spec 0049)"

    def add_arguments(self, parser):
        parser.add_argument("run_tag", help="Run tag from extract_terms")
        parser.add_argument("--ntee", default="P2%", help="Target vertical (default: P2%%)")
        parser.add_argument("--min-support", type=float, default=0.05,
                            help="Minimum FP-Growth itemset support")
        parser.add_argument("--min-lift", type=float, default=1.5,
                            help="Minimum lift for association rules")
        parser.add_argument("--min-keyness", type=float, default=None,
                            help="Minimum tfidf_keyness to include a term")
        parser.add_argument("--k", default="auto",
                            help="Number of clusters (int or 'auto')")
        parser.add_argument("--min-org-count", type=int, default=3,
                            help="Minimum orgs for a term to be included")
        parser.add_argument("--seed", type=int, default=42, help="Random seed")

    def handle(self, *args, **options):
        run_tag = options["run_tag"]
        ntee = options["ntee"]

        if not _RUN_TAG_RE.match(run_tag):
            self.stderr.write(
                "Invalid run_tag: must be 1-64 alphanumeric/hyphen/underscore characters"
            )
            raise SystemExit(1)

        if not _NTEE_RE.match(ntee):
            self.stderr.write("Invalid NTEE filter: must match ^[A-Z][0-9]*%?$")
            raise SystemExit(1)

        k_arg = options["k"]
        k = None if k_arg == "auto" else int(k_arg)

        engine = make_app_engine()

        with engine.connect() as conn:
            run_row = conn.execute(
                text("SELECT id FROM lava_vocab.extraction_runs WHERE run_tag = :tag"),
                {"tag": run_tag},
            ).fetchone()

        if not run_row:
            self.stderr.write(f"Run tag {run_tag!r} not found.")
            raise SystemExit(1)

        run_id = run_row[0]

        self.stdout.write(f"Building org-term matrix for {ntee}...")
        matrix, org_eins, term_labels = build_org_term_matrix(
            engine, run_id, ntee,
            min_org_count=options["min_org_count"],
            min_keyness=options["min_keyness"],
        )

        if matrix.size == 0:
            self.stderr.write("No data for archetype discovery.")
            return

        self.stdout.write(
            f"Matrix: {matrix.shape[0]} orgs x {matrix.shape[1]} terms"
        )

        self.stdout.write("Running hierarchical clustering...")
        labels, k_chosen, diagnostics = find_clusters(matrix, k=k, seed=options["seed"])

        self.stdout.write(f"k={k_chosen}")
        if "best_silhouette" in diagnostics:
            self.stdout.write(f"  silhouette: {diagnostics['best_silhouette']:.3f}")
        if "warning" in diagnostics:
            self.stdout.write(f"  WARNING: {diagnostics['warning']}")

        ntee_clean = ntee.rstrip("%")
        config = {
            "min_support": options["min_support"],
            "min_lift": options["min_lift"],
            "min_keyness": options["min_keyness"],
            "min_org_count": options["min_org_count"],
            "k": k_chosen,
            "seed": options["seed"],
            "diagnostics": diagnostics,
        }

        with engine.begin() as conn:
            conn.execute(
                text("""
                    DELETE FROM lava_vocab.association_rules
                    WHERE archetype_id IN (
                        SELECT id FROM lava_vocab.archetypes
                        WHERE run_id = :run_id AND ntee_prefix = :ntee
                    )
                """),
                {"run_id": run_id, "ntee": ntee_clean},
            )
            conn.execute(
                text("""
                    DELETE FROM lava_vocab.archetype_members
                    WHERE archetype_id IN (
                        SELECT id FROM lava_vocab.archetypes
                        WHERE run_id = :run_id AND ntee_prefix = :ntee
                    )
                """),
                {"run_id": run_id, "ntee": ntee_clean},
            )
            conn.execute(
                text("DELETE FROM lava_vocab.archetypes WHERE run_id = :run_id AND ntee_prefix = :ntee"),
                {"run_id": run_id, "ntee": ntee_clean},
            )

        self.stdout.write(f"\n=== Archetype Discovery: {run_tag} ({ntee}) ===")
        self.stdout.write(f"k={k_chosen}")
        if "best_silhouette" in diagnostics:
            self.stdout.write(f"  (silhouette: {diagnostics['best_silhouette']:.3f})")

        for cluster_id in range(k_chosen):
            mask = labels == cluster_id
            org_count = int(mask.sum())
            cluster_eins = [org_eins[i] for i in range(len(org_eins)) if mask[i]]

            term_lifts = compute_lift_per_term(matrix, labels, cluster_id, term_labels)
            label = auto_label(term_lifts)

            rules = run_fp_growth(
                matrix, term_labels, labels, cluster_id,
                min_support=options["min_support"],
                min_lift=options["min_lift"],
            )

            with engine.begin() as conn:
                archetype_id = conn.execute(text("""
                    INSERT INTO lava_vocab.archetypes
                        (run_id, label, ntee_prefix, method, cluster_id,
                         org_count, config_json)
                    VALUES (:run_id, :label, :ntee, 'fp_growth_ward', :cluster_id,
                            :org_count, :config)
                    RETURNING id
                """), {
                    "run_id": run_id, "label": label, "ntee": ntee_clean,
                    "cluster_id": cluster_id, "org_count": org_count,
                    "config": json.dumps(config),
                }).scalar()

                for ein in cluster_eins:
                    conn.execute(text("""
                        INSERT INTO lava_vocab.archetype_members
                            (archetype_id, source_org_ein, membership_strength)
                        VALUES (:aid, :ein, 1.0)
                    """), {"aid": archetype_id, "ein": ein})

                for rule in rules:
                    conn.execute(text("""
                        INSERT INTO lava_vocab.association_rules
                            (archetype_id, antecedent, consequent,
                             support, confidence, lift, conviction)
                        VALUES (:aid, :ant, :con, :sup, :conf, :lift, :conv)
                    """), {
                        "aid": archetype_id,
                        "ant": rule["antecedent"],
                        "con": rule["consequent"],
                        "sup": rule["support"],
                        "conf": rule["confidence"],
                        "lift": rule["lift"],
                        "conv": rule["conviction"],
                    })

            self.stdout.write(f"\n[{cluster_id + 1}] {label} ({org_count} orgs)")

            top_terms = term_lifts[:5]
            if top_terms:
                terms_str = ", ".join(
                    f"{t} (lift {l:.1f})" for t, l in top_terms
                )
                self.stdout.write(f"    Top terms: {terms_str}")

            if cluster_eins:
                examples = cluster_eins[:3]
                self.stdout.write(f"    Example orgs: {', '.join(examples)}")

            if rules:
                self.stdout.write(f"    Association rules: {len(rules)}")

        self.stdout.write(f"\nTotal: {k_chosen} archetypes, {len(org_eins)} orgs")

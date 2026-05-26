"""Extract metric observations with context snippets (Spec 0050).

Scans parsed sections and tables for numeric values co-occurring with archetype
signature vocabulary, then writes (term, number, snippet) tuples to
lava_vocab.metric_observations.

Usage:
    python3 manage.py extract_metrics p20-pilot-v1
    python3 manage.py extract_metrics p20-pilot-v1 --ntee P2%
    python3 manage.py extract_metrics p20-pilot-v1 --batch-size 50
    python3 manage.py extract_metrics p20-pilot-v1 --resume
    python3 manage.py extract_metrics p20-pilot-v1 --dry-run
"""
from __future__ import annotations

import json
import logging
import re
import signal
import time

from django.core.management.base import BaseCommand
from sqlalchemy import text

from lavandula.common.db import make_app_engine
from lavandula.nlp.metrics import process_document

log = logging.getLogger(__name__)

_RUN_TAG_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_NTEE_RE = re.compile(r"^[A-Z][0-9]*%?$")
_EXTRACTOR_VERSION = "0050-v1"


def _get_vmhwm_mb() -> float | None:
    """Read peak RSS (VmHWM) from /proc/self/status in MB."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    return None


class Command(BaseCommand):
    help = "Extract metric observations with context snippets (Spec 0050)"

    def add_arguments(self, parser):
        parser.add_argument("run_tag", help="Run tag from extract_terms (reuses existing run)")
        parser.add_argument("--ntee", default="P2%", help="NTEE prefix filter (default: P2%%)")
        parser.add_argument("--batch-size", type=int, default=50, help="Docs per DB fetch")
        parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint")
        parser.add_argument("--dry-run", action="store_true", help="Show eligible doc count only")

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

        engine = make_app_engine()

        with engine.connect() as conn:
            run_row = conn.execute(
                text("SELECT id, config_json FROM lava_vocab.extraction_runs WHERE run_tag = :tag"),
                {"tag": run_tag},
            ).fetchone()

        if not run_row:
            self.stderr.write(
                f"Run tag {run_tag!r} not found. "
                "extract_metrics requires an existing run from extract_terms."
            )
            raise SystemExit(1)

        run_id = run_row[0]

        with engine.connect() as conn:
            archetypes_exist = conn.execute(
                text("SELECT COUNT(*) FROM lava_vocab.archetypes WHERE run_id = :run_id"),
                {"run_id": run_id},
            ).scalar()

        if not archetypes_exist:
            self.stderr.write(
                f"No archetypes found for run {run_id}. "
                "Run discover_archetypes before extract_metrics."
            )
            raise SystemExit(1)

        lock_key = f"extract-metrics-{run_tag}"
        lock_conn = engine.connect()
        try:
            locked = lock_conn.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:key))"),
                {"key": lock_key},
            ).scalar()
            lock_conn.commit()
            if not locked:
                self.stderr.write(f"Another metric extraction with tag {run_tag!r} is running.")
                lock_conn.close()
                return

            self._run(engine, run_id, **options)
        finally:
            try:
                lock_conn.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:key))"),
                    {"key": lock_key},
                )
                lock_conn.commit()
            except Exception:
                log.exception("Failed to release advisory lock")
            finally:
                lock_conn.close()

    def _run(self, engine, run_id, **options):
        run_tag = options["run_tag"]
        ntee = options["ntee"]
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]
        resume = options["resume"]

        eligible_count = self._count_eligible(engine, ntee)
        self.stdout.write(f"Eligible documents: {eligible_count}")

        if dry_run:
            self.stdout.write("Dry run — no data written.")
            return

        if eligible_count == 0:
            self.stdout.write("No eligible documents. Nothing to do.")
            return

        cursor = self._get_cursor(engine, run_id, resume)
        if cursor is None:
            return

        term_set, archetype_terms, ein_to_archetype = self._load_term_sets(
            engine, run_id, ntee,
        )
        self.stdout.write(
            f"Loaded {len(term_set)} terms, "
            f"{len(ein_to_archetype)} orgs with archetypes"
        )

        shutdown = False
        original_sigint = signal.getsignal(signal.SIGINT)

        def _handle_sigint(signum, frame):
            nonlocal shutdown
            if shutdown:
                self.stderr.write("\nForce quit.")
                signal.signal(signal.SIGINT, original_sigint)
                return
            self.stderr.write("\nShutting down gracefully after current document...")
            shutdown = True

        signal.signal(signal.SIGINT, _handle_sigint)

        doc_count = 0
        obs_count = 0
        start_time = time.monotonic()
        initial_hwm = _get_vmhwm_mb()

        self.stdout.write("Starting metric extraction...")

        try:
            while not shutdown:
                batch = self._fetch_batch(engine, ntee, cursor, batch_size)
                if not batch:
                    break

                for doc_row in batch:
                    if shutdown:
                        break

                    sha = doc_row["content_sha256"]
                    ein = doc_row["source_org_ein"]
                    archetype_id = ein_to_archetype.get(ein)

                    doc_term_set = term_set
                    if archetype_id and archetype_id in archetype_terms:
                        doc_term_set = archetype_terms[archetype_id] | term_set

                    try:
                        observations = process_document(
                            engine, run_id, sha, ein, doc_term_set, archetype_id,
                        )
                    except Exception as e:
                        log.warning("Failed to process %s: %s", sha[:16], e)
                        cursor = sha
                        self._update_cursor(engine, run_id, cursor)
                        doc_count += 1
                        continue

                    if observations:
                        self._write_observations(engine, run_id, sha, ein, observations)
                        obs_count += len(observations)

                    cursor = sha
                    self._update_cursor(engine, run_id, cursor)
                    doc_count += 1

                    if doc_count % 100 == 0:
                        hwm = _get_vmhwm_mb()
                        elapsed = time.monotonic() - start_time
                        rate = doc_count / elapsed if elapsed > 0 else 0
                        self.stdout.write(
                            f"  Progress: {doc_count} docs, {obs_count} observations "
                            f"[{rate:.1f} docs/s, VmHWM={hwm:.0f}MB]"
                        )
                        if hwm and hwm > 400:
                            log.warning("Peak RSS %.0fMB exceeds 400MB warning threshold", hwm)

                elapsed = time.monotonic() - start_time
                rate = doc_count / elapsed if elapsed > 0 else 0
                self.stdout.write(
                    f"  Batch done: {doc_count}/{eligible_count} docs, "
                    f"{obs_count} observations [{rate:.1f} docs/s]"
                )

        finally:
            signal.signal(signal.SIGINT, original_sigint)

        elapsed = time.monotonic() - start_time
        final_hwm = _get_vmhwm_mb()
        stats = {
            "metrics_doc_count": doc_count,
            "metrics_obs_count": obs_count,
            "metrics_elapsed_sec": round(elapsed, 1),
            "metrics_peak_rss_mb": final_hwm,
        }

        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE lava_vocab.extraction_runs
                SET stats_json = COALESCE(stats_json, '{}')::jsonb || :stats::jsonb
                WHERE id = :run_id
            """), {"run_id": run_id, "stats": json.dumps(stats)})

        self.stdout.write(
            f"\nMetric extraction complete: {doc_count} docs, "
            f"{obs_count} observations in {elapsed:.0f}s "
            f"(VmHWM={final_hwm:.0f}MB)"
        )

    def _count_eligible(self, engine, ntee):
        with engine.connect() as conn:
            count = conn.execute(text("""
                SELECT COUNT(DISTINCT d.content_sha256)
                FROM lava_parse.documents d
                JOIN lava_corpus.corpus c ON d.content_sha256 = c.content_sha256
                JOIN lava_corpus.nonprofits_seed ns ON c.source_org_ein = ns.ein
                WHERE ns.ntee_code LIKE :ntee
                  AND c.v3_material_type = ANY(:materials)
                  AND d.error IS NULL
            """), {"ntee": ntee, "materials": ["annual_report", "impact_report"]}).scalar()
        return count or 0

    def _get_cursor(self, engine, run_id, resume):
        if not resume:
            return ""
        with engine.connect() as conn:
            config = conn.execute(
                text("SELECT config_json FROM lava_vocab.extraction_runs WHERE id = :run_id"),
                {"run_id": run_id},
            ).fetchone()
        if not config:
            return ""
        config_json = config[0] if isinstance(config[0], dict) else json.loads(config[0] or "{}")
        cursor = config_json.get("metrics_cursor", "")
        if cursor:
            self.stdout.write(f"Resuming from cursor {cursor[:16]}...")
        return cursor

    def _load_term_sets(self, engine, run_id, ntee):
        """Load archetype signature terms and fallback TF-IDF terms."""
        with engine.connect() as conn:
            archetype_rows = conn.execute(text("""
                SELECT a.id, am.source_org_ein
                FROM lava_vocab.archetypes a
                JOIN lava_vocab.archetype_members am ON a.id = am.archetype_id
                WHERE a.run_id = :run_id
            """), {"run_id": run_id}).fetchall()

            tfidf_rows = conn.execute(text("""
                SELECT term
                FROM lava_vocab.tfidf_scores
                WHERE run_id = :run_id
                  AND ntee_prefix = :ntee_clean
                ORDER BY tfidf_within DESC
                LIMIT 500
            """), {"run_id": run_id, "ntee_clean": ntee.rstrip("%")}).fetchall()

            obs_rows = conn.execute(text("""
                SELECT DISTINCT o.term, am.archetype_id
                FROM lava_vocab.observations o
                JOIN lava_vocab.archetype_members am ON o.source_org_ein = am.source_org_ein
                JOIN lava_vocab.archetypes a ON am.archetype_id = a.id
                WHERE o.run_id = :run_id AND a.run_id = :run_id
            """), {"run_id": run_id}).fetchall()

        ein_to_archetype: dict[str, int] = {}
        for row in archetype_rows:
            ein_to_archetype[row[1]] = row[0]

        fallback_terms: set[str] = {row[0] for row in tfidf_rows}

        archetype_term_counts: dict[int, dict[str, int]] = {}
        for term, arch_id in obs_rows:
            archetype_term_counts.setdefault(arch_id, {}).setdefault(term, 0)
            archetype_term_counts[arch_id][term] += 1

        archetype_terms: dict[int, set[str]] = {}
        for arch_id, term_counts in archetype_term_counts.items():
            archetype_terms[arch_id] = set(term_counts.keys())

        return fallback_terms, archetype_terms, ein_to_archetype

    def _fetch_batch(self, engine, ntee, cursor, batch_size):
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT d.content_sha256, d.source_org_ein
                FROM lava_parse.documents d
                JOIN lava_corpus.corpus c ON d.content_sha256 = c.content_sha256
                JOIN lava_corpus.nonprofits_seed ns ON c.source_org_ein = ns.ein
                WHERE ns.ntee_code LIKE :ntee
                  AND c.v3_material_type = ANY(:materials)
                  AND d.error IS NULL
                  AND d.content_sha256 > :cursor
                ORDER BY d.content_sha256
                LIMIT :limit
            """), {
                "ntee": ntee,
                "materials": ["annual_report", "impact_report"],
                "cursor": cursor,
                "limit": batch_size,
            }).fetchall()
        return [{"content_sha256": r[0], "source_org_ein": r[1]} for r in rows]

    def _write_observations(self, engine, run_id, sha, ein, observations):
        with engine.begin() as conn:
            for obs in observations:
                conn.execute(text("""
                    INSERT INTO lava_vocab.metric_observations
                        (run_id, content_sha256, source_org_ein, term, numeric_value,
                         numeric_parsed, unit_hint, snippet, snippet_heading, section_index,
                         source_type, archetype_id, confidence)
                    VALUES (:run_id, :sha, :ein, :term, :numeric_value,
                            :numeric_parsed, :unit_hint, :snippet, :snippet_heading,
                            :section_index, :source_type, :archetype_id, :confidence)
                    ON CONFLICT (run_id, content_sha256, term, numeric_value, section_index)
                    DO NOTHING
                """), {
                    "run_id": run_id,
                    "sha": sha,
                    "ein": ein,
                    "term": obs["term"],
                    "numeric_value": obs["numeric_value"],
                    "numeric_parsed": obs.get("numeric_parsed"),
                    "unit_hint": obs.get("unit_hint"),
                    "snippet": obs["snippet"],
                    "snippet_heading": obs.get("snippet_heading"),
                    "section_index": obs.get("section_index"),
                    "source_type": obs.get("source_type", "narrative"),
                    "archetype_id": obs.get("archetype_id"),
                    "confidence": obs.get("confidence", "medium"),
                })

    def _update_cursor(self, engine, run_id, cursor):
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE lava_vocab.extraction_runs
                SET config_json = jsonb_set(
                    COALESCE(config_json, '{}'),
                    '{metrics_cursor}',
                    to_jsonb(:cursor::text)
                )
                WHERE id = :run_id
            """), {"run_id": run_id, "cursor": cursor})

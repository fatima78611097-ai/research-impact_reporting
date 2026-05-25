"""Extract domain vocabulary from Docling-parsed sections (Spec 0049 Step 1).

Prerequisite: python -m spacy download en_core_web_lg

Usage:
    python3 manage.py extract_terms p20-pilot-v1
    python3 manage.py extract_terms p20-pilot-v1 --ntee P2%
    python3 manage.py extract_terms p20-pilot-v1 --material annual_report impact_report
    python3 manage.py extract_terms p20-pilot-v1 --workers 2 --batch-size 100
    python3 manage.py extract_terms p20-pilot-v1 --dry-run
    python3 manage.py extract_terms p20-pilot-v1 --resume
"""
from __future__ import annotations

import json
import logging
import re
import signal
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from django.core.management.base import BaseCommand
from sqlalchemy import text

from lavandula.common.db import make_app_engine

log = logging.getLogger(__name__)

_RUN_TAG_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_NTEE_RE = re.compile(r"^[A-Z][0-9]*%?$")
_EXTRACTOR_VERSION = "0049-v1"

_worker_nlp = None


def _init_worker():
    """Process pool initializer: load spaCy model once per worker process."""
    global _worker_nlp
    import spacy
    _worker_nlp = spacy.load("en_core_web_lg", disable=["textcat"])


def _worker_extract(sections, min_section_chars):
    """Run in subprocess — uses process-local spaCy model."""
    from lavandula.nlp.extractor import extract_document
    return extract_document(_worker_nlp, sections, min_section_chars=min_section_chars)


class Command(BaseCommand):
    help = "Extract domain vocabulary from Docling-parsed sections (Spec 0049)"

    def add_arguments(self, parser):
        parser.add_argument("run_tag", help="Unique tag for this extraction run")
        parser.add_argument("--ntee", default="P2%", help="NTEE prefix filter (default: P2%%)")
        parser.add_argument("--material", nargs="+",
                            default=["annual_report", "impact_report"],
                            help="Material types to include")
        parser.add_argument("--workers", type=int, default=2, help="Concurrent extraction workers")
        parser.add_argument("--batch-size", type=int, default=100, help="Docs per DB fetch")
        parser.add_argument("--min-section-chars", type=int, default=50,
                            help="Minimum section body_text length")
        parser.add_argument("--dry-run", action="store_true", help="Show eligible doc count only")
        parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint")

    def handle(self, *args, **options):
        run_tag = options["run_tag"]
        ntee = options["ntee"]
        materials = options["material"]

        if not _RUN_TAG_RE.match(run_tag):
            self.stderr.write(
                "Invalid run_tag: must be 1-64 alphanumeric/hyphen/underscore characters"
            )
            raise SystemExit(1)

        if not _NTEE_RE.match(ntee):
            self.stderr.write("Invalid NTEE filter: must match ^[A-Z][0-9]*%?$")
            raise SystemExit(1)

        try:
            import spacy
            spacy.load("en_core_web_lg", disable=["textcat"])
        except OSError:
            self.stderr.write(
                "spaCy model not found. Install with:\n"
                "  python -m spacy download en_core_web_lg"
            )
            raise SystemExit(1)

        engine = make_app_engine()

        with engine.connect() as conn:
            known_types = {
                r[0] for r in conn.execute(
                    text("SELECT DISTINCT v3_material_type FROM lava_corpus.corpus WHERE v3_material_type IS NOT NULL")
                ).fetchall()
            }
        for m in materials:
            if m not in known_types:
                self.stderr.write(f"Unknown material type: {m}")
                raise SystemExit(1)

        lock_key = f"extract-terms-{run_tag}"
        lock_conn = engine.connect()
        try:
            locked = lock_conn.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:key))"),
                {"key": lock_key},
            ).scalar()
            lock_conn.commit()
            if not locked:
                self.stderr.write(f"Another extraction with tag {run_tag!r} is running.")
                lock_conn.close()
                return

            self._run(engine, **options)
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

    def _run(self, engine, **options):
        run_tag = options["run_tag"]
        ntee = options["ntee"]
        materials = options["material"]
        workers = options["workers"]
        batch_size = options["batch_size"]
        min_section_chars = options["min_section_chars"]
        dry_run = options["dry_run"]
        resume = options["resume"]

        eligible_count = self._count_eligible(engine, ntee, materials)
        self.stdout.write(f"Eligible documents: {eligible_count}")

        if dry_run:
            self.stdout.write("Dry run — no data written.")
            return

        if eligible_count == 0:
            self.stdout.write("No eligible documents. Nothing to do.")
            return

        run_id, cursor = self._init_run(engine, run_tag, ntee, materials, resume)
        if run_id is None:
            return

        shutdown = False
        original_sigint = signal.getsignal(signal.SIGINT)

        def _handle_sigint(signum, frame):
            nonlocal shutdown
            if shutdown:
                self.stderr.write("\nForce quit.")
                signal.signal(signal.SIGINT, original_sigint)
                return
            self.stderr.write("\nShutting down gracefully after current batch...")
            shutdown = True

        signal.signal(signal.SIGINT, _handle_sigint)

        success_count = 0
        error_count = 0
        total_terms = 0
        start_time = time.monotonic()

        self.stdout.write(f"Starting extraction with {workers} workers...")

        try:
            with ProcessPoolExecutor(
                max_workers=workers, initializer=_init_worker
            ) as pool:
                while not shutdown:
                    batch = self._fetch_batch(engine, run_id, ntee, materials, cursor, batch_size)
                    if not batch:
                        break

                    doc_sections = {}
                    skip_docs = []
                    for doc_row in batch:
                        sha = doc_row["content_sha256"]
                        ein = doc_row["source_org_ein"]
                        sections = self._fetch_sections(engine, sha, min_section_chars)
                        if not sections:
                            skip_docs.append((sha, ein))
                        else:
                            doc_sections[sha] = (ein, sections)

                    for sha, ein in skip_docs:
                        with engine.begin() as conn:
                            existing = conn.execute(text("""
                                SELECT run_id FROM lava_vocab.doc_extractions
                                WHERE content_sha256 = :sha
                            """), {"sha": sha}).fetchone()
                            if existing:
                                log.warning(
                                    "doc_extractions row already exists for %s "
                                    "(from run_id=%s), skipping tracking for current run",
                                    sha[:16], existing[0],
                                )
                                cursor = sha
                                continue
                            conn.execute(text("""
                                INSERT INTO lava_vocab.doc_extractions
                                    (content_sha256, run_id, source_org_ein, term_count, error)
                                VALUES (:sha, :run_id, :ein, 0, :error)
                            """), {
                                "sha": sha, "run_id": run_id, "ein": ein,
                                "error": "no_eligible_sections",
                            })
                            self._update_cursor(conn, run_id, sha)
                        cursor = sha

                    if not doc_sections:
                        continue

                    futures = {}
                    for sha, (ein, sections) in doc_sections.items():
                        if shutdown:
                            break
                        fut = pool.submit(_worker_extract, sections, min_section_chars)
                        futures[fut] = (sha, ein)

                    for fut in as_completed(futures):
                        sha, ein = futures[fut]
                        try:
                            sec_obs, cvalue_obs, warnings = fut.result()
                            all_obs = sec_obs + cvalue_obs
                            error_msg = "; ".join(warnings) if warnings else None

                            with engine.begin() as conn:
                                for obs in all_obs:
                                    conn.execute(text("""
                                        INSERT INTO lava_vocab.observations
                                            (run_id, content_sha256, source_org_ein,
                                             term, term_raw, term_type, pos_pattern,
                                             frequency, section_index, section_heading,
                                             heading_context)
                                        VALUES (:run_id, :sha, :ein,
                                                :term, :term_raw, :term_type, :pos_pattern,
                                                :freq, :sec_idx, :sec_heading, :heading_ctx)
                                        ON CONFLICT (run_id, content_sha256, term, section_index)
                                        DO UPDATE SET frequency = EXCLUDED.frequency
                                    """), {
                                        "run_id": run_id, "sha": sha, "ein": ein,
                                        "term": obs.term, "term_raw": obs.term_raw,
                                        "term_type": obs.term_type,
                                        "pos_pattern": obs.pos_pattern,
                                        "freq": obs.frequency,
                                        "sec_idx": obs.section_index,
                                        "sec_heading": obs.section_heading,
                                        "heading_ctx": obs.heading_context,
                                    })

                                existing = conn.execute(text("""
                                    SELECT run_id FROM lava_vocab.doc_extractions
                                    WHERE content_sha256 = :sha
                                """), {"sha": sha}).fetchone()
                                if existing:
                                    log.warning(
                                        "doc_extractions row already exists for %s "
                                        "(from run_id=%s), skipping tracking for current run",
                                        sha[:16], existing[0],
                                    )
                                else:
                                    conn.execute(text("""
                                        INSERT INTO lava_vocab.doc_extractions
                                            (content_sha256, run_id, source_org_ein, term_count, error)
                                        VALUES (:sha, :run_id, :ein, :count, :error)
                                    """), {
                                        "sha": sha, "run_id": run_id, "ein": ein,
                                        "count": len(all_obs),
                                        "error": error_msg[:200] if error_msg else None,
                                    })
                                self._update_cursor(conn, run_id, sha)

                            success_count += 1
                            total_terms += len(all_obs)
                            cursor = sha

                        except Exception as e:
                            err_msg = f"{type(e).__name__}: {str(e)[:200]}"
                            with engine.begin() as conn:
                                existing = conn.execute(text("""
                                    SELECT run_id FROM lava_vocab.doc_extractions
                                    WHERE content_sha256 = :sha
                                """), {"sha": sha}).fetchone()
                                if existing:
                                    log.warning(
                                        "doc_extractions row already exists for %s "
                                        "(from run_id=%s), skipping tracking for current run",
                                        sha[:16], existing[0],
                                    )
                                else:
                                    conn.execute(text("""
                                        INSERT INTO lava_vocab.doc_extractions
                                            (content_sha256, run_id, source_org_ein, term_count, error)
                                        VALUES (:sha, :run_id, :ein, 0, :error)
                                    """), {
                                        "sha": sha, "run_id": run_id, "ein": ein,
                                        "error": err_msg,
                                    })
                                self._update_cursor(conn, run_id, sha)
                            error_count += 1
                            cursor = sha
                            log.warning("Failed to extract %s: %s", sha[:16], err_msg)

                    processed = success_count + error_count
                    if processed > 0:
                        elapsed = time.monotonic() - start_time
                        rate = processed / elapsed if elapsed > 0 else 0
                        self.stdout.write(
                            f"  Batch done: {processed}/{eligible_count} "
                            f"({success_count} ok, {error_count} err) "
                            f"[{rate:.1f} docs/s, {total_terms} terms]"
                        )

                    total = success_count + error_count
                    if total > 0 and error_count / total > 0.20:
                        self.stderr.write(
                            f"High failure rate ({error_count}/{total} = "
                            f"{100*error_count/total:.0f}%) — "
                            f"check lava_parse data quality. Use --resume to continue."
                        )
                        break

        finally:
            signal.signal(signal.SIGINT, original_sigint)

        if not shutdown and success_count > 0:
            self._purge_high_freq_terms(engine, run_id)

        elapsed = time.monotonic() - start_time
        stats = {
            "success_count": success_count,
            "error_count": error_count,
            "total_terms": total_terms,
            "elapsed_sec": round(elapsed, 1),
        }

        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE lava_vocab.extraction_runs
                SET finished_at = now(), stats_json = :stats
                WHERE id = :run_id
            """), {"run_id": run_id, "stats": json.dumps(stats)})

        self.stdout.write(
            f"\nExtraction complete: {success_count} docs, "
            f"{error_count} errors, {total_terms} terms "
            f"in {elapsed:.0f}s"
        )

        if success_count == 0:
            raise SystemExit(1)

    def _count_eligible(self, engine, ntee, materials):
        with engine.connect() as conn:
            count = conn.execute(text("""
                SELECT COUNT(DISTINCT d.content_sha256)
                FROM lava_parse.documents d
                JOIN lava_corpus.corpus c ON d.content_sha256 = c.content_sha256
                JOIN lava_corpus.nonprofits_seed ns ON c.source_org_ein = ns.ein
                WHERE ns.ntee_code LIKE :ntee
                  AND c.v3_material_type = ANY(:materials)
                  AND d.error IS NULL
            """), {"ntee": ntee, "materials": materials}).scalar()
        return count or 0

    def _init_run(self, engine, run_tag, ntee, materials, resume):
        with engine.begin() as conn:
            existing = conn.execute(
                text("SELECT id, config_json FROM lava_vocab.extraction_runs WHERE run_tag = :tag"),
                {"tag": run_tag},
            ).fetchone()

            if existing and not resume:
                self.stderr.write(
                    f"Run tag {run_tag!r} already exists. Use --resume to continue."
                )
                return None, None

            if existing and resume:
                run_id = existing[0]
                config = existing[1] if isinstance(existing[1], dict) else json.loads(existing[1] or "{}")
                cursor = config.get("cursor", "")
                self.stdout.write(f"Resuming run {run_id} from cursor {cursor[:16]}...")
                return run_id, cursor

            config = {"cursor": "", "ntee": ntee, "materials": materials}
            run_id = conn.execute(text("""
                INSERT INTO lava_vocab.extraction_runs
                    (run_tag, ntee_filter, material_filter, extractor_version, config_json)
                VALUES (:tag, :ntee, :materials, :version, :config)
                RETURNING id
            """), {
                "tag": run_tag, "ntee": ntee, "materials": materials,
                "version": _EXTRACTOR_VERSION, "config": json.dumps(config),
            }).scalar()

        self.stdout.write(f"Created run {run_id} with tag {run_tag!r}")
        return run_id, ""

    def _fetch_batch(self, engine, run_id, ntee, materials, cursor, batch_size):
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
                  AND d.content_sha256 NOT IN (
                      SELECT content_sha256 FROM lava_vocab.doc_extractions
                      WHERE run_id = :run_id
                  )
                ORDER BY d.content_sha256
                LIMIT :limit
            """), {
                "ntee": ntee, "materials": materials,
                "cursor": cursor, "run_id": run_id, "limit": batch_size,
            }).fetchall()
        return [{"content_sha256": r[0], "source_org_ein": r[1]} for r in rows]

    def _fetch_sections(self, engine, sha, min_chars):
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT section_index, heading, heading_level, body_text,
                       char_count, page_start, page_end, parent_headings
                FROM lava_parse.sections
                WHERE content_sha256 = :sha
                ORDER BY section_index
            """), {"sha": sha}).fetchall()

        return [
            {
                "section_index": r[0],
                "heading": r[1],
                "heading_level": r[2],
                "body_text": r[3],
                "char_count": r[4],
                "page_start": r[5],
                "page_end": r[6],
                "parent_headings": r[7],
            }
            for r in rows
            if r[4] and r[4] >= min_chars
        ]

    def _update_cursor(self, conn, run_id, sha):
        conn.execute(text("""
            UPDATE lava_vocab.extraction_runs
            SET config_json = jsonb_set(config_json, '{cursor}', to_jsonb(:cursor::text))
            WHERE id = :run_id
        """), {"run_id": run_id, "cursor": sha})

    def _purge_high_freq_terms(self, engine, run_id):
        """Remove terms appearing in >80% of documents (AC9)."""
        with engine.begin() as conn:
            total_docs = conn.execute(text("""
                SELECT COUNT(*) FROM lava_vocab.doc_extractions
                WHERE run_id = :run_id AND error IS NULL
            """), {"run_id": run_id}).scalar() or 0

            if total_docs < 5:
                return

            threshold = int(0.80 * total_docs)

            purged = conn.execute(text("""
                WITH high_freq AS (
                    SELECT term, COUNT(DISTINCT content_sha256) AS doc_count
                    FROM lava_vocab.observations
                    WHERE run_id = :run_id
                    GROUP BY term
                    HAVING COUNT(DISTINCT content_sha256) > :threshold
                )
                DELETE FROM lava_vocab.observations
                WHERE run_id = :run_id AND term IN (SELECT term FROM high_freq)
                RETURNING term
            """), {"run_id": run_id, "threshold": threshold}).fetchall()

            purged_terms = list({r[0] for r in purged})

            if purged_terms:
                self.stdout.write(f"Purged {len(purged_terms)} high-frequency terms (>80% docs)")
                conn.execute(text("""
                    UPDATE lava_vocab.extraction_runs
                    SET config_json = jsonb_set(
                        config_json, '{purged_terms}', :terms::jsonb
                    ),
                    stats_json = jsonb_set(
                        COALESCE(stats_json, '{}'), '{purged_count}',
                        to_jsonb(:count)
                    )
                    WHERE id = :run_id
                """), {
                    "run_id": run_id,
                    "terms": json.dumps(purged_terms[:100]),
                    "count": len(purged_terms),
                })

            conn.execute(text("""
                UPDATE lava_vocab.extraction_runs
                SET config_json = jsonb_set(config_json, '{purge_complete}', 'true'::jsonb)
                WHERE id = :run_id
            """), {"run_id": run_id})

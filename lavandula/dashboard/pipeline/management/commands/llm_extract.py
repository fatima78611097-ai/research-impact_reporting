"""Extract impact metrics and stories from nonprofit reports via LLM (Spec 0051).

Usage:
    python3 manage.py llm_extract p20-test --ein 760305357
    python3 manage.py llm_extract p20-v1 --ntee P2% --parallel 10 --cost-limit 20
    python3 manage.py llm_extract p20-v1 --ntee P2% --dry-run
    python3 manage.py llm_extract p20-v1 --ntee P2% --resume
"""
from __future__ import annotations

import json
import logging
import re
import signal
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
import httpx
from django.core.management.base import BaseCommand
from sqlalchemy import text

from lavandula.common.db import make_app_engine
from lavandula.common.secrets import get_secret
from lavandula.nlp.llm_extract import extract_document

log = logging.getLogger(__name__)

_RUN_TAG_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_NTEE_RE = re.compile(r"^[A-Z][0-9]*%?$")
_STATE_RE = re.compile(r"^[A-Z]{2}$")
_EIN_RE = re.compile(r"^\d{9}$")
_EXTRACTOR_VERSION = "0051-v1"
_AVG_COST_PER_DOC = 0.004


class Command(BaseCommand):
    help = "Extract impact metrics and stories from nonprofit reports via DeepSeek LLM (Spec 0051)"

    def add_arguments(self, parser):
        parser.add_argument("run_tag", help="Unique identifier for this extraction run")
        parser.add_argument("--ntee", default="P2%", help="NTEE prefix filter (default: P2%%)")
        parser.add_argument("--state", default=None, help="Two-letter state code filter")
        parser.add_argument("--ein", default=None, help="Nine-digit EIN filter")
        parser.add_argument("--batch-size", type=int, default=20, help="Documents per progress update (default: 20)")
        parser.add_argument("--parallel", type=int, default=5, help="Concurrent API calls (default: 5, max: 20)")
        parser.add_argument("--resume", action="store_true", help="Skip already-extracted documents")
        parser.add_argument("--dry-run", action="store_true", help="Show eligible doc count and estimated cost only")
        parser.add_argument("--max-docs", type=int, default=None, help="Maximum documents to process")
        parser.add_argument("--cost-limit", type=float, default=25.0, help="Abort when estimated cost exceeds this (USD)")

    def handle(self, *args, **options):
        run_tag = options["run_tag"]
        ntee = options["ntee"]
        state = options["state"]
        ein = options["ein"]
        batch_size = options["batch_size"]
        parallel = options["parallel"]

        # Validate inputs
        if not _RUN_TAG_RE.match(run_tag):
            self.stderr.write("Invalid run_tag: must be 1-64 alphanumeric/hyphen/underscore characters")
            raise SystemExit(1)
        if not _NTEE_RE.match(ntee):
            self.stderr.write("Invalid NTEE filter: must match ^[A-Z][0-9]*%?$")
            raise SystemExit(1)
        if state and not _STATE_RE.match(state):
            self.stderr.write("Invalid state: must be two uppercase letters")
            raise SystemExit(1)
        if ein and not _EIN_RE.match(ein):
            self.stderr.write("Invalid EIN: must be exactly 9 digits")
            raise SystemExit(1)
        if not (1 <= batch_size <= 100):
            self.stderr.write("Invalid batch-size: must be 1-100")
            raise SystemExit(1)
        if not (1 <= parallel <= 20):
            self.stderr.write("Invalid parallel: must be 1-20")
            raise SystemExit(1)
        if options["cost_limit"] <= 0:
            self.stderr.write("Invalid cost-limit: must be positive")
            raise SystemExit(1)
        if options["max_docs"] is not None and options["max_docs"] <= 0:
            self.stderr.write("Invalid max-docs: must be positive")
            raise SystemExit(1)

        engine = make_app_engine()

        # Advisory lock to prevent concurrent runs with same tag
        lock_key = f"llm-extract-{run_tag}"
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
        state = options["state"]
        ein = options["ein"]
        batch_size = options["batch_size"]
        parallel = options["parallel"]
        resume = options["resume"]
        dry_run = options["dry_run"]
        max_docs = options["max_docs"]
        cost_limit = options["cost_limit"]

        # Count eligible documents
        eligible = self._query_eligible(engine, ntee, state, ein, max_docs, run_id=None, skipped_shas=[])
        eligible_count = len(eligible)
        est_cost = eligible_count * _AVG_COST_PER_DOC
        est_time_s = eligible_count / parallel * 10

        self.stdout.write(f"Eligible documents: {eligible_count}")
        self.stdout.write(f"Estimated cost: ${est_cost:.2f}")
        self.stdout.write(f"Estimated time: {est_time_s / 60:.1f} min at parallel={parallel}")

        if dry_run:
            self.stdout.write("Dry run — no data written.")
            return

        if eligible_count == 0:
            self.stdout.write("No eligible documents. Nothing to do.")
            return

        # Init or resume run
        run_id, skipped_shas = self._init_run(engine, run_tag, ntee, resume)
        if run_id is None:
            return

        # Re-query with run_id for resume filtering
        if resume:
            eligible = self._query_eligible(engine, ntee, state, ein, max_docs, run_id, skipped_shas)
            self.stdout.write(f"After resume filtering: {len(eligible)} documents remaining")

        if not eligible:
            self.stdout.write("All documents already processed.")
            self._finish_run(engine, run_id, "completed", {})
            return

        # Get API key
        api_key = get_secret("lavandula/deepseek/api_key")

        # Setup graceful shutdown
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

        # Stats
        docs_processed = 0
        docs_skipped = 0
        docs_failed = 0
        metrics_found = 0
        stories_found = 0
        cumulative_cost = 0.0
        start_time = time.monotonic()
        final_status = "completed"

        s3_client = boto3.client("s3")
        tmp_dir = Path(tempfile.mkdtemp(prefix="llm_extract_"))

        self.stdout.write(f"Starting extraction run {run_id} with {parallel} workers...")

        try:
            http_client = httpx.Client(
                headers={"Authorization": f"Bearer {api_key}"},
                verify=True,
            )

            try:
                # Process in batches
                for batch_start in range(0, len(eligible), batch_size):
                    if shutdown:
                        final_status = "aborted"
                        break

                    # Check stop_requested flag (poll-based stop from dashboard)
                    if self._check_stop_requested(engine, run_id):
                        self.stdout.write("Stop requested via dashboard. Shutting down...")
                        final_status = "aborted"
                        break

                    batch = eligible[batch_start:batch_start + batch_size]

                    futures = {}
                    with ThreadPoolExecutor(max_workers=parallel) as pool:
                        for doc in batch:
                            if shutdown:
                                break
                            fut = pool.submit(
                                extract_document,
                                engine, doc["sha"], doc["ein"], run_id,
                                api_key, s3_client, http_client, tmp_dir,
                            )
                            futures[fut] = doc

                        for fut in as_completed(futures):
                            doc = futures[fut]
                            try:
                                stats = fut.result()
                                if stats["skipped"]:
                                    docs_skipped += 1
                                    skipped_shas.append(doc["sha"])
                                elif stats["error"]:
                                    docs_failed += 1
                                else:
                                    docs_processed += 1
                                    metrics_found += stats["metrics"]
                                    stories_found += stats["stories"]

                                cumulative_cost += stats["cost_usd"]

                            except Exception as exc:
                                docs_failed += 1
                                log.error("Worker error for %s: %s", doc["sha"][:16], str(exc)[:200])

                    # Update running stats
                    elapsed = time.monotonic() - start_time
                    run_stats = {
                        "status": "running",
                        "docs_processed": docs_processed,
                        "docs_skipped": docs_skipped,
                        "docs_failed": docs_failed,
                        "metrics_found": metrics_found,
                        "stories_found": stories_found,
                        "cost_usd": round(cumulative_cost, 4),
                        "elapsed_s": round(elapsed, 1),
                        "skipped_shas": skipped_shas[-1000:],
                    }
                    self._update_stats(engine, run_id, run_stats)

                    total = docs_processed + docs_skipped + docs_failed
                    self.stdout.write(
                        f"  Batch: {total}/{len(eligible)} "
                        f"({docs_processed} ok, {docs_skipped} skip, {docs_failed} err) "
                        f"[{metrics_found} metrics, {stories_found} stories, "
                        f"${cumulative_cost:.3f}]"
                    )

                    # Cost limit check
                    if cumulative_cost >= cost_limit:
                        self.stderr.write(
                            f"Cost limit reached: ${cumulative_cost:.3f} >= ${cost_limit:.2f}"
                        )
                        final_status = "aborted"
                        break

            finally:
                http_client.close()

        except Exception as exc:
            final_status = "failed"
            log.exception("Unhandled error in extraction run")

        finally:
            signal.signal(signal.SIGINT, original_sigint)

            # Write final stats
            elapsed = time.monotonic() - start_time
            final_stats = {
                "status": final_status,
                "docs_processed": docs_processed,
                "docs_skipped": docs_skipped,
                "docs_failed": docs_failed,
                "metrics_found": metrics_found,
                "stories_found": stories_found,
                "cost_usd": round(cumulative_cost, 4),
                "elapsed_s": round(elapsed, 1),
                "skipped_shas": skipped_shas[-1000:],
            }
            self._finish_run(engine, run_id, final_status, final_stats)

            # Clean up temp directory
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)

        self.stdout.write(
            f"\nExtraction {final_status}: {docs_processed} docs, "
            f"{docs_failed} errors, {docs_skipped} skipped, "
            f"{metrics_found} metrics, {stories_found} stories, "
            f"${cumulative_cost:.3f} in {elapsed:.0f}s"
        )

    def _query_eligible(self, engine, ntee, state, ein, max_docs, run_id, skipped_shas):
        """Query eligible documents for extraction."""
        params = {"ntee": ntee}
        where_clauses = [
            "c.v3_material_type IN ('annual_report', 'impact_report')",
            "ns.ntee_code LIKE :ntee",
        ]

        if state:
            where_clauses.append("ns.state = :state")
            params["state"] = state
        if ein:
            where_clauses.append("c.source_org_ein = :ein")
            params["ein"] = ein

        # Resume: exclude already-processed documents
        resume_exclude = ""
        if run_id is not None:
            params["run_id"] = run_id
            resume_exclude = """
                AND c.content_sha256 NOT IN (
                    SELECT content_sha256 FROM lava_vocab.llm_metrics WHERE run_id = :run_id
                    UNION
                    SELECT content_sha256 FROM lava_vocab.llm_stories WHERE run_id = :run_id
                )
            """
            if skipped_shas:
                # Also exclude known skipped SHAs
                for i, sha in enumerate(skipped_shas):
                    key = f"skip_{i}"
                    params[key] = sha
                # Use a subquery approach to avoid massive IN clause
                # For now, filter in Python after query (skipped_shas can be large)

        where_sql = " AND ".join(where_clauses)
        limit_sql = f"LIMIT :limit" if max_docs else ""
        if max_docs:
            params["limit"] = max_docs

        query = f"""
            SELECT c.content_sha256, c.source_org_ein
            FROM lava_corpus.corpus c
            JOIN lava_corpus.nonprofits_seed ns ON c.source_org_ein = ns.ein
            WHERE {where_sql}
            {resume_exclude}
            ORDER BY c.source_org_ein, c.content_sha256
            {limit_sql}
        """

        with engine.connect() as conn:
            rows = conn.execute(text(query), params).fetchall()

        skip_set = set(skipped_shas) if skipped_shas else set()
        results = []
        for r in rows:
            if r[0] not in skip_set:
                results.append({"sha": r[0], "ein": r[1]})

        return results

    def _init_run(self, engine, run_tag, ntee, resume):
        """Create or resume an extraction run. Returns (run_id, skipped_shas) or (None, [])."""
        with engine.begin() as conn:
            existing = conn.execute(
                text("SELECT id, stats_json FROM lava_vocab.extraction_runs WHERE run_tag = :tag"),
                {"tag": run_tag},
            ).fetchone()

            if existing:
                run_id = existing[0]
                stats = existing[1] if isinstance(existing[1], dict) else json.loads(existing[1] or "{}")
                status = stats.get("status", "")

                if status == "running":
                    self.stderr.write(
                        f"Run {run_tag!r} is already active. "
                        "Use --resume if the previous process crashed."
                    )
                    return None, []

                if status in ("completed", "aborted", "failed"):
                    if not resume:
                        self.stderr.write(
                            f"Run {run_tag!r} already exists (status: {status}). "
                            "Use --resume to continue or pick a new run_tag."
                        )
                        return None, []

                    skipped_shas = stats.get("skipped_shas", [])
                    # Mark as running again
                    stats["status"] = "running"
                    conn.execute(text("""
                        UPDATE lava_vocab.extraction_runs
                        SET stats_json = CAST(:stats AS jsonb), finished_at = NULL
                        WHERE id = :run_id
                    """), {"run_id": run_id, "stats": json.dumps(stats)})
                    self.stdout.write(f"Resuming run {run_id} (was: {status})")
                    return run_id, skipped_shas

                # Unknown status — treat as resume-able if --resume
                if resume:
                    skipped_shas = stats.get("skipped_shas", [])
                    stats["status"] = "running"
                    conn.execute(text("""
                        UPDATE lava_vocab.extraction_runs
                        SET stats_json = CAST(:stats AS jsonb), finished_at = NULL
                        WHERE id = :run_id
                    """), {"run_id": run_id, "stats": json.dumps(stats)})
                    return run_id, skipped_shas

                self.stderr.write(f"Run {run_tag!r} exists with unknown status: {status}")
                return None, []

            # Create new run
            config = {"ntee": ntee}
            stats = {"status": "running", "skipped_shas": []}
            run_id = conn.execute(text("""
                INSERT INTO lava_vocab.extraction_runs
                    (run_tag, ntee_filter, extractor_version, config_json, stats_json)
                VALUES (:tag, :ntee, :version, CAST(:config AS jsonb), CAST(:stats AS jsonb))
                RETURNING id
            """), {
                "tag": run_tag, "ntee": ntee,
                "version": _EXTRACTOR_VERSION,
                "config": json.dumps(config),
                "stats": json.dumps(stats),
            }).scalar()

        self.stdout.write(f"Created run {run_id} with tag {run_tag!r}")
        return run_id, []

    def _update_stats(self, engine, run_id, stats):
        """Update running stats in the extraction_runs row."""
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE lava_vocab.extraction_runs
                SET stats_json = CAST(:stats AS jsonb)
                WHERE id = :run_id
            """), {"run_id": run_id, "stats": json.dumps(stats)})

    def _finish_run(self, engine, run_id, status, stats):
        """Write final stats and finished_at timestamp."""
        stats["status"] = status
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE lava_vocab.extraction_runs
                SET stats_json = CAST(:stats AS jsonb), finished_at = now()
                WHERE id = :run_id
            """), {"run_id": run_id, "stats": json.dumps(stats)})

    def _check_stop_requested(self, engine, run_id):
        """Check if dashboard set stop_requested flag."""
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT stats_json FROM lava_vocab.extraction_runs WHERE id = :run_id"),
                {"run_id": run_id},
            ).fetchone()
        if row:
            stats = row[0] if isinstance(row[0], dict) else json.loads(row[0] or "{}")
            return stats.get("stop_requested", False)
        return False

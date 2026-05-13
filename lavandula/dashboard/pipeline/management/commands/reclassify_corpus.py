"""Reclassify the corpus using rule pre-filter + augmented LLM prompt (Spec 0038).

Usage:
    python3 manage.py reclassify_corpus --run-tag v3.2
    python3 manage.py reclassify_corpus --run-tag v3.2 --state TX
    python3 manage.py reclassify_corpus --run-tag v3.2 --ein 13-1234567
    python3 manage.py reclassify_corpus --run-tag v3.2 --sample 500
    python3 manage.py reclassify_corpus --run-tag v3.2 --workers 8
    python3 manage.py reclassify_corpus --run-tag v3.2 --dry-run
    python3 manage.py reclassify_corpus --run-tag v3.2 --allow-fallback
    python3 manage.py reclassify_corpus --run-tag v3.2 --resume
"""
from __future__ import annotations

import json
import logging
import os
import random
import signal
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from pathlib import Path

from django.core.management.base import BaseCommand
from sqlalchemy import text

from lavandula.common.db import make_app_engine
from lavandula.nonprofits.definition_loader import load_definition, resolve_definition_name
from lavandula.nonprofits.prefilter import RuleEngine
from lavandula.reports.classify import (
    ClassificationResult,
    ClassifierError,
    classify_first_page_v3,
)
from lavandula.reports.classifier_clients import select_classifier_client

log = logging.getLogger(__name__)

_SCHEMA = "lava_corpus"
_MAX_CONSECUTIVE_FAILURES = 20
_MAX_DOC_RETRIES = 4
_RETRY_BASE_DELAYS = [2, 4, 8, 16]
_SIGINT_DRAIN_TIMEOUT = 30

_RULES_PATH = Path(__file__).resolve().parents[5] / "lavandula" / "nonprofits" / "definitions" / "prefilter_rules.yaml"

_COST_PER_DOC = {"deepseek": 0.00019, "haiku": 0.00025, "claude": 0.003, "gemini": 0.0002}
_DOCS_PER_SEC_PER_WORKER = 3.0


def _format_duration(seconds):
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m}m"


class Command(BaseCommand):
    help = "Reclassify corpus using rule pre-filter + augmented LLM (Spec 0038)"

    def add_arguments(self, parser):
        parser.add_argument("--run-tag", required=True, help="Unique tag for this classification run")
        parser.add_argument("--state", type=str, default=None, help="Two-letter state code")
        parser.add_argument("--ein", type=str, default=None, help="EIN (e.g., 13-1234567)")
        parser.add_argument("--sample", type=int, default=None, help="Random sample size")
        parser.add_argument("--where", type=str, default=None, help="Additional WHERE clause")
        parser.add_argument("--dry-run", action="store_true", help="Show counts without classifying")
        parser.add_argument("--backend", choices=["deepseek", "claude", "gemini", "codex"],
                            default="deepseek", help="LLM backend")
        parser.add_argument("--workers", type=int, default=4, help="Concurrent LLM workers")
        parser.add_argument("--batch-size", type=int, default=200, help="Docs per database fetch")
        parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint")
        parser.add_argument("--definition", type=str, default=None, help="Classifier definition name")
        parser.add_argument("--allow-fallback", action="store_true",
                            help="Allow first_page_text when no extraction context")
        parser.add_argument("--min-text-len", type=int, default=1000,
                            help="Minimum pages_text length to classify")
        parser.add_argument("--quiet", action="store_true",
                            help="Suppress per-batch progress (keep start/end summary)")

    def handle(self, *args, **options):
        engine = make_app_engine()
        run_tag = options["run_tag"]
        lock_key = f"reclassify-{run_tag}"

        self._lock_conn = engine.connect()
        try:
            locked = self._lock_conn.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:key))"),
                {"key": lock_key},
            ).scalar()
            self._lock_conn.commit()
            if not locked:
                self.stderr.write(f"Another reclassification with tag {run_tag!r} is running.")
                self._lock_conn.close()
                return

            self._run(engine, **options)
        finally:
            try:
                self._lock_conn.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:key))"),
                    {"key": lock_key},
                )
                self._lock_conn.commit()
            except Exception:
                log.exception("Failed to release advisory lock")
            finally:
                self._lock_conn.close()

    def _run(self, engine, **options):
        run_tag = options["run_tag"]
        sample = options["sample"]
        where_clause = options["where"]
        dry_run = options["dry_run"]
        backend = options["backend"]
        workers = options["workers"]
        batch_size = options["batch_size"]
        resume = options["resume"]
        allow_fallback = options["allow_fallback"]
        min_text_len = options["min_text_len"]
        quiet = options["quiet"]
        def_name = resolve_definition_name(options.get("definition"))

        if resume and sample:
            self.stderr.write("ERROR: --sample runs cannot be resumed.")
            return

        rules_path = _RULES_PATH
        if not rules_path.is_file():
            rules_path = Path(__file__).resolve().parents[4] / "nonprofits" / "definitions" / "prefilter_rules.yaml"

        rule_engine = RuleEngine(rules_path)
        definition = load_definition(def_name)

        if allow_fallback:
            self.stderr.write(
                "WARNING: --allow-fallback enabled. Documents without extraction context\n"
                "will be classified using first_page_text only. Results may be poor."
            )

        # --- Startup counts ---
        counts = self._count_eligible(engine, state=options["state"], ein=options["ein"],
                                       where_clause=where_clause, allow_fallback=allow_fallback,
                                       min_text_len=min_text_len)

        if counts["eligible"] == 0 and not allow_fallback:
            self.stderr.write(
                "ERROR: No documents with extraction context match the filter.\n"
                "  Run extract_classification_context first, or use --allow-fallback."
            )
            return

        filter_desc = self._filter_desc(options["state"], options["ein"], where_clause, sample)

        if dry_run:
            self._print_dry_run(counts, run_tag, filter_desc, backend, workers, min_text_len,
                                allow_fallback, engine, options["state"], options["ein"],
                                where_clause, sample)
            return

        # --- Init or resume run ---
        run_id, cursor, stored_config = self._init_run(
            engine, run_tag, resume, rule_engine, definition, backend,
            options["state"], options["ein"], where_clause,
            allow_fallback, min_text_len, sample,
        )
        if run_id is None:
            return

        if resume and stored_config:
            state = stored_config.get("filters", {}).get("state")
            ein = stored_config.get("filters", {}).get("ein")
            where_clause = stored_config.get("filters", {}).get("where")
            backend = stored_config.get("backend", backend)
            allow_fallback = stored_config.get("allow_fallback", allow_fallback)
            min_text_len = stored_config.get("min_text_len", min_text_len)
        else:
            state = options["state"]
            ein = options["ein"]

        # --- Print startup summary ---
        self._print_startup(run_tag, backend, workers, batch_size, definition,
                            filter_desc, counts, allow_fallback)

        # --- Create clients (one per worker) ---
        clients = [select_classifier_client(backend=backend) for _ in range(workers)]

        stats = defaultdict(int)
        distribution = defaultdict(int)
        batch_times = deque(maxlen=5)
        shutdown = threading.Event()
        active_futures = []

        original_handler = signal.getsignal(signal.SIGINT)

        def _handle_sigint(signum, frame):
            if shutdown.is_set():
                self.stderr.write("\nForce quit.")
                signal.signal(signal.SIGINT, original_handler)
                return
            self.stderr.write("\nShutting down gracefully... (press Ctrl+C again to force)")
            shutdown.set()

        signal.signal(signal.SIGINT, _handle_sigint)

        # --- Watchdog ---
        from lavandula.reports.stall_watchdog import StallWatchdog

        watchdog = StallWatchdog(
            get_progress=lambda: stats["total"],
            get_active=lambda: len([f for f in active_futures if not f.done()]),
            stall_threshold_sec=600,
            notify_email=os.environ.get("WATCHDOG_NOTIFY_EMAIL"),
            progress_total=counts["eligible"],
        )
        watchdog.start_thread()

        # --- Rate limit tracking ---
        rate_limit_lock = threading.Lock()
        rate_limit_times = deque(maxlen=20)

        total_eligible = counts["eligible"]
        last_cursor = cursor or ""
        batch_num = 0
        consecutive_failures = 0
        previous_cursor = cursor or ""
        start_time = time.monotonic()

        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                while not shutdown.is_set():
                    # Global throttle check
                    with rate_limit_lock:
                        recent_429s = sum(1 for t in rate_limit_times
                                          if time.monotonic() - t < 10)
                    if recent_429s >= 3:
                        self.stdout.write(f"[throttle] 3+ rate limits in 10s, pausing 30s")
                        time.sleep(30)

                    batch_start = time.monotonic()
                    rows = self._fetch_batch(
                        engine, run_id, last_cursor, sample, where_clause,
                        state, ein, allow_fallback, min_text_len, batch_size,
                    )
                    if not rows:
                        break

                    batch_max_sha = rows[-1]["content_sha256"]
                    futures = {}

                    for row in rows:
                        if shutdown.is_set():
                            break

                        pages_text = row.get("pages_text") or ""
                        first_page_text = row.get("first_page_text") or ""
                        has_context = bool(pages_text)
                        eval_text = pages_text if has_context else first_page_text

                        # Quality gate
                        if len(eval_text.strip()) < min_text_len:
                            if has_context:
                                stats["skip_ctx"] += 1
                            else:
                                stats["skip_fp"] += 1
                            stats["total"] += 1
                            self._write_result(
                                engine, run_id, row["content_sha256"],
                                material_type=None, material_group=None,
                                event_type=None, confidence=None,
                                reasoning=f"Skipped: text length {len(eval_text.strip())} < {min_text_len}",
                                classified_by="skip:short_text",
                            )
                            continue

                        # Rule prefilter (fast, main thread)
                        rule_match = rule_engine.evaluate(
                            text=eval_text,
                            url=row.get("source_url") or "",
                            pdf_creator=row.get("pdf_creator") or "",
                            page_count=row.get("total_pages"),
                            file_size=row.get("file_size"),
                        )
                        if rule_match is not None:
                            cat = definition.get_category(rule_match.material_type)
                            mg = cat.group if cat else None
                            classified_by = f"rule:{rule_match.rule_name}@{rule_match.rules_sha256[:8]}"
                            self._write_result(
                                engine, run_id, row["content_sha256"],
                                material_type=rule_match.material_type,
                                material_group=mg,
                                event_type=None,
                                confidence=None,
                                reasoning=rule_match.reasoning[:500],
                                classified_by=classified_by,
                            )
                            stats["rule_matched"] += 1
                            stats["total"] += 1
                            distribution[rule_match.material_type] += 1
                            continue

                        # Submit LLM call to thread pool
                        client_idx = len(futures) % workers
                        fut = pool.submit(
                            self._classify_one, clients[client_idx], definition, row,
                            rate_limit_lock, rate_limit_times,
                        )
                        futures[fut] = row
                        active_futures.append(fut)

                    # Collect results
                    for fut in as_completed(futures):
                        if shutdown.is_set():
                            break
                        row = futures[fut]
                        try:
                            result = fut.result()
                        except Exception:
                            result = None

                        if result and not result.error:
                            cat = definition.get_category(result.material_type)
                            mg = cat.group if cat else (result.material_group or None)
                            model_name = result.classifier_model or backend
                            self._write_result(
                                engine, run_id, row["content_sha256"],
                                material_type=result.material_type,
                                material_group=mg,
                                event_type=result.event_type,
                                confidence=None,
                                reasoning=(result.reasoning or "")[:500],
                                classified_by=f"llm:{model_name}",
                            )
                            stats["llm_classified"] += 1
                            distribution[result.material_type] += 1
                            consecutive_failures = 0
                        else:
                            self._write_error(engine, run_id, row["content_sha256"])
                            stats["llm_errors"] += 1
                            consecutive_failures += 1
                            if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                                self.stderr.write(
                                    f"\nHALT: {_MAX_CONSECUTIVE_FAILURES} consecutive LLM failures.\n"
                                    f"Run --resume to continue after fixing the issue."
                                )
                                shutdown.set()
                                break
                        stats["total"] += 1

                    # Clean up active_futures
                    active_futures = [f for f in active_futures if not f.done()]

                    if not shutdown.is_set():
                        # Full batch completed — safe to advance cursor
                        previous_cursor = last_cursor
                        last_cursor = batch_max_sha
                        self._checkpoint(engine, run_id, last_cursor, stats)
                    else:
                        # Partial — drain remaining futures
                        remaining_futs = {f: r for f, r in futures.items() if not f.done()}
                        if remaining_futs:
                            done, not_done = wait(list(remaining_futs.keys()),
                                                  timeout=_SIGINT_DRAIN_TIMEOUT)
                            for fut in done:
                                row = futures[fut]
                                try:
                                    result = fut.result()
                                except Exception:
                                    result = None
                                if result and not result.error:
                                    cat = definition.get_category(result.material_type)
                                    mg = cat.group if cat else (result.material_group or None)
                                    model_name = result.classifier_model or backend
                                    self._write_result(
                                        engine, run_id, row["content_sha256"],
                                        material_type=result.material_type,
                                        material_group=mg,
                                        event_type=result.event_type,
                                        confidence=None,
                                        reasoning=(result.reasoning or "")[:500],
                                        classified_by=f"llm:{model_name}",
                                    )
                                    stats["llm_classified"] += 1
                                    distribution[result.material_type] += 1
                                else:
                                    self._write_error(engine, run_id, row["content_sha256"])
                                    stats["llm_errors"] += 1
                                stats["total"] += 1

                            for fut in not_done:
                                fut.cancel()

                            if not not_done:
                                last_cursor = batch_max_sha
                            else:
                                last_cursor = previous_cursor

                        self._checkpoint(engine, run_id, last_cursor, stats)
                        break

                    batch_elapsed = time.monotonic() - batch_start
                    batch_times.append((len(rows), batch_elapsed))
                    batch_num += 1

                    if not quiet:
                        self._print_progress(batch_num, stats, total_eligible,
                                             batch_times, allow_fallback)

                    if sample and stats["total"] >= sample:
                        break

        finally:
            watchdog.stop()
            if watchdog._thread is not None:
                watchdog._thread.join(timeout=2)
            signal.signal(signal.SIGINT, original_handler)

        if shutdown.is_set():
            self.stdout.write(f"\nInterrupted. Resume with --resume to continue.")
            self._print_summary(run_tag, stats, distribution, start_time)
            return

        # Finalize run
        with engine.begin() as conn:
            conn.execute(text(
                f"UPDATE {_SCHEMA}.classification_runs "
                f"SET finished_at = now(), stats_json = :stats "
                f"WHERE id = :run_id"
            ), {"run_id": run_id, "stats": json.dumps(dict(stats))})

        self._print_summary(run_tag, stats, distribution, start_time)

    def _classify_one(self, client, definition, row, rate_limit_lock, rate_limit_times):
        pages_text = row.get("pages_text") or ""
        first_page_text = row.get("first_page_text") or ""

        for attempt in range(_MAX_DOC_RETRIES):
            try:
                result = classify_first_page_v3(
                    first_page_text,
                    client=client,
                    definition=definition,
                    raise_on_error=False,
                    pages_text=pages_text if pages_text else None,
                    url_path=row.get("source_url") if row.get("source_url") else None,
                    page_count=row.get("total_pages"),
                    file_size_bytes=row.get("file_size"),
                    pdf_creator=row.get("pdf_creator") if row.get("pdf_creator") else None,
                )
                if result.error:
                    log.warning("LLM classification error (attempt %d): %s",
                                attempt + 1, result.error[:200])
                    is_rate_limit = "429" in result.error or "rate" in result.error.lower()
                    if is_rate_limit:
                        with rate_limit_lock:
                            rate_limit_times.append(time.monotonic())
                    if attempt < _MAX_DOC_RETRIES - 1:
                        base_delay = _RETRY_BASE_DELAYS[min(attempt, len(_RETRY_BASE_DELAYS) - 1)]
                        jitter = base_delay * 0.25 * (2 * random.random() - 1)
                        time.sleep(base_delay + jitter)
                        continue
                    return None
                return result
            except Exception as exc:
                if "refused" in str(exc):
                    log.warning("LLM refused content for sha=%s, skipping",
                                row.get("content_sha256", "?")[:12])
                    return None
                log.exception("LLM classification exception (attempt %d)", attempt + 1)
                if attempt < _MAX_DOC_RETRIES - 1:
                    base_delay = _RETRY_BASE_DELAYS[min(attempt, len(_RETRY_BASE_DELAYS) - 1)]
                    time.sleep(base_delay)
                    continue
                return None
        return None

    def _count_eligible(self, engine, *, state, ein, where_clause,
                        allow_fallback, min_text_len):
        filter_sql = ""
        params = {}
        if state:
            filter_sql += f" AND c.source_org_ein IN (SELECT ein FROM {_SCHEMA}.nonprofits_seed WHERE state = :state)"
            params["state"] = state
        if ein:
            filter_sql += " AND c.source_org_ein = :ein"
            params["ein"] = ein
        if where_clause:
            filter_sql += f" AND ({where_clause})"

        with engine.connect() as conn:
            # Total PDFs matching filters
            total_pdfs = conn.execute(text(
                f"SELECT COUNT(*) FROM {_SCHEMA}.corpus c "
                f"WHERE c.content_type = 'application/pdf'{filter_sql}"
            ), params).scalar()

            # With extraction context + quality gate
            with_context = conn.execute(text(
                f"SELECT COUNT(*) FROM {_SCHEMA}.corpus c "
                f"INNER JOIN {_SCHEMA}.classification_context cc "
                f"  ON cc.content_sha256 = c.content_sha256 "
                f"WHERE c.content_type = 'application/pdf' "
                f"  AND cc.text_length >= :min_text_len{filter_sql}"
            ), {**params, "min_text_len": min_text_len}).scalar()

            # With context but below quality gate
            with_context_all = conn.execute(text(
                f"SELECT COUNT(*) FROM {_SCHEMA}.corpus c "
                f"INNER JOIN {_SCHEMA}.classification_context cc "
                f"  ON cc.content_sha256 = c.content_sha256 "
                f"WHERE c.content_type = 'application/pdf'{filter_sql}"
            ), params).scalar()

        below_gate = with_context_all - with_context
        without_context = total_pdfs - with_context_all
        coverage = (with_context_all / total_pdfs * 100) if total_pdfs else 0

        eligible = with_context_all if not allow_fallback else total_pdfs
        return {
            "total_pdfs": total_pdfs,
            "with_context": with_context,
            "with_context_all": with_context_all,
            "below_gate": below_gate,
            "without_context": without_context,
            "coverage": coverage,
            "eligible": eligible,
        }

    def _filter_desc(self, state, ein, where_clause, sample):
        parts = []
        if state:
            parts.append(f"state={state}")
        if ein:
            parts.append(f"ein={ein}")
        if where_clause:
            parts.append(f"where=...")
        if sample:
            parts.append(f"sample={sample}")
        return ", ".join(parts) if parts else "none"

    def _print_startup(self, run_tag, backend, workers, batch_size, definition,
                       filter_desc, counts, allow_fallback):
        self.stdout.write(f"\nReclassify corpus (run tag: {run_tag})")
        self.stdout.write(f"  Backend: {backend} | Workers: {workers} | Batch size: {batch_size}")
        self.stdout.write(f"  Definition: {definition.name} v{definition.version} (context_mode: {definition.context_mode})")
        self.stdout.write(f"  Filter: {filter_desc}")
        self.stdout.write(f"  Eligible docs: {counts['eligible']:,}")
        self.stdout.write(f"  Docs without context (excluded): {counts['without_context']:,}")
        self.stdout.write(f"  Extraction coverage: {counts['coverage']:.1f}%")
        require_ctx = "NO (--allow-fallback)" if allow_fallback else "YES (use --allow-fallback to override)"
        self.stdout.write(f"  Require context: {require_ctx}")
        if allow_fallback and counts["without_context"] > 0:
            self.stdout.write(f"  Fallback docs (no context): {counts['without_context']:,} — will use first_page_text")
        self.stdout.write("")

    def _print_dry_run(self, counts, run_tag, filter_desc, backend, workers,
                       min_text_len, allow_fallback, engine, state, ein,
                       where_clause, sample):
        self.stdout.write(f"\nDry run ({run_tag}, {filter_desc}):")
        self.stdout.write(f"  Total eligible PDFs:           {counts['total_pdfs']:,}")
        with_ctx_total = counts['with_context'] + counts['below_gate']
        self.stdout.write(f"  With extraction context:       {with_ctx_total:,} ({counts['coverage']:.1f}%)")
        self.stdout.write(f"  Without context (excluded):    {counts['without_context']:,}")
        self.stdout.write(f"  Context text >= {min_text_len} chars:     {counts['with_context']:,}")
        self.stdout.write(f"  Context text < {min_text_len} chars:        {counts['below_gate']:,}")
        self.stdout.write("")

        would_process = counts["eligible"]
        if sample:
            would_process = min(would_process, sample)
        self.stdout.write(f"  Would process: {would_process:,}")

        if backend in _COST_PER_DOC:
            est_cost = would_process * _COST_PER_DOC[backend]
            est_secs = would_process / (_DOCS_PER_SEC_PER_WORKER * workers)
            self.stdout.write("")
            self.stdout.write(f"  Estimated cost ({backend}): ~${est_cost:.2f}")
            self.stdout.write(f"  Estimated time ({workers} workers): ~{_format_duration(est_secs)}")
        else:
            self.stdout.write(f"  Estimated cost/time: unavailable for {backend}")

    def _init_run(self, engine, run_tag, resume, rule_engine, definition, backend,
                  state, ein, where_clause, allow_fallback, min_text_len, sample):
        cursor = None
        stored_config = None

        if resume:
            with engine.connect() as conn:
                row = conn.execute(text(
                    f"SELECT id, config_json FROM {_SCHEMA}.classification_runs "
                    f"WHERE run_tag = :tag AND finished_at IS NULL "
                    f"ORDER BY started_at DESC LIMIT 1"
                ), {"tag": run_tag}).fetchone()
                if row:
                    run_id = row[0]
                    config = row[1] or {}
                    if isinstance(config, str):
                        config = json.loads(config)
                    cursor = config.get("cursor")

                    # Validate execution parameters match stored config
                    stored_filters = config.get("filters", {})
                    provided_filters = {"state": state, "ein": ein, "where": where_clause}
                    if stored_filters != provided_filters:
                        if any(v is not None for v in provided_filters.values()):
                            self.stderr.write(
                                f"ERROR: Filters changed since original run.\n"
                                f"  Original: {stored_filters}\n"
                                f"  Provided: {provided_filters}\n"
                                f"Resume uses the original filters. Remove conflicting flags."
                            )
                            return None, None, None

                    param_mismatches = []
                    if backend != config.get("backend", backend):
                        param_mismatches.append(f"backend: stored={config['backend']}, provided={backend}")
                    if definition.name != config.get("definition", definition.name):
                        param_mismatches.append(f"definition: stored={config['definition']}, provided={definition.name}")
                    if allow_fallback != config.get("allow_fallback", allow_fallback):
                        param_mismatches.append(f"allow_fallback: stored={config['allow_fallback']}, provided={allow_fallback}")
                    if min_text_len != config.get("min_text_len", min_text_len):
                        param_mismatches.append(f"min_text_len: stored={config['min_text_len']}, provided={min_text_len}")
                    if param_mismatches:
                        self.stderr.write(
                            f"ERROR: Execution parameters changed since original run.\n"
                            + "".join(f"  {m}\n" for m in param_mismatches)
                            + "Resume uses the original parameters. Remove conflicting flags."
                        )
                        return None, None, None

                    if config.get("sample"):
                        self.stderr.write("ERROR: --sample runs cannot be resumed.")
                        return None, None, None

                    log.info("Resuming run %d from cursor %s", run_id, cursor)
                    return run_id, cursor, config
                else:
                    self.stderr.write(f"No incomplete run found for tag {run_tag!r}.")
                    return None, None, None

        # Check for existing completed runs with same tag
        with engine.connect() as conn:
            existing = conn.execute(text(
                f"SELECT id, finished_at FROM {_SCHEMA}.classification_runs "
                f"WHERE run_tag = :tag"
            ), {"tag": run_tag}).fetchall()

        completed = [r for r in existing if r[1] is not None]
        if completed:
            self.stderr.write(
                f"ERROR: Run tag '{run_tag}' already exists (completed). "
                f"Use a different tag or delete the existing run."
            )
            return None, None, None

        rules_yaml = Path(_RULES_PATH)
        if not rules_yaml.is_file():
            rules_yaml = Path(__file__).resolve().parents[4] / "nonprofits" / "definitions" / "prefilter_rules.yaml"
        rules_content = rules_yaml.read_text() if rules_yaml.is_file() else ""

        config_json = {
            "backend": backend,
            "definition": definition.name,
            "definition_version": definition.version,
            "rules_sha256": rule_engine.rules_sha256,
            "filters": {"state": state, "ein": ein, "where": where_clause},
            "allow_fallback": allow_fallback,
            "min_text_len": min_text_len,
            "sample": sample,
        }

        with engine.begin() as conn:
            result = conn.execute(text(
                f"INSERT INTO {_SCHEMA}.classification_runs "
                f"(run_tag, config_json, rules_snapshot) "
                f"VALUES (:tag, :config, :rules) "
                f"RETURNING id"
            ), {
                "tag": run_tag,
                "config": json.dumps(config_json),
                "rules": rules_content,
            })
            run_id = result.scalar()

        return run_id, cursor, None

    def _fetch_batch(self, engine, run_id, cursor, sample, where_clause,
                     state, ein, allow_fallback, min_text_len, batch_size):
        join_type = "LEFT" if allow_fallback else "INNER"

        sql = (
            f"SELECT c.content_sha256, c.first_page_text, c.source_url_redacted, "
            f"  c.file_size_bytes, c.pdf_creator, c.source_org_ein, "
            f"  cc.pages_text, cc.pages_extracted, cc.total_pages, "
            f"  cc.text_length "
            f"FROM {_SCHEMA}.corpus c "
            f"{join_type} JOIN {_SCHEMA}.classification_context cc "
            f"  ON cc.content_sha256 = c.content_sha256 "
            f"LEFT JOIN {_SCHEMA}.classification_results cr "
            f"  ON cr.content_sha256 = c.content_sha256 AND cr.run_id = :run_id "
            f"WHERE c.content_type = 'application/pdf' "
            f"  AND cr.content_sha256 IS NULL"
        )
        params = {"run_id": run_id, "batch_size": batch_size}

        if state:
            sql += f" AND c.source_org_ein IN (SELECT ein FROM {_SCHEMA}.nonprofits_seed WHERE state = :state)"
            params["state"] = state

        if ein:
            sql += " AND c.source_org_ein = :ein"
            params["ein"] = ein

        if where_clause:
            sql += f" AND ({where_clause})"

        if not sample and cursor:
            sql += " AND c.content_sha256 > :cursor"
            params["cursor"] = cursor

        if sample:
            sql += " ORDER BY random() LIMIT :batch_size"
        else:
            sql += " ORDER BY c.content_sha256 LIMIT :batch_size"

        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()

        return [
            {
                "content_sha256": r[0],
                "first_page_text": r[1] or "",
                "source_url": r[2] or "",
                "file_size": r[3],
                "pdf_creator": r[4] or "",
                "source_org_ein": r[5] or "",
                "pages_text": r[6] or "",
                "total_pages": r[8],
                "text_length": r[9],
            }
            for r in rows
        ]

    def _write_result(self, engine, run_id, sha, *, material_type, material_group,
                      event_type, confidence, reasoning, classified_by):
        with engine.begin() as conn:
            conn.execute(text(
                f"INSERT INTO {_SCHEMA}.classification_results "
                f"(run_id, content_sha256, material_type, material_group, event_type, "
                f" confidence, reasoning, classified_by) "
                f"VALUES (:run_id, :sha, :mt, :mg, :et, :conf, :reasoning, :cb) "
                f"ON CONFLICT (run_id, content_sha256) DO NOTHING"
            ), {
                "run_id": run_id, "sha": sha, "mt": material_type,
                "mg": material_group, "et": event_type,
                "conf": confidence, "reasoning": reasoning, "cb": classified_by,
            })

    def _write_error(self, engine, run_id, sha):
        self._write_result(
            engine, run_id, sha,
            material_type=None, material_group=None, event_type=None,
            confidence=None,
            reasoning="LLM classification failed after 4 attempts",
            classified_by="llm:error",
        )

    def _checkpoint(self, engine, run_id, cursor, stats):
        config_update = {"cursor": cursor}
        stats_snapshot = dict(stats)
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    f"UPDATE {_SCHEMA}.classification_runs "
                    f"SET config_json = config_json || :config, stats_json = :stats "
                    f"WHERE id = :run_id"
                ), {
                    "run_id": run_id,
                    "config": json.dumps(config_update),
                    "stats": json.dumps(stats_snapshot),
                })
        except Exception:
            log.exception("Checkpoint failed for run %d", run_id)

    def _print_progress(self, batch_num, stats, total, batch_times, allow_fallback):
        now = time.strftime("%H:%M:%S")
        processed = stats["total"]
        pct = (processed / total * 100) if total else 0

        if batch_times:
            recent_docs = sum(d for d, _ in batch_times)
            recent_time = sum(t for _, t in batch_times)
            throughput = recent_docs / recent_time if recent_time > 0 else 0
        else:
            throughput = 0

        remaining = total - processed
        if throughput > 0 and len(batch_times) >= 2:
            eta_secs = remaining / throughput
            eta = _format_duration(eta_secs)
        else:
            eta = "--"

        skip_total = stats.get("skip_ctx", 0) + stats.get("skip_fp", 0)
        if allow_fallback:
            skip_str = f"skip(ctx):{stats.get('skip_ctx', 0)} skip(fp):{stats.get('skip_fp', 0)}"
        else:
            skip_str = f"skip:{skip_total}"

        self.stdout.write(
            f"[{now}] Batch {batch_num} | "
            f"{processed:,}/{total:,} ({pct:.1f}%) | "
            f"{throughput:.1f} docs/sec | ETA {eta} | "
            f"rules:{stats.get('rule_matched', 0)} "
            f"llm:{stats.get('llm_classified', 0)} "
            f"{skip_str} "
            f"err:{stats.get('llm_errors', 0)}"
        )

    def _print_summary(self, run_tag, stats, distribution, start_time):
        elapsed = time.monotonic() - start_time
        total = stats["total"]
        avg_throughput = total / elapsed if elapsed > 0 else 0

        self.stdout.write(f"\nReclassification complete ({run_tag})")
        self.stdout.write(f"  Total: {total:,} | Elapsed: {_format_duration(elapsed)} | Avg: {avg_throughput:.1f} docs/sec")
        self.stdout.write(f"  Rule-matched:    {stats.get('rule_matched', 0):,} ({stats.get('rule_matched', 0)/total*100:.1f}%)" if total else f"  Rule-matched:    0")
        self.stdout.write(f"  LLM-classified:  {stats.get('llm_classified', 0):,} ({stats.get('llm_classified', 0)/total*100:.1f}%)" if total else f"  LLM-classified:  0")
        skip_total = stats.get("skip_ctx", 0) + stats.get("skip_fp", 0)
        self.stdout.write(f"  Skipped (short): {skip_total:,} ({skip_total/total*100:.1f}%)" if total else f"  Skipped (short): 0")
        self.stdout.write(f"  LLM errors:      {stats.get('llm_errors', 0):,} ({stats.get('llm_errors', 0)/total*100:.1f}%)" if total else f"  LLM errors:      0")

        if distribution:
            self.stdout.write(f"\n  Distribution:")
            for mt, cnt in sorted(distribution.items(), key=lambda x: -x[1]):
                pct = cnt / total * 100 if total else 0
                self.stdout.write(f"    {mt:30s} {cnt:>6,} ({pct:.1f}%)")

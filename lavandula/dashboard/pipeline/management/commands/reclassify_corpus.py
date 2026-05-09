"""Reclassify the corpus using rule pre-filter + augmented LLM prompt (Spec 0035).

Usage:
    python3 manage.py reclassify_corpus --run-tag v3.1
    python3 manage.py reclassify_corpus --run-tag v3.1 --sample 1000
    python3 manage.py reclassify_corpus --run-tag v3.1 --dry-run
    python3 manage.py reclassify_corpus --run-tag v3.1 --backend haiku
    python3 manage.py reclassify_corpus --run-tag v3.1 --resume
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from django.core.management.base import BaseCommand
from django.utils import timezone
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
from lavandula.reports.taxonomy import material_type_to_legacy

log = logging.getLogger(__name__)

_SCHEMA = "lava_corpus"
_BATCH_SIZE = 500
_MAX_CONSECUTIVE_FAILURES = 20
_MAX_DOC_FAILURES = 4
_RETRY_DELAYS = [2, 4, 8, 16]
_MIN_TEXT_LEN = 50

_RULES_PATH = Path(__file__).resolve().parents[5] / "lavandula" / "nonprofits" / "definitions" / "prefilter_rules.yaml"


class Command(BaseCommand):
    help = "Reclassify corpus using rule pre-filter + augmented LLM"

    def add_arguments(self, parser):
        parser.add_argument("--run-tag", required=True, help="Tag for this classification run")
        parser.add_argument("--sample", type=int, default=None, help="Random sample size")
        parser.add_argument("--where", type=str, default=None, help="Additional WHERE clause")
        parser.add_argument("--dry-run", action="store_true", help="Show counts without writing")
        parser.add_argument("--backend", choices=["deepseek", "haiku", "gemini", "claude"],
                            default="deepseek", help="LLM backend")
        parser.add_argument("--workers", type=int, default=4, help="Concurrent workers")
        parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint")
        parser.add_argument("--definition", type=str, default=None, help="Classifier definition name")

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
        resume = options["resume"]
        def_name = resolve_definition_name(options.get("definition"))

        rules_path = _RULES_PATH
        if not rules_path.is_file():
            rules_path = Path(__file__).resolve().parents[4] / "nonprofits" / "definitions" / "prefilter_rules.yaml"

        rule_engine = RuleEngine(rules_path)
        definition = load_definition(def_name)

        if dry_run:
            self._dry_run(engine, sample, where_clause)
            return

        client = select_classifier_client(backend=backend)

        run_id, cursor = self._init_run(engine, run_tag, resume, rule_engine, definition, backend)

        stats = defaultdict(int)
        consecutive_failures = 0
        domain_not_relevant = defaultdict(int)
        domain_total = defaultdict(int)
        suppression_disagreements = 0
        classifying = [0]

        from lavandula.reports.stall_watchdog import StallWatchdog

        watchdog = StallWatchdog(
            get_progress=lambda: stats["total"],
            get_active=lambda: classifying[0],
            stall_threshold_sec=600,
            notify_email=os.environ.get("WATCHDOG_NOTIFY_EMAIL"),
        )
        watchdog.start_thread()

        last_cursor = cursor or ""
        batch_num = 0

        try:
            while True:
                rows = self._fetch_batch(engine, run_id, last_cursor, sample, where_clause)
                if not rows:
                    break

                for row in rows:
                    sha = row["content_sha256"]
                    last_cursor = sha

                    pages_text = row.get("pages_text") or ""
                    first_page_text = row.get("first_page_text") or ""
                    source_url = row.get("source_url") or ""
                    file_size = row.get("file_size")
                    pdf_creator = row.get("pdf_creator") or ""
                    total_pages = row.get("total_pages")

                    domain = self._extract_domain(source_url)
                    domain_total[domain] += 1

                    eval_text = pages_text or first_page_text

                    # Step A: Rule pre-filter
                    rule_match = rule_engine.evaluate(
                        text=eval_text, url=source_url,
                        pdf_creator=pdf_creator, page_count=total_pages,
                        file_size=file_size,
                    )

                    if rule_match is not None:
                        classified_by = f"rule:{rule_match.rule_name}@{rule_match.rules_sha256[:8]}"
                        cat = definition.get_category(rule_match.material_type)
                        mg = cat.group if cat else None
                        self._write_result(
                            engine, run_id, sha,
                            material_type=rule_match.material_type,
                            material_group=mg,
                            event_type=None,
                            confidence=rule_match.confidence,
                            reasoning=rule_match.reasoning[:500],
                            classified_by=classified_by,
                        )
                        stats["rule_matched"] += 1
                        stats["total"] += 1

                        if rule_match.material_type == "not_relevant":
                            domain_not_relevant[domain] += 1

                            # Deterministic 5% suppression sampling
                            sample_hash = hashlib.sha256(
                                f"{run_id}:{sha}".encode()
                            ).digest()[0]
                            if sample_hash < 13:  # 13/256 ≈ 5.1%
                                classifying[0] = 1
                                try:
                                    llm_result = self._classify_llm(
                                        client, definition, first_page_text,
                                        pages_text=pages_text, url_path=source_url,
                                        page_count=total_pages,
                                        file_size_bytes=file_size,
                                        pdf_creator=pdf_creator,
                                    )
                                finally:
                                    classifying[0] = 0
                                if llm_result and llm_result.material_type != rule_match.material_type:
                                    suppression_disagreements += 1
                                    log.info(
                                        "Suppression disagreement: sha=%s rule=%s llm=%s",
                                        sha[:12], rule_match.material_type,
                                        llm_result.material_type,
                                    )
                        continue

                    # Step B: Insufficient text check
                    if len(eval_text.strip()) < _MIN_TEXT_LEN:
                        self._write_result(
                            engine, run_id, sha,
                            material_type="other_collateral",
                            material_group="other",
                            event_type=None,
                            confidence=0.1,
                            reasoning="Insufficient text for classification",
                            classified_by="rule:insufficient_text",
                        )
                        stats["insufficient_text"] += 1
                        stats["total"] += 1
                        continue

                    # Step C: LLM classification
                    result = None
                    for attempt in range(_MAX_DOC_FAILURES):
                        classifying[0] = 1
                        try:
                            result = self._classify_llm(
                                client, definition, first_page_text,
                                pages_text=pages_text, url_path=source_url,
                                page_count=total_pages,
                                file_size_bytes=file_size,
                                pdf_creator=pdf_creator,
                            )
                        finally:
                            classifying[0] = 0
                        if result is not None:
                            consecutive_failures = 0
                            break

                        consecutive_failures += 1
                        log.warning("LLM failure attempt %d for sha=%s", attempt + 1, sha[:12])
                        if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                            self._checkpoint(engine, run_id, last_cursor, stats,
                                             suppression_disagreements, domain_not_relevant,
                                             domain_total)
                            self.stderr.write(
                                f"Halting: {_MAX_CONSECUTIVE_FAILURES} consecutive failures. "
                                f"Resume with --resume."
                            )
                            return
                        if attempt < _MAX_DOC_FAILURES - 1:
                            delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                            time.sleep(delay)

                    if result is None:
                        self._write_result(
                            engine, run_id, sha,
                            material_type=None, material_group=None, event_type=None,
                            confidence=0.0, reasoning="LLM classification failed",
                            classified_by="llm:error",
                        )
                        stats["llm_errors"] += 1
                        stats["total"] += 1
                        continue

                    cat = definition.get_category(result.material_type)
                    mg = cat.group if cat else (result.material_group or None)
                    model_name = getattr(client, "_cli_model", None) or getattr(client, "_model", backend)
                    self._write_result(
                        engine, run_id, sha,
                        material_type=result.material_type,
                        material_group=mg,
                        event_type=result.event_type,
                        confidence=result.classification_confidence or 0.0,
                        reasoning=(result.reasoning or "")[:500],
                        classified_by=f"llm:{model_name}",
                    )
                    stats["llm_classified"] += 1
                    stats["total"] += 1

                batch_num += 1
                self._checkpoint(engine, run_id, last_cursor, stats,
                                 suppression_disagreements, domain_not_relevant,
                                 domain_total)

                if sample and stats["total"] >= sample:
                    break
        finally:
            watchdog.stop()
            if watchdog._thread is not None:
                watchdog._thread.join(timeout=2)

        # Post-run: finalize
        domain_warnings = []
        for domain, nr_count in domain_not_relevant.items():
            total = domain_total.get(domain, 0)
            if total > 0 and nr_count / total > 0.5:
                domain_warnings.append(f"{domain}: {nr_count}/{total} ({nr_count/total:.0%})")

        final_stats = dict(stats)
        final_stats["suppression_sample_disagreements"] = suppression_disagreements
        final_stats["per_domain_suppression_warnings"] = domain_warnings

        with engine.begin() as conn:
            conn.execute(text(
                f"UPDATE {_SCHEMA}.classification_runs "
                f"SET finished_at = now(), stats_json = :stats "
                f"WHERE id = :run_id"
            ), {"run_id": run_id, "stats": json.dumps(final_stats)})

        self.stdout.write(f"\nReclassification complete ({run_tag}):")
        self.stdout.write(f"  Total: {stats['total']}")
        self.stdout.write(f"  Rule-matched: {stats.get('rule_matched', 0)}")
        self.stdout.write(f"  LLM-classified: {stats.get('llm_classified', 0)}")
        self.stdout.write(f"  Insufficient text: {stats.get('insufficient_text', 0)}")
        self.stdout.write(f"  LLM errors: {stats.get('llm_errors', 0)}")
        self.stdout.write(f"  Suppression disagreements: {suppression_disagreements}")
        if domain_warnings:
            self.stdout.write("  Domain suppression warnings:")
            for w in domain_warnings:
                self.stdout.write(f"    {w}")

    def _dry_run(self, engine, sample, where_clause):
        base_sql = (
            f"SELECT COUNT(*) FROM {_SCHEMA}.corpus c "
            f"WHERE c.content_type = 'application/pdf'"
        )
        if where_clause:
            base_sql += f" AND ({where_clause})"

        with engine.connect() as conn:
            total = conn.execute(text(base_sql)).scalar()

        effective = min(total, sample) if sample else total
        self.stdout.write(f"Dry run: {effective} docs would be processed (total eligible: {total})")

    def _init_run(self, engine, run_tag, resume, rule_engine, definition, backend):
        cursor = None
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
                    cursor = config.get("cursor")
                    log.info("Resuming run %d from cursor %s", run_id, cursor)
                    return run_id, cursor

        rules_yaml = Path(_RULES_PATH)
        if not rules_yaml.is_file():
            rules_yaml = Path(__file__).resolve().parents[4] / "nonprofits" / "definitions" / "prefilter_rules.yaml"
        rules_content = rules_yaml.read_text() if rules_yaml.is_file() else ""

        config_json = {
            "backend": backend,
            "definition": definition.name,
            "definition_version": definition.version,
            "rules_sha256": rule_engine.rules_sha256,
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

        return run_id, cursor

    def _fetch_batch(self, engine, run_id, cursor, sample, where_clause):
        sql = (
            f"SELECT c.content_sha256, c.first_page_text, c.source_url, "
            f"  c.file_size, c.pdf_creator, "
            f"  cc.pages_text, cc.pages_extracted, cc.total_pages "
            f"FROM {_SCHEMA}.corpus c "
            f"LEFT JOIN {_SCHEMA}.classification_context cc "
            f"  ON cc.content_sha256 = c.content_sha256 "
            f"LEFT JOIN {_SCHEMA}.classification_results cr "
            f"  ON cr.content_sha256 = c.content_sha256 AND cr.run_id = :run_id "
            f"WHERE c.content_type = 'application/pdf' "
            f"  AND cr.content_sha256 IS NULL "
        )
        if not sample:
            sql += f"  AND c.content_sha256 > :cursor "
        if where_clause:
            sql += f" AND ({where_clause}) "
        if sample:
            sql += f"ORDER BY random() LIMIT :batch_size"
        else:
            sql += f"ORDER BY c.content_sha256 LIMIT :batch_size"

        params = {"run_id": run_id, "batch_size": _BATCH_SIZE}
        if not sample:
            params["cursor"] = cursor

        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()

        return [
            {
                "content_sha256": r[0],
                "first_page_text": r[1] or "",
                "source_url": r[2] or "",
                "file_size": r[3],
                "pdf_creator": r[4] or "",
                "pages_text": r[5] or "",
                "total_pages": r[7],
            }
            for r in rows
        ]

    def _classify_llm(self, client, definition, first_page_text, *,
                       pages_text, url_path, page_count, file_size_bytes, pdf_creator):
        try:
            result = classify_first_page_v3(
                first_page_text,
                client=client,
                definition=definition,
                raise_on_error=False,
                pages_text=pages_text if pages_text else None,
                url_path=url_path if url_path else None,
                page_count=page_count,
                file_size_bytes=file_size_bytes,
                pdf_creator=pdf_creator if pdf_creator else None,
            )
            if result.error:
                log.warning("LLM classification error: %s", result.error[:200])
                return None
            return result
        except Exception:
            log.exception("LLM classification exception")
            return None

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

    def _checkpoint(self, engine, run_id, cursor, stats,
                     suppression_disagreements, domain_not_relevant, domain_total):
        config_update = {"cursor": cursor}
        stats_snapshot = dict(stats)
        stats_snapshot["suppression_sample_disagreements"] = suppression_disagreements
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

    @staticmethod
    def _extract_domain(url: str) -> str:
        if not url:
            return "unknown"
        try:
            parsed = urlparse(url)
            return parsed.netloc or "unknown"
        except Exception:
            return "unknown"

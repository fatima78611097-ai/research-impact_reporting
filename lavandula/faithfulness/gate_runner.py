"""Faithfulness gate runner — batch verification of LLM-extracted facts.

Iterates a run's llm_metrics and llm_stories, pulls source text via the
SourceTextProvider, runs the grounding verifier, writes verification_tier
/ grounding_rule / grounding_offsets, and emits a per-run faithfulness
score. Deterministic and idempotent.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from lavandula.faithfulness.grounding import (
    MAX_CONTEXT_WINDOW_CHARS,
    Verdict,
    check,
    check_story,
    normalize,
)
from lavandula.faithfulness.source_provider import SourceText, SourceTextProvider

log = logging.getLogger(__name__)

TIER_VERIFIED = "verified"
TIER_UNVERIFIED_OCR = "unverified_ocr"
TIER_UNVERIFIED_PENDING = "unverified_pending"
TIER_QUARANTINE = "quarantine"
TIER_UNVERIFIED_LEGACY = "unverified_legacy"


@dataclass
class RunStats:
    total_metrics: int = 0
    total_stories: int = 0
    metrics_verified: int = 0
    metrics_ocr: int = 0
    metrics_pending: int = 0
    metrics_quarantined: int = 0
    stories_verified: int = 0
    stories_ocr: int = 0
    stories_pending: int = 0
    stories_quarantined: int = 0
    docs_processed: int = 0
    docs_missing_source: int = 0
    elapsed_s: float = 0.0

    @property
    def metrics_grounding_rate(self) -> float:
        if self.total_metrics == 0:
            return 0.0
        return self.metrics_verified / self.total_metrics

    @property
    def stories_grounding_rate(self) -> float:
        if self.total_stories == 0:
            return 0.0
        return self.stories_verified / self.total_stories

    def to_dict(self) -> dict:
        return {
            "total_metrics": self.total_metrics,
            "total_stories": self.total_stories,
            "metrics_verified": self.metrics_verified,
            "metrics_ocr": self.metrics_ocr,
            "metrics_pending": self.metrics_pending,
            "metrics_quarantined": self.metrics_quarantined,
            "stories_verified": self.stories_verified,
            "stories_ocr": self.stories_ocr,
            "stories_pending": self.stories_pending,
            "stories_quarantined": self.stories_quarantined,
            "metrics_grounding_rate": round(self.metrics_grounding_rate, 4),
            "stories_grounding_rate": round(self.stories_grounding_rate, 4),
            "docs_processed": self.docs_processed,
            "docs_missing_source": self.docs_missing_source,
            "elapsed_s": round(self.elapsed_s, 1),
        }


def _assign_tier(
    verdict: Verdict,
    source: SourceText | None,
) -> str:
    """Assign verification tier based on verdict and source provenance.

    Fail-safe: absence of source or certification never yields 'verified'.
    """
    if source is None:
        return TIER_QUARANTINE

    if not verdict.grounded:
        return TIER_QUARANTINE

    if source.source == "pdftotext-repaired":
        return TIER_UNVERIFIED_OCR

    return TIER_VERIFIED


def _extract_context_window(
    source_text: str, offsets: list[tuple[int, int]]
) -> str | None:
    """Extract a bounded context window around grounding offsets."""
    if not offsets or not source_text:
        return None

    source_norm = normalize(source_text)
    start = offsets[0][0]
    end = offsets[-1][1]

    ctx_start = max(0, start - 100)
    ctx_end = min(len(source_norm), end + 100)

    window = source_norm[ctx_start:ctx_end]
    if len(window) > MAX_CONTEXT_WINDOW_CHARS:
        window = window[:MAX_CONTEXT_WINDOW_CHARS]

    return window


def verify_run(
    engine: Engine,
    provider: SourceTextProvider,
    run_id: int,
    *,
    progress_callback=None,
) -> RunStats:
    """Run the faithfulness gate over all metrics and stories in a run.

    Deterministic and idempotent: re-running produces identical results.
    """
    stats = RunStats()
    start_time = time.monotonic()

    shas = _get_run_shas(engine, run_id)
    source_cache: dict[str, SourceText | None] = {}

    for sha_idx, sha in enumerate(shas):
        source = source_cache.get(sha)
        if source is None and sha not in source_cache:
            source = provider.get(sha)
            source_cache[sha] = source

        if source is None:
            stats.docs_missing_source += 1

        _verify_metrics_for_doc(engine, run_id, sha, source, stats)
        _verify_stories_for_doc(engine, run_id, sha, source, stats)

        stats.docs_processed += 1

        if progress_callback and (sha_idx + 1) % 100 == 0:
            stats.elapsed_s = time.monotonic() - start_time
            progress_callback(stats)

    stats.elapsed_s = time.monotonic() - start_time
    _write_run_faithfulness_score(engine, run_id, stats)
    return stats


def _get_run_shas(engine: Engine, run_id: int) -> list[str]:
    """Get all distinct content_sha256 values for a run."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT content_sha256 FROM (
                SELECT content_sha256 FROM lava_vocab.llm_metrics WHERE run_id = :run_id
                UNION
                SELECT content_sha256 FROM lava_vocab.llm_stories WHERE run_id = :run_id
            ) combined
            ORDER BY content_sha256
        """), {"run_id": run_id}).fetchall()
    return [r[0] for r in rows]


def _verify_metrics_for_doc(
    engine: Engine,
    run_id: int,
    sha: str,
    source: SourceText | None,
    stats: RunStats,
) -> None:
    """Verify all metrics for one document."""
    with engine.connect() as conn:
        metrics = conn.execute(text("""
            SELECT id, source_snippet, metric_value
            FROM lava_vocab.llm_metrics
            WHERE run_id = :run_id AND content_sha256 = :sha
        """), {"run_id": run_id, "sha": sha}).fetchall()

    for metric_id, snippet, value in metrics:
        stats.total_metrics += 1

        if source is None:
            _update_metric_tier(engine, metric_id, TIER_QUARANTINE, None, None, None, None)
            stats.metrics_quarantined += 1
            continue

        verdict = check(
            snippet=snippet or "",
            source_text=source.section_text,
            tables=source.tables,
            value=value,
        )

        tier = _assign_tier(verdict, source)
        offsets_json = json.dumps(verdict.offsets) if verdict.offsets else None
        context = _extract_context_window(source.section_text, verdict.offsets)

        _update_metric_tier(
            engine, metric_id, tier, verdict.rule,
            source.source, offsets_json, context,
        )

        if tier == TIER_VERIFIED:
            stats.metrics_verified += 1
        elif tier == TIER_UNVERIFIED_OCR:
            stats.metrics_ocr += 1
        elif tier == TIER_UNVERIFIED_PENDING:
            stats.metrics_pending += 1
        else:
            stats.metrics_quarantined += 1


def _verify_stories_for_doc(
    engine: Engine,
    run_id: int,
    sha: str,
    source: SourceText | None,
    stats: RunStats,
) -> None:
    """Verify all stories for one document."""
    with engine.connect() as conn:
        stories = conn.execute(text("""
            SELECT id, source_snippet, story_summary
            FROM lava_vocab.llm_stories
            WHERE run_id = :run_id AND content_sha256 = :sha
        """), {"run_id": run_id, "sha": sha}).fetchall()

    for story_id, snippet, summary in stories:
        stats.total_stories += 1

        if source is None:
            _update_story_tier(engine, story_id, TIER_QUARANTINE, None, None, None, None)
            stats.stories_quarantined += 1
            continue

        verdict = check_story(
            source_snippet=snippet,
            story_summary=summary,
            source_text=source.section_text,
            tables=source.tables,
        )

        tier = _assign_tier(verdict, source)
        offsets_json = json.dumps(verdict.offsets) if verdict.offsets else None
        context = _extract_context_window(source.section_text, verdict.offsets)

        _update_story_tier(
            engine, story_id, tier, verdict.rule,
            source.source, offsets_json, context,
        )

        if tier == TIER_VERIFIED:
            stats.stories_verified += 1
        elif tier == TIER_UNVERIFIED_OCR:
            stats.stories_ocr += 1
        elif tier == TIER_UNVERIFIED_PENDING:
            stats.stories_pending += 1
        else:
            stats.stories_quarantined += 1


def _update_metric_tier(
    engine: Engine,
    metric_id: int,
    tier: str,
    rule: str | None,
    grounding_source: str | None,
    offsets_json: str | None,
    context: str | None,
) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE lava_vocab.llm_metrics
            SET verification_tier = :tier,
                grounding_rule = :rule,
                grounding_source = :source,
                grounding_offsets = CAST(:offsets AS JSONB),
                context_window = :context
            WHERE id = :id
        """), {
            "id": metric_id,
            "tier": tier,
            "rule": rule,
            "source": grounding_source,
            "offsets": offsets_json,
            "context": context,
        })


def _update_story_tier(
    engine: Engine,
    story_id: int,
    tier: str,
    rule: str | None,
    grounding_source: str | None,
    offsets_json: str | None,
    context: str | None,
) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE lava_vocab.llm_stories
            SET verification_tier = :tier,
                grounding_rule = :rule,
                grounding_source = :source,
                grounding_offsets = CAST(:offsets AS JSONB),
                context_window = :context
            WHERE id = :id
        """), {
            "id": story_id,
            "tier": tier,
            "rule": rule,
            "source": grounding_source,
            "offsets": offsets_json,
            "context": context,
        })


def _write_run_faithfulness_score(
    engine: Engine, run_id: int, stats: RunStats
) -> None:
    """Write the per-run faithfulness score to extraction_runs.stats_json."""
    score = {
        "faithfulness": stats.to_dict(),
    }
    with engine.begin() as conn:
        existing = conn.execute(text("""
            SELECT stats_json FROM lava_vocab.extraction_runs WHERE id = :run_id
        """), {"run_id": run_id}).fetchone()

        if existing:
            current = existing[0] if isinstance(existing[0], dict) else json.loads(existing[0] or "{}")
            current.update(score)
            conn.execute(text("""
                UPDATE lava_vocab.extraction_runs
                SET stats_json = CAST(:stats AS JSONB)
                WHERE id = :run_id
            """), {"run_id": run_id, "stats": json.dumps(current)})

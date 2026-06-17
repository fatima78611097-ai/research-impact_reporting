"""Marker-grounded gate runner (Spec 0069, Phase 4).

Consumes a 0068 marker run and writes a publish/quarantine decision onto every
marker-bearing ``llm_metrics`` row, in place, under a server-assigned ``gate_run_id``:

    advisory lock on source_run_id  ->  allocate gate_runs row (server id)
    ->  per doc: re-render (0068 marker_render) -> stale check -> per-metric decide
        (oracle + policies + LLM measurable/is-a-metric signals) -> prose de-dup
    ->  atomic in-place UPDATE of gate_* columns (active-run guarded)
    ->  per-run report + quarantine triage to extraction_runs.stats_json.

Integrity (spec §5.6/§8, plan §4):
  - **Concurrency** (S7): a Postgres advisory lock keyed on ``source_run_id`` — a second
    runner on the same source run fails gracefully (no interleaved gate_run_id, no
    duplicate measurable-check calls).
  - **In-place idempotency** (AC9): re-gating UPDATEs the same rows; the 0068 marker
    payload is never deleted/touched. Deterministic inputs -> identical decisions.
  - **Active-run guard**: the UPDATE only advances ``gate_run_id`` (``<= :new_id``); a
    stale/older run can never overwrite the current decision.
  - **Parameterized writes** (Gemini MEDIUM): all SQL is parameterized; ``gate_reason``
    is a fixed enum token, never reflected source text.
  - **Fail-safe** (§8/§9): any re-render error / uncertainty quarantines (never publish);
    a small int whose measurable check is unavailable -> ``measure_unchecked``.

The LLM boundary (``measure_fn``) is injectable so the orchestration is testable without
network; ``write=False`` is a fully read-only dry run.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from lavandula.common.lock_keys import GATE_RUNNER_BASE
from lavandula.nlp import gate, gate_policy
from lavandula.nlp import measure_check as mc
from lavandula.nlp.marker_render import RENDER_VERSION, SkipDocument, render_tagged

log = logging.getLogger(__name__)

GATE_VERSION = "marker-gate-0069.v1"

# Quarantine-reason -> triage bucket (spec §5.7): recoverable-by-relabel /
# recoverable-by-vision / true-junk. Recoverable feeds 0070, not the publish set.
TRIAGE = {
    "mispair": "recoverable_relabel",
    "subject_not_grounded": "recoverable_relabel",
    "unmarked": "recoverable_vision",
    "stale_coords": "recoverable_vision",
    "value_not_at_marker": "recoverable_vision",
    "no_numeric_value": "true_junk",
    "value_out_of_bounds": "true_junk",
    "not_a_metric": "true_junk",
    "not_a_metric_rule": "true_junk",
    "measure_unchecked": "true_junk",
    "duplicate": "true_junk",
    "gate_timeout": "true_junk",
    "render_error": "recoverable_vision",
    "idmap_too_large": "recoverable_vision",
    "idmap_id_collision": "true_junk",
}


@dataclass
class MetricDecision:
    metric_id: int
    decision: str
    reason: str
    confidence: float | None = None


@dataclass
class DocResult:
    sha: str
    decisions: list[MetricDecision] = field(default_factory=list)
    error: str = ""


# ============================================================
# LLM signals (the only network; bounded to small-int suspects)
# ============================================================

def _measured_signal(value, source_text: str, measure_fn: mc.ChatFn | None) -> bool | None:
    """Small-int measurable signal. None (unchecked) for non-small-ints OR when no
    measure_fn is wired OR the check is unavailable/malformed -> the policy layer
    quarantines a small int with measured=None as ``measure_unchecked``."""
    if not gate.is_small_int(value):
        return None
    if measure_fn is None:
        return None
    return mc.check_measurable(source_text, value, measure_fn)


def _nonmetric_reject(value, label: str, source_text: str, measure_fn: mc.ChatFn | None) -> bool:
    """is-a-metric reject: True iff a stage-1 candidate is confirmed NOT-a-metric by the
    stage-2 judge. Conservative — an unchecked/unavailable judge does NOT reject."""
    if measure_fn is None:
        return False
    if not mc.nonmetric_stage1(value, label, source_text):
        return False
    return mc.check_is_metric(source_text, value, measure_fn) is False


def _confidence(metric: dict, value_ref, subject_ref, idmap: dict) -> float:
    """Coarse ADVISORY score (grounding margin + co-location strength). Does NOT affect
    the decision (spec §5.5) — only prioritizes the spot-review sample."""
    score = 0.5
    if gate.co_located(value_ref, subject_ref, idmap):
        score += 0.25
    sr = idmap.get(subject_ref) or {}
    stext = (sr.get("text", "") or "") + " " + (sr.get("row_text", "") or "")
    if gate._words(metric.get("label")) & gate._words(stext):
        score += 0.25
    return round(score, 3)


# ============================================================
# Per-document gating (pure given the precomputed LLM signals)
# ============================================================

def gate_document(rows: list[dict], idmap: dict, stale: bool,
                  measure_fn: mc.ChatFn | None, *, mispair_detect: bool) -> list[MetricDecision]:
    """Decide every metric in one doc, then apply prose-internal de-dup over the
    publish-eligible set. Returns one :class:`MetricDecision` per row."""
    decisions: list[MetricDecision] = []
    for r in rows:
        value = r.get("metric_value")
        label = r.get("metric_type")
        source_text = r.get("source_snippet") or r.get("metric_text") or ""
        value_ref = r.get("value_ref")
        subject_ref = r.get("subject_ref")
        metric = {"metric_value": value, "label": label, "source_text": source_text}
        measured = _measured_signal(value, source_text, measure_fn)
        nonmetric = _nonmetric_reject(value, label, source_text, measure_fn)
        dec, reason = gate_policy.decide(
            metric, value_ref, subject_ref, idmap,
            marker_resolved=bool(r.get("marker_resolved")), stale=stale,
            measured=measured, nonmetric_reject=nonmetric, mispair_detect=mispair_detect)
        conf = _confidence(metric, value_ref, subject_ref, idmap) if dec == gate_policy.PUBLISH else None
        decisions.append(MetricDecision(r["id"], dec, reason, conf))

    # prose-internal de-dup over the publish-eligible subset (§5.4 / AC7)
    pub_idx = [i for i, d in enumerate(decisions) if d.decision == gate_policy.PUBLISH]
    pub_metrics = [{"metric_value": rows[i].get("metric_value"),
                    "label": rows[i].get("metric_type"),
                    "value_ref": rows[i].get("value_ref")} for i in pub_idx]
    dropped = gate_policy.dedup_indices(pub_metrics, idmap)
    for local_i in dropped:
        d = decisions[pub_idx[local_i]]
        d.decision = gate_policy.QUARANTINE
        d.reason = "duplicate"
        d.confidence = None
    return decisions


# ============================================================
# DB helpers (parameterized)
# ============================================================

_DOC_METRICS_SQL = text("""
    SELECT id, content_sha256, source_org_ein, metric_text, metric_type, metric_value,
           source_snippet, value_ref, subject_ref, marker_resolved, parse_version, render_version
    FROM lava_vocab.llm_metrics
    WHERE run_id = :rid AND content_sha256 = :sha
    ORDER BY id
""")

_UPDATE_SQL = text("""
    UPDATE lava_vocab.llm_metrics
    SET gate_decision = :d, gate_reason = :r, gate_confidence = :c, gate_run_id = :gid
    WHERE id = :mid AND (gate_run_id IS NULL OR gate_run_id <= :gid)
""")


def _source_run_id(conn, run_tag: str) -> int | None:
    return conn.execute(text(
        "SELECT id FROM lava_vocab.extraction_runs WHERE run_tag = :t"), {"t": run_tag}).scalar()


def _doc_shas(conn, source_run_id: int) -> list[str]:
    rows = conn.execute(text(
        "SELECT DISTINCT content_sha256 FROM lava_vocab.llm_metrics WHERE run_id = :rid "
        "ORDER BY content_sha256"), {"rid": source_run_id}).fetchall()
    return [r[0] for r in rows]


def _live_provenance(conn, sha: str) -> str | None:
    return conn.execute(text(
        "SELECT parse_version FROM lava_parse.documents WHERE content_sha256 = :s"),
        {"s": sha}).scalar()


def _is_stale(rows: list[dict], live_parse_version: str | None) -> bool:
    """A doc is stale iff the live parse_version differs from what 0068 stored, or the
    render logic version moved (§5.8) — 0069 gates on what 0068 stored, never re-resolves."""
    stored_pv = rows[0].get("parse_version") if rows else None
    if live_parse_version is None or stored_pv is None:
        return True
    if live_parse_version != stored_pv:
        return True
    stored_rv = rows[0].get("render_version")
    return bool(stored_rv) and stored_rv != RENDER_VERSION


# ============================================================
# Orchestration
# ============================================================

def run_gate(
    engine: Engine,
    source_run_tag: str,
    *,
    measure_fn: mc.ChatFn | None = None,
    mispair_detect: bool = True,
    write: bool = True,
) -> dict:
    """Gate a 0068 marker run. Returns the per-run report. ``write=False`` is a fully
    read-only dry run (no gate_runs row, no UPDATEs).

    Concurrency-safe: serialized per ``source_run_id`` via an advisory lock; a concurrent
    runner on the same source run raises :class:`GateRunBusy`.
    """
    with engine.connect() as conn:
        source_run_id = _source_run_id(conn, source_run_tag)
    if source_run_id is None:
        raise ValueError(f"no extraction_runs row for run_tag {source_run_tag!r}")

    if not write:
        return _process(engine, source_run_id, None, measure_fn, mispair_detect, write=False)

    # Serialize this source run + allocate a server-assigned gate_run_id, in one txn so the
    # lock is held while we work (xact lock releases at COMMIT). A concurrent runner that
    # cannot get the lock fails fast rather than interleaving.
    with engine.begin() as conn:
        got = conn.execute(text("SELECT pg_try_advisory_xact_lock(:cls, :obj)"),
                           {"cls": GATE_RUNNER_BASE, "obj": int(source_run_id)}).scalar()
        if not got:
            raise GateRunBusy(f"another gate runner holds source_run_id={source_run_id}")
        gate_run_id = conn.execute(text(
            "INSERT INTO lava_vocab.gate_runs (source_run_id, gate_version) "
            "VALUES (:src, :v) RETURNING id"),
            {"src": int(source_run_id), "v": GATE_VERSION}).scalar()
        report = _process(engine, source_run_id, gate_run_id, measure_fn, mispair_detect,
                          write=True, conn=conn)
        # immutable audit: append the report under this gate_run_id on the SOURCE run's
        # stats_json (never clobbering prior gate runs' reports — append-only by key).
        conn.execute(text(
            "UPDATE lava_vocab.extraction_runs "
            "SET stats_json = jsonb_set("
            "  COALESCE(stats_json, '{}'::jsonb) || jsonb_build_object("
            "    'gate_runs', COALESCE(stats_json->'gate_runs', '{}'::jsonb)), "
            "  ARRAY['gate_runs', :gid_txt], CAST(:rep AS JSONB), true) "
            "WHERE id = :rid"),
            {"gid_txt": str(gate_run_id), "rep": json.dumps(report), "rid": int(source_run_id)})
    return report


def _process(engine, source_run_id, gate_run_id, measure_fn, mispair_detect, *,
             write: bool, conn=None) -> dict:
    """Iterate docs, decide, and (if write) UPDATE in place within the held transaction."""
    results: list[DocResult] = []
    with engine.connect() as read_conn:
        shas = _doc_shas(read_conn, source_run_id)
        for sha in shas:
            rows = [dict(m._mapping) for m in read_conn.execute(
                _DOC_METRICS_SQL, {"rid": source_run_id, "sha": sha}).fetchall()]
            if not rows:
                continue
            live_pv = _live_provenance(read_conn, sha)
            dr = DocResult(sha)
            try:
                render = render_tagged(read_conn, sha)
                idmap = render.idmap
                stale = _is_stale(rows, live_pv)
            except SkipDocument as sd:
                # whole-doc re-render failure -> quarantine all metrics (no partial publish)
                reason = sd.reason if sd.reason in TRIAGE else "render_error"
                dr.decisions = [MetricDecision(r["id"], "quarantine", reason) for r in rows]
                dr.error = sd.reason
                results.append(dr)
                continue
            dr.decisions = gate_document(rows, idmap, stale, measure_fn, mispair_detect=mispair_detect)
            results.append(dr)

    if write:
        for dr in results:
            for d in dr.decisions:
                conn.execute(_UPDATE_SQL, {"d": d.decision, "r": d.reason, "c": d.confidence,
                                           "gid": int(gate_run_id), "mid": int(d.metric_id)})

    return aggregate_report(results, gate_run_id, source_run_id)


def aggregate_report(results: list[DocResult], gate_run_id, source_run_id) -> dict:
    """Per-run report: publish/quarantine split + reason histogram + quarantine triage."""
    all_d = [d for dr in results for d in dr.decisions]
    publish = [d for d in all_d if d.decision == gate_policy.PUBLISH]
    quarantine = [d for d in all_d if d.decision == gate_policy.QUARANTINE]
    reason_hist: dict[str, int] = {}
    triage = {"recoverable_relabel": 0, "recoverable_vision": 0, "true_junk": 0}
    for d in quarantine:
        reason_hist[d.reason] = reason_hist.get(d.reason, 0) + 1
        triage[TRIAGE.get(d.reason, "true_junk")] += 1
    return {
        "gate_run_id": gate_run_id,
        "source_run_id": source_run_id,
        "gate_version": GATE_VERSION,
        "docs": len(results),
        "metrics_total": len(all_d),
        "published": len(publish),
        "quarantined": len(quarantine),
        "reason_histogram": dict(sorted(reason_hist.items())),
        "triage": triage,
        "docs_render_error": sum(1 for dr in results if dr.error),
    }


class GateRunBusy(RuntimeError):
    """Another gate runner already holds this source run's advisory lock (S7)."""

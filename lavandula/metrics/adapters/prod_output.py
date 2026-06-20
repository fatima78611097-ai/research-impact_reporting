"""Prod OUTPUT adapter — gated Metrics -> lava_impact RDS commit.

I/O only: serializes Metric records into lava_impact.metrics. Re-implements the Spec 0069
durability PATTERNS (read as reference, NOT imported) against this contract:
  - advisory lock per run  -> no two writers interleave
  - idempotent UPSERT on (run_id, content_sha256, idx)  -> re-running UPDATEs in place
  - a runs row bookkeeps each extract/gate pass

Makes NO decisions — every field comes straight off the Metric. The dev output adapter
differs ONLY here (JSON+images instead of RDS).
"""
from __future__ import annotations

import json

from sqlalchemy import text

EXTRACTION_MODEL = "deepseek-chat"
_LOCK_KEY = 0x1A9AC7  # advisory-lock namespace for metric-engine writes


def start_run(conn, kind: str, note: str | None = None) -> int:
    """Open a runs row; returns its id. kind = 'extract' | 'gate'."""
    return conn.execute(text(
        "INSERT INTO lava_impact.runs (kind, note) VALUES (:k, :n) RETURNING id"),
        {"k": kind, "n": note}).scalar()


def finish_run(conn, run_id: int, doc_count: int, metric_count: int) -> None:
    conn.execute(text(
        "UPDATE lava_impact.runs SET doc_count=:d, metric_count=:m, finished_at=now() WHERE id=:r"),
        {"d": doc_count, "m": metric_count, "r": run_id})


def _row(m, run_id: int) -> dict:
    p = m.prov
    return {
        "run_id": run_id, "content_sha256": m.content_sha256, "idx": m.idx,
        "ein": None, "report_year": m.report_year, "url": m.url,
        "statement": m.statement, "value": m.value, "value_text": m.value_text,
        "unit": m.unit, "logic_tier": m.tier, "subject": m.subject,
        "value_ref": p.value_ref, "subject_ref": p.subject_ref,
        "value_page": p.value_page, "subject_page": p.subject_page,
        "value_bbox": json.dumps(p.value_bbox) if p.value_bbox else None,
        "subject_bbox": json.dumps(p.subject_bbox) if p.subject_bbox else None,
        "same_marker": p.same_marker, "source_snippet": p.source_snippet,
        "gate_decision": m.decision, "gate_reason": m.reason,
        "gate_flags": json.dumps(m.flags) if m.flags else None, "gate_run_id": run_id,
        "extraction_model": EXTRACTION_MODEL,
    }


_UPSERT = text("""
    INSERT INTO lava_impact.metrics (
        run_id, content_sha256, idx, ein, report_year, url,
        statement, value, value_text, unit, logic_tier, subject,
        value_ref, subject_ref, value_page, subject_page, value_bbox, subject_bbox,
        same_marker, source_snippet, gate_decision, gate_reason, gate_flags, gate_run_id,
        extraction_model, gated_at)
    VALUES (
        :run_id, :content_sha256, :idx, :ein, :report_year, :url,
        :statement, :value, :value_text, :unit, :logic_tier, :subject,
        :value_ref, :subject_ref, :value_page, :subject_page, :value_bbox, :subject_bbox,
        :same_marker, :source_snippet, :gate_decision, :gate_reason, :gate_flags, :gate_run_id,
        :extraction_model, now())
    ON CONFLICT (run_id, content_sha256, idx) DO UPDATE SET
        statement=EXCLUDED.statement, value=EXCLUDED.value, value_text=EXCLUDED.value_text,
        unit=EXCLUDED.unit, logic_tier=EXCLUDED.logic_tier, subject=EXCLUDED.subject,
        value_ref=EXCLUDED.value_ref, subject_ref=EXCLUDED.subject_ref,
        value_page=EXCLUDED.value_page, subject_page=EXCLUDED.subject_page,
        value_bbox=EXCLUDED.value_bbox, subject_bbox=EXCLUDED.subject_bbox,
        same_marker=EXCLUDED.same_marker, source_snippet=EXCLUDED.source_snippet,
        gate_decision=EXCLUDED.gate_decision, gate_reason=EXCLUDED.gate_reason,
        gate_flags=EXCLUDED.gate_flags, gate_run_id=EXCLUDED.gate_run_id, gated_at=now()
""")


def commit(conn, metrics, run_id: int, *, write: bool = True) -> int:
    """UPSERT `metrics` into lava_impact.metrics under `run_id`. write=False = dry run (no writes).
    Returns the count that would be / was written. Idempotent: re-running updates in place."""
    rows = [_row(m, run_id) for m in metrics]
    if not write:
        return len(rows)
    # advisory lock: serialize writers on this run
    conn.execute(text("SELECT pg_advisory_xact_lock(:k, :r)"), {"k": _LOCK_KEY, "r": run_id})
    for r in rows:
        conn.execute(_UPSERT, r)
    return len(rows)


# ── re-gate path (the gate-metrics stage): re-decide stored metrics without re-extracting ──

def load_for_regate(conn, run_id: int):
    """Read stored metrics for a run back into Metric objects (enough for the gate to re-decide:
    statement, value, tier, and the provenance the mispairing check needs)."""
    from lavandula.metrics.core.types import Metric, Provenance
    import json as _json

    def _bb(v):
        return _json.loads(v) if isinstance(v, str) else v

    rows = conn.execute(text("""
        SELECT content_sha256, idx, statement, value, value_text, unit, logic_tier, subject,
               value_ref, subject_ref, value_page, subject_page, value_bbox, subject_bbox,
               same_marker, source_snippet, report_year, url
        FROM lava_impact.metrics WHERE run_id = :r ORDER BY content_sha256, idx"""),
        {"r": run_id}).mappings().all()
    out = []
    for x in rows:
        m = Metric(content_sha256=x["content_sha256"], idx=x["idx"], report_year=x["report_year"],
                   url=x["url"], statement=x["statement"] or "", value=x["value"],
                   value_text=x["value_text"], unit=x["unit"], tier=x["logic_tier"], subject=x["subject"])
        m.prov = Provenance(value_page=x["value_page"], subject_page=x["subject_page"],
                            value_ref=x["value_ref"], subject_ref=x["subject_ref"],
                            value_bbox=_bb(x["value_bbox"]), subject_bbox=_bb(x["subject_bbox"]),
                            same_marker=bool(x["same_marker"]), source_snippet=x["source_snippet"])
        out.append(m)
    return out


_REGATE = text("""
    UPDATE lava_impact.metrics
    SET gate_decision=:gate_decision, gate_reason=:gate_reason, gate_flags=:gate_flags,
        gate_run_id=:gate_run_id, gated_at=now()
    WHERE run_id=:run_id AND content_sha256=:content_sha256 AND idx=:idx
""")


def update_decisions(conn, metrics, run_id: int, gate_run_id: int) -> int:
    """UPDATE only the gate columns for already-stored metrics (re-gate in place). Idempotent."""
    conn.execute(text("SELECT pg_advisory_xact_lock(:k, :r)"), {"k": _LOCK_KEY, "r": run_id})
    n = 0
    for m in metrics:
        conn.execute(_REGATE, {
            "run_id": run_id, "content_sha256": m.content_sha256, "idx": m.idx,
            "gate_decision": m.decision, "gate_reason": m.reason,
            "gate_flags": json.dumps(m.flags) if m.flags else None, "gate_run_id": gate_run_id})
        n += 1
    return n

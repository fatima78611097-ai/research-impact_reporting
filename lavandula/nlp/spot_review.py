"""Human spot-review + precision SLA gate (Spec 0069, Phase 6).

An OFFLINE evaluation (spec §5.7): a stratified sample of the published set is graded for
right-number AND right-label; the run is "shippable" iff the MEASURED precision >= the
named ``PUBLISH_PRECISION_SLA``. 0069 ships the protocol + the measured number; it does
NOT write per-metric corrections back to ``gate_decision`` (a correction UI is separate).

Integrity (spec §5.7 / Codex HIGH):
  - the sample is **frozen at selection** — sampled metric ids + a content hash are
    recorded in a committed manifest, so a sample can't be swapped or replayed;
  - grading is **append-only** with **reviewer identity + timestamp** (the identity is the
    authenticated session/shell identity, NOT a spoofable CLI argument — Gemini MEDIUM);
  - the shippable judgment is a documented, repeatable computation, not a code branch that
    silently passes.

Pure selection/measurement here; the DB writes go through :func:`record_review`.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine


@dataclass(frozen=True)
class PrecisionSLA:
    """The named publish precision bar (plan §dec.1; operator confirms in Phase 0).

    ``per_metric`` — minimum right-number-AND-right-label fraction across the sample.
    ``max_wrong_per_org`` — at most this many wrong published metrics for any one org.
    """
    per_metric: float = 0.98
    max_wrong_per_org: int = 1


# The operator-set default (committed here, version-controlled per spec §8). Phase 0
# confirms the exact numbers; lowering it is an explicit, recorded operator action.
PUBLISH_PRECISION_SLA = PrecisionSLA()


def located_kind(row: dict) -> str:
    """A published row is 'table' if it resolved to a cell (row+col), else 'prose'."""
    return "table" if (row.get("value_row") is not None and row.get("value_col") is not None) else "prose"


def reviewer_identity(env: dict | None = None) -> str:
    """The authenticated session/shell identity (Gemini MEDIUM) — NOT a CLI argument.
    Raises if no identity is present (a failing run can't be rubber-stamped anonymously)."""
    env = env if env is not None else os.environ
    ident = env.get("LOCARD_REVIEWER") or env.get("USER") or env.get("LOGNAME")
    if not ident:
        raise RuntimeError("no reviewer identity in session ($USER/$LOGNAME/$LOCARD_REVIEWER)")
    return ident


def select_sample(rows: list[dict], *, size: int) -> dict:
    """Deterministically pick a stratified sample of the published set and FREEZE it.

    ``rows`` are published metrics with at least ``id``, ``source_org_ein``,
    ``value_row``/``value_col`` (for prose-vs-table strata) and an advisory
    ``gate_confidence`` (lowest first within a stratum — review the least-confident).
    Strata = (located_kind, org). Allocation is proportional, round-robin across strata
    so both prose and table and a spread of orgs appear. Returns a frozen manifest
    ``{size, sampled_ids, strata, content_hash}``; the hash binds the exact id set.
    """
    if size <= 0 or not rows:
        return {"size": 0, "sampled_ids": [], "strata": {}, "content_hash": _hash([])}

    # bucket by stratum, ordered within a stratum by (confidence asc, id) — deterministic.
    strata: dict[tuple, list[dict]] = {}
    for r in rows:
        key = (located_kind(r), r.get("source_org_ein"))
        strata.setdefault(key, []).append(r)
    for key in strata:
        strata[key].sort(key=lambda r: (r.get("gate_confidence") if r.get("gate_confidence") is not None else 1.0,
                                        r["id"]))

    # round-robin draw across strata (sorted for determinism) until we hit `size`.
    order = sorted(strata.keys(), key=lambda k: (str(k[0]), str(k[1])))
    picked: list[int] = []
    cursors = {k: 0 for k in order}
    target = min(size, len(rows))
    while len(picked) < target:
        progressed = False
        for key in order:
            if len(picked) >= target:
                break
            c = cursors[key]
            if c < len(strata[key]):
                picked.append(strata[key][c]["id"])
                cursors[key] = c + 1
                progressed = True
        if not progressed:
            break

    picked_sorted = sorted(picked)
    strata_counts = {f"{k[0]}|{k[1]}": min(cursors[k], len(strata[k])) for k in order}
    return {
        "size": len(picked_sorted),
        "sampled_ids": picked_sorted,
        "strata": strata_counts,
        "content_hash": _hash(picked_sorted),
    }


def _hash(ids: list[int]) -> str:
    return hashlib.sha256(json.dumps(sorted(ids)).encode()).hexdigest()


def write_manifest(manifest: dict, gate_run_id: int, created_at: str, base_dir: str) -> str:
    """Persist the frozen manifest as a committed artifact
    ``<base_dir>/<gate_run_id>-manifest.json``. ``created_at`` is passed in (no wall-clock
    here, for reproducibility). Returns the path."""
    os.makedirs(base_dir, exist_ok=True)
    path = os.path.join(base_dir, f"{gate_run_id}-manifest.json")
    payload = {"gate_run_id": gate_run_id, "created_at": created_at, **manifest}
    with open(path, "w") as f:
        json.dump(payload, f, indent=1)
    return path


def verify_manifest(manifest: dict) -> bool:
    """A manifest is intact iff its recorded ``content_hash`` matches its ``sampled_ids``
    (detects a swapped/relabeled sample)."""
    return manifest.get("content_hash") == _hash(list(manifest.get("sampled_ids", [])))


def record_review(engine: Engine, gate_run_id: int, metric_id: int, *,
                  right_number: bool, right_label: bool, sample_hash: str,
                  reviewer: str | None = None, notes: str = "") -> None:
    """Append one immutable grade. Reviewer defaults to the session identity (never a
    caller-spoofable value unless explicitly overridden in a trusted context)."""
    rev = reviewer or reviewer_identity()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO lava_vocab.gate_review
                (gate_run_id, metric_id, reviewer, right_number, right_label, sample_hash, notes)
            VALUES (:g, :m, :rev, :rn, :rl, :h, :n)
        """), {"g": int(gate_run_id), "m": int(metric_id), "rev": rev,
               "rn": bool(right_number), "rl": bool(right_label), "h": sample_hash, "n": notes})


@dataclass
class PrecisionResult:
    n: int
    correct: int                       # right_number AND right_label
    per_metric: float
    per_org_wrong: dict[str, int] = field(default_factory=dict)
    shippable: bool = False


def compute_precision(reviews: list[dict], sla: PrecisionSLA = PUBLISH_PRECISION_SLA) -> PrecisionResult:
    """Measure right-number-AND-right-label precision over graded reviews and decide
    shippability vs the SLA (per-metric AND per-org). Latest grade per metric wins (the
    table is append-only; a re-grade is a newer row)."""
    latest: dict[int, dict] = {}
    for r in sorted(reviews, key=lambda x: x.get("reviewed_at_ord", 0)):
        latest[r["metric_id"]] = r
    graded = list(latest.values())
    n = len(graded)
    correct = sum(1 for r in graded if r["right_number"] and r["right_label"])
    per_metric = (correct / n) if n else 0.0
    per_org_wrong: dict[str, int] = {}
    for r in graded:
        if not (r["right_number"] and r["right_label"]):
            org = r.get("source_org_ein", "?")
            per_org_wrong[org] = per_org_wrong.get(org, 0) + 1
    org_ok = all(w <= sla.max_wrong_per_org for w in per_org_wrong.values())
    shippable = bool(n) and per_metric >= sla.per_metric and org_ok
    return PrecisionResult(n=n, correct=correct, per_metric=round(per_metric, 4),
                           per_org_wrong=per_org_wrong, shippable=shippable)

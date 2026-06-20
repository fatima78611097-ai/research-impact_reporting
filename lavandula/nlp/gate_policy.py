"""Production-only gate policies + the per-metric decision chain (Spec 0069, Phase 2).

Layered ON TOP of the pure oracle (:mod:`lavandula.nlp.gate`), kept separate so its
regressions isolate from the oracle's (spec §4). Adds the production policies that may
be tuned:

  - **marker_resolved gate** (§4.1) — an unresolved marker -> ``unmarked``.
  - **stale-coords** (§5.8) — a re-render that no longer matches the stored
    ``parse_version`` -> ``stale_coords``; 0069 NEVER re-resolves.
  - **is-a-metric reject rules** (§5.3) — re-homed onto value + label + marker source
    text (stage-1 regex in :mod:`lavandula.nlp.measure_check`; the LLM confirm is wired
    by the runner) -> ``not_a_metric_rule``.
  - **mispair / column-coherence DETECT** (§4.6) — ``column_guard`` ported in
    *detect -> quarantine only* mode (NEVER auto-fix) -> ``mispair``.
  - **prose-internal de-dup** (§5.4) — collapse identical value+label co-located
    occurrences, keep lowest reading order; uncertain -> keep both.

The decision precedence is fixed (§4 order, 1->7); the FIRST failing check wins and is
the single stored ``gate_reason``. Conservative throughout: quarantine-when-in-doubt
(false-publish is costly, false-quarantine is recoverable).

Pure and deterministic (the LLM signals ``measured`` / ``nonmetric_reject`` are
precomputed by the runner and passed in).
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable

from lavandula.nlp import gate

# ============================================================
# de-dup helpers (§5.4) — labels_agree ported from research regroup.py
# ============================================================

# Generic participation/measure words: never sufficient for label agreement on their own
# ("180 seminar ATTENDEES" must not agree with "individuals ATTENDED support groups").
_GENERIC_STEMS = {"atten", "serve", "servi", "recei", "benef", "indiv", "peopl", "famil",
                  "clien", "membe", "perso", "parti", "stude", "youth", "adult", "child",
                  "throu", "progr", "total"}


def labels_agree(label_a: Any, label_b: Any) -> bool:
    """Distinctive-stem overlap (the same helper 0068's regroup used)."""
    sa = {w[:5] for w in re.findall(r"[a-z]{4,}", (label_a or "").lower())} - _GENERIC_STEMS
    sb = {w[:5] for w in re.findall(r"[a-z]{4,}", (label_b or "").lower())} - _GENERIC_STEMS
    return bool(sa & sb)


def reading_order_key(value_ref: str | None, idmap: dict) -> tuple:
    """A total order over markers: page, then text-before-table, then table/row/col.
    Used to keep the lowest-reading-order occurrence on de-dup (§5.4)."""
    e = idmap.get(value_ref) or {}
    page = e.get("page")
    page = page if page is not None else 1_000_000          # unknown page sorts last
    is_cell = 1 if e.get("kind") == "cell" else 0           # text before table
    table = e.get("table") if e.get("table") is not None else -1
    row = e.get("row") if e.get("row") is not None else -1
    col = e.get("col") if e.get("col") is not None else -1
    idx = e.get("idx") if e.get("idx") is not None else 0
    return (page, is_cell, table, row, col, idx)


def dedup_indices(metrics: list[dict], idmap: dict) -> dict[int, int]:
    """Prose-internal de-dup WITHIN one document (§5.4).

    ``metrics`` is the doc's publish-eligible list; each item has ``metric_value``,
    ``label``, ``value_ref``. Two are merged iff ALL hold: equal numeric value (after
    ``gate.to_float``), ``labels_agree``, AND co-located ``value_ref`` markers. On merge
    keep the lowest reading order; the dropped index maps to the kept index. Any
    uncertainty (no value, missing marker, not co-located) -> keep both.

    Returns ``{dropped_index: kept_index}`` (reason ``duplicate`` for dropped rows).
    """
    n = len(metrics)
    order = sorted(range(n), key=lambda i: reading_order_key(metrics[i].get("value_ref"), idmap))
    dropped: dict[int, int] = {}
    for pos_a in range(n):
        i = order[pos_a]
        if i in dropped:
            continue
        vi = gate.to_float(metrics[i].get("metric_value"))
        ri = metrics[i].get("value_ref")
        if vi is None or ri is None:
            continue
        for pos_b in range(pos_a + 1, n):
            j = order[pos_b]
            if j in dropped:
                continue
            vj = gate.to_float(metrics[j].get("metric_value"))
            rj = metrics[j].get("value_ref")
            if vj is None or rj is None:
                continue
            if abs(vi - vj) > 1e-9:
                continue
            if not labels_agree(metrics[i].get("label"), metrics[j].get("label")):
                continue
            if not gate.co_located(ri, rj, idmap):
                continue  # spatially distinct -> NOT a duplicate (keep both)
            dropped[j] = i   # j is later in reading order -> drop it, keep i
    return dropped


# ============================================================
# mispair / column-coherence DETECT (§4.6) — ported gate.column_guard, detect-only
# ============================================================

_TARGET = {"target", "goal", "projected", "budget", "budgeted", "planned", "expected", "forecast"}
_ACTUAL = {"actual", "actuals", "achieved", "attained", "realized"}
_BENCH = {"average", "averages", "national", "benchmark", "median", "peer"}
_TOTAL_HDR = ("total", "all funds", "combined", "consolidated", "grand", "subtotal")


def _qual_cat(wset: set[str]) -> str | None:
    if wset & _TARGET:
        return "target"
    if wset & _ACTUAL:
        return "actual"
    if wset & _BENCH:
        return "bench"
    return None


def _header_for(cells: list[dict], col) -> str:
    if col is None:
        return ""
    hr = min((c.get("row") for c in cells if c.get("row") is not None), default=None)
    return next((c.get("text", "") for c in cells if c.get("row") == hr and c.get("col") == col), "")


def _is_total_hdr(h: Any) -> bool:
    return any(t in (h or "").lower() for t in _TOTAL_HDR)


def column_mispair(metric_value, value_ref, idmap, source_text="", label="") -> str | None:
    """Detect wrong-COLUMN attribution for a value in a table cell (port of the research
    ``column_guard``, used here in DETECT mode only — the result quarantines, never
    auto-fixes). Returns a sub-reason or ``None``.

      A) value EXACTLY duplicated across non-total columns of its row -> ambiguous.
      B) the value cell's column header conflicts with the metric wording (target vs
         achieved / a different year).
    """
    a = idmap.get(value_ref)
    if not a or a.get("kind") != "cell" or a.get("col") is None:
        return None
    cells = [c for c in idmap.values()
             if c.get("kind") == "cell" and c.get("table") == a.get("table") and c.get("page") == a.get("page")]
    if not cells:
        return None
    aval = gate.to_float(a.get("text"))
    if aval is not None:
        samerow = [c for c in cells if c.get("row") == a.get("row") and c.get("col") != a.get("col")]
        matches = [c for c in samerow
                   if gate.to_float(c.get("text")) is not None and abs(gate.to_float(c.get("text")) - aval) < 1e-6]
        if matches and not _is_total_hdr(_header_for(cells, a.get("col"))) \
                and not any(_is_total_hdr(_header_for(cells, c.get("col"))) for c in matches):
            return "column_ambiguous"
    hdr = _header_for(cells, a.get("col"))
    mtext = (source_text or "") + " " + (label or "")
    hc, mc = _qual_cat(gate._words(hdr)), _qual_cat(gate._words(mtext))
    if hc and mc and hc != mc:
        return "column_header_conflict"
    syear = set(re.findall(r"(?:19|20)\d{2}", source_text or ""))
    hyear = set(re.findall(r"(?:19|20)\d{2}", hdr or ""))
    if syear and hyear and not (syear & hyear):
        return "column_year_conflict"
    return None


# ============================================================
# the per-metric decision chain (§4, fixed precedence)
# ============================================================

PUBLISH = "publish"
QUARANTINE = "quarantine"

# ============================================================
# financial-statement line item (production policy) — operational financial detail
# (totals, balance-sheet/P&L lines) that the impact-metric product excludes (per
# nonprofit_metric_extraction_guidance.md: financial line items are operational, not impact).
# ============================================================
_FIN_LINEITEM = re.compile(
    r"\btotal (revenue|revenues|income|expenses|support|assets|liabilities|net assets"
    r"|current assets|current liabilities|operating expenses)\b"
    r"|\bgross receipts\b|\bnet assets\b|\b(endowment (fund )?|fund )balance\b"
    r"|\boperating expenses\b|\bcost of (goods|services|revenue)\b"
    r"|\b(investment|interest) income\b|\b(federal|state|government) (support|funding|appropriation)\b"
    r"|\bmanagement and general\b|\bfundrais\w+ expenses?\b"
    r"|\bprogram (services )?expenses?\b|\bchange in net assets\b"
    r"|\baccounts (payable|receivable)\b|\bcash (and|&) (cash )?equivalents\b"
    r"|\bdeferred revenue\b|\bdepreciation\b|\bprepaid expenses\b", re.I)


def financial_lineitem(label, source_text="") -> bool:
    """True iff the metric is an audited-financials / balance-sheet / P&L line item (total
    revenue/income/expenses/assets/net assets, fund balance, federal support, etc.) —
    operational financial detail the impact-metric product excludes. Keyed on the metric
    LABEL only: the source sentence can mention financial context incidentally for a real
    impact metric ('government funding helped serve 500'), so matching source would over-reject.
    Does NOT match beneficiary distributions ('grants to families', 'relief provided')."""
    return bool(_FIN_LINEITEM.search(label or ""))


def decide(
    metric: dict,
    value_ref: str | None,
    subject_ref: str | None,
    idmap: dict,
    *,
    marker_resolved: bool = True,
    stale: bool = False,
    measured: bool | None = None,
    nonmetric_reject: bool = False,
    financial_reject: bool = False,
    mispair_detect: bool = True,
    budget_s: float | None = gate.DEFAULT_TIME_BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[str, str]:
    """The integrated per-metric chain. Returns ``(decision, reason)`` with a single,
    deterministic reason (first failing check wins, §4 order 1->7).

    Precomputed LLM signals: ``measured`` (small-int measurable gate, §4.4) and
    ``nonmetric_reject`` (is-a-metric stage-2 confirmed a reject, §4.5). The runner owns
    those external calls; this function stays pure.
    """
    # 1. stale coords (§5.8) — gate only on what 0068 stored; never re-resolve.
    if stale:
        return QUARANTINE, "stale_coords"

    # 1b. unmarked (§4.1) — an unresolved/null marker is a vision candidate, never published.
    if not marker_resolved:
        return QUARANTINE, "unmarked"

    # 2–4. pure oracle: value-out-of-bounds, value grounding, subject grounding, small-int measurable.
    try:
        dec, reason = gate.verdict(metric, value_ref, subject_ref, idmap,
                                   measured=measured, budget_s=budget_s, clock=clock)
    except gate.GateTimeout:
        return QUARANTINE, "gate_timeout"
    if dec == QUARANTINE:
        return dec, reason

    # 4b. measure_unchecked (§5.3) — a small int that PASSED grounding but whose measurable
    # check was unavailable/malformed (measured is None) must NOT publish unverified. The
    # oracle stays regression-locked (it treats measured=None as "skip"); this fail-safe is
    # production-only policy. Large values (is_small_int False) are unaffected.
    if measured is None and gate.is_small_int(metric.get("metric_value")):
        return QUARANTINE, "measure_unchecked"

    # 4c. financial-statement line item — fast deterministic reject (free) for the obvious
    # audited-statement vocabulary (total revenue/expenses/assets/net assets, ...).
    if financial_lineitem(metric.get("label"), metric.get("source_text", "")):
        return QUARANTINE, "financial_lineitem"

    # 4d. financial tier (LLM-classified) — the org's own money figure (budget, revenue,
    # fundraising proceeds, ratios) that regex misses; out of scope for the impact product.
    if financial_reject:
        return QUARANTINE, "tier_financial"

    # 5. is-a-metric reject rules (production policy, value+label+source).
    if nonmetric_reject:
        return QUARANTINE, "not_a_metric_rule"

    # 6. mispair / column-coherence DETECT (never auto-fix).
    if mispair_detect:
        mp = column_mispair(metric.get("metric_value"), value_ref, idmap,
                            source_text=metric.get("source_text", ""), label=metric.get("label"))
        if mp is not None:
            return QUARANTINE, "mispair"

    # 7. publish.
    return PUBLISH, "ok"

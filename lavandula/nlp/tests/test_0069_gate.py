"""Phase-1 tests: the pure marker-grounded gate oracle (Spec 0069).

AC1  — reproduces research gate.py verdicts on the frozen counterexample set
        (spelled-out / abbreviated / rounded ground; small-int stray-digit does NOT;
        the efd61fea parse-shatter trap; rankings -> not_a_metric).
S1   — to_float rejects NaN/Inf/overflow -> value_out_of_bounds, no malformed value.
S4   — per-metric time budget -> GateTimeout (runner maps to quarantine/gate_timeout).
AC11 — determinism: identical inputs -> identical decision + reason.

Pure units, no DB / network.
"""
from __future__ import annotations

import math

import pytest

from lavandula.nlp import gate


def _idmap(**entries):
    return entries


def _text(s, page=1, row=None, col=None, bbox=None, row_text=None):
    return {"kind": "cell" if row is not None else "text", "text": s, "page": page,
            "row": row, "col": col, "bbox": bbox, "row_text": row_text}


# ============================================================
# value grounding (AC1)
# ============================================================

class TestValueGrounded:
    def test_spelled_out(self):
        assert gate.value_grounded(5, "Five clients served") is True

    def test_abbreviated_millions(self):
        assert gate.value_grounded(4_400_000, "raised $4.4 million this year") is True

    def test_rounded_precise_figure(self):
        # 6,841,066 within 0.1% of $6,841,065.77
        assert gate.value_grounded(6_841_066, "net assets $6,841,065.77") is True

    def test_commas_and_dollar(self):
        assert gate.value_grounded(14884, "served 14,884 individuals") is True

    def test_parse_shattered_whitespace(self):
        # digits split by whitespace only re-join ("1 4, 884" -> 14884)
        assert gate.value_grounded(14884, "1 4, 884 individuals") is True

    def test_small_int_exact_token_grounds(self):
        assert gate.value_grounded(9, "operated 9 vans") is True
        assert gate.value_grounded(1, "one client served") is True

    def test_small_int_does_not_match_stray_digits(self):
        # the hardened small-int rule: "1" must NOT ground against "$10,000" (no literal 1 token)
        assert gate.value_grounded(1, "spent $10,000 on supplies") is False

    def test_small_int_no_numeral_does_not_ground(self):
        assert gate.value_grounded(1, "a food truck") is False
        assert gate.value_grounded(2, "second highest in the region") is False

    def test_efd61fea_trap_no_cross_token_join(self):
        # 111 must NOT ground by spanning "...11 individuals ... 121 tours"
        assert gate.value_grounded(111, "11 individuals took 121 tours") is False

    def test_no_numeric_value_returns_none(self):
        assert gate.value_grounded("qualitative impact", "lots of text") is None


# ============================================================
# float finiteness + bounds (S1)
# ============================================================

class TestFloatBounds:
    def test_to_float_rejects_nan(self):
        assert gate.to_float(float("nan")) is None

    def test_to_float_rejects_inf(self):
        assert gate.to_float(float("inf")) is None
        assert gate.to_float(float("-inf")) is None

    def test_to_float_rejects_overflow(self):
        assert gate.to_float(1e16) is None
        assert gate.to_float(-1e16) is None

    def test_to_float_accepts_in_bounds(self):
        assert gate.to_float(5000) == 5000.0
        assert gate.to_float("$4,400,000") == 4_400_000.0

    def test_value_in_bounds_distinguishes_cases(self):
        assert gate.value_in_bounds(float("nan")) is False
        assert gate.value_in_bounds(1e16) is False
        assert gate.value_in_bounds(5000) is True
        assert gate.value_in_bounds("not a number") is True  # plain no-number, not out-of-bounds

    def test_verdict_quarantines_out_of_bounds_first(self):
        idmap = _idmap(t0=_text("anything"))
        dec, reason = gate.verdict({"metric_value": float("inf"), "label": "x"}, "t0", "t0", idmap)
        assert dec == "quarantine" and reason == "value_out_of_bounds"

    def test_overflow_quarantines_before_grounding(self):
        idmap = _idmap(t0=_text("10000000000000000"))
        dec, reason = gate.verdict({"metric_value": 1e16, "label": "x"}, "t0", "t0", idmap)
        assert dec == "quarantine" and reason == "value_out_of_bounds"


# ============================================================
# the verdict chain (AC1) — fixed precedence, first-failure-wins
# ============================================================

class TestVerdict:
    def test_publish_value_and_subject_grounded(self):
        idmap = _idmap(t0=_text("served 5,000 families in our shelter program"))
        dec, reason = gate.verdict(
            {"metric_value": 5000, "label": "families shelter served"}, "t0", "t0", idmap)
        assert dec == "publish" and reason == "ok"

    def test_no_numeric_value(self):
        idmap = _idmap(t0=_text("a strong year of impact"))
        dec, reason = gate.verdict({"metric_value": "lots", "label": "impact"}, "t0", "t0", idmap)
        assert dec == "quarantine" and reason == "no_numeric_value"

    def test_value_not_at_marker(self):
        idmap = _idmap(t0=_text("our mission statement here"))
        dec, reason = gate.verdict({"metric_value": 5000, "label": "served"}, "t0", "t0", idmap)
        assert dec == "quarantine" and reason == "value_not_at_marker"

    def test_subject_not_grounded(self):
        # value grounds at t0, but subject marker t1 shares no words and is on a different page
        idmap = _idmap(
            t0=_text("5,000", page=1),
            t1=_text("unrelated heading", page=9),
        )
        dec, reason = gate.verdict({"metric_value": 5000, "label": "homeless veterans housed"}, "t0", "t1", idmap)
        assert dec == "quarantine" and reason == "subject_not_grounded"

    def test_small_int_not_a_metric_when_measured_false(self):
        idmap = _idmap(t0=_text("ranked 2 in the nation"))
        dec, reason = gate.verdict(
            {"metric_value": 2, "label": "ranked nation"}, "t0", "t0", idmap, measured=False)
        assert dec == "quarantine" and reason == "not_a_metric"

    def test_small_int_publishes_when_measured_true(self):
        idmap = _idmap(t0=_text("operated 9 vans across the county"))
        dec, reason = gate.verdict(
            {"metric_value": 9, "label": "vans operated"}, "t0", "t0", idmap, measured=True)
        assert dec == "publish" and reason == "ok"

    def test_subject_grounded_by_colocation(self):
        # subject marker shares no label words but is co-located (same row) with the value
        idmap = _idmap(
            c0=_text("5000", page=1, row=3, col=1),
            c1=_text("a totally different phrase", page=1, row=3, col=0),
        )
        dec, reason = gate.verdict({"metric_value": 5000, "label": "zzz"}, "c0", "c1", idmap)
        assert dec == "publish"


# ============================================================
# co-location helper
# ============================================================

class TestCoLocated:
    def test_same_marker(self):
        idmap = _idmap(c0=_text("x", row=1, col=1))
        assert gate.co_located("c0", "c0", idmap) is True

    def test_same_row_same_page(self):
        idmap = _idmap(c0=_text("x", page=2, row=4, col=1), c1=_text("y", page=2, row=4, col=2))
        assert gate.co_located("c0", "c1", idmap) is True

    def test_different_page_not_colocated(self):
        idmap = _idmap(c0=_text("x", page=1, row=4, col=1), c1=_text("y", page=2, row=4, col=2))
        assert gate.co_located("c0", "c1", idmap) is False

    def test_missing_marker(self):
        assert gate.co_located("c0", "nope", _idmap(c0=_text("x"))) is False


# ============================================================
# per-metric time budget (S4)
# ============================================================

class TestTimeBudget:
    def test_budget_exceeded_raises(self):
        # a fake clock that jumps far past the budget between checks
        ticks = iter([0.0, 100.0, 100.0, 100.0])
        idmap = _idmap(t0=_text("served 5000 families"))
        with pytest.raises(gate.GateTimeout):
            gate.verdict({"metric_value": 5000, "label": "families served"}, "t0", "t0", idmap,
                         budget_s=1.0, clock=lambda: next(ticks))

    def test_no_budget_never_times_out(self):
        idmap = _idmap(t0=_text("served 5000 families"))
        dec, _ = gate.verdict({"metric_value": 5000, "label": "families served"}, "t0", "t0", idmap,
                              budget_s=None)
        assert dec == "publish"

    def test_giant_input_is_bounded(self):
        # a crafted megabyte cell is truncated to MAX_GATE_TEXT — no hang, linear scan
        big = ("9 " * 1_000_000) + "5000 families"
        idmap = _idmap(t0=_text(big))
        # completes quickly and deterministically (no ReDoS)
        gate.value_grounded(5000, big)
        assert len(gate._cap(big)) == gate.MAX_GATE_TEXT


# ============================================================
# determinism (AC11)
# ============================================================

class TestDeterminism:
    def test_identical_inputs_identical_verdict(self):
        idmap = _idmap(t0=_text("served 5,000 families in our shelter"))
        m = {"metric_value": 5000, "label": "families shelter served"}
        out = {gate.verdict(m, "t0", "t0", idmap) for _ in range(50)}
        assert len(out) == 1

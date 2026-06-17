"""Phase-7 regression lock: the gate oracle vs the frozen counterexample fixture
(Spec 0069 AC1/AC8). If the ported gate logic ever drifts from the research verdicts,
one of these cases fails. CI runs this file.
"""
from __future__ import annotations

import json
import os

import pytest

from lavandula.nlp import gate

_FIX = os.path.join(os.path.dirname(__file__), "fixtures", "0069_gate_counterexamples.json")
_CASES = json.load(open(_FIX))["cases"]


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_oracle_matches_frozen_verdict(case):
    value = float("inf") if case.get("value_is_inf") else case["value"]
    idmap = {"t0": {"kind": "text", "text": case["text"], "page": 1,
                    "row": None, "col": None, "table": None, "idx": 0,
                    "bbox": None, "row_text": None}}
    dec, reason = gate.verdict({"metric_value": value, "label": case["label"]},
                               "t0", "t0", idmap, measured=case["measured"])
    assert dec == case["expect_decision"], f"{case['name']}: decision {dec} != {case['expect_decision']} ({reason})"
    assert reason == case["expect_reason"], f"{case['name']}: reason {reason} != {case['expect_reason']}"

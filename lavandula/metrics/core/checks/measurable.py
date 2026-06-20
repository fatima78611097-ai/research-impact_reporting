"""Measurable — quarantine a value of 1/2 that isn't an actual printed count.

The model sometimes assigns a fake count of 1 to a narrative ("men, women and children had
a safe home" -> value=1). Signal: the small value isn't even a digit token in the statement,
so it was inferred, not counted. A real "1 new clinic opened" keeps its printed "1" and passes.
Ported concept from nlp/measure_check (the small-int "is this a real measurable count").
"""
from __future__ import annotations

import re

from ..types import Metric, CheckResult, QUARANTINE


def measurable(m: Metric) -> CheckResult:
    try:
        v = int(float(m.value))
    except (TypeError, ValueError):
        return CheckResult()
    if v in (1, 2):
        toks = re.findall(r"\d+", (m.statement or "").replace(",", ""))
        if str(v) not in toks:
            return CheckResult(QUARANTINE, "value 1/2 not a printed count (event, not a metric)")
    return CheckResult()

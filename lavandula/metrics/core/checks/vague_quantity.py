"""Vague-quantity — quarantine a vague magnitude word rendered as a precise number.

"Thousands of families looked to us" is not a count of 1000. High-precision: only the
unambiguous vague-magnitude phrases (a real "1.2 million meals" has no "... of" and is kept).
New this session (found on 128de607: "Thousands of Dane County families…" value=1000).
"""
from __future__ import annotations

import re

from ..types import Metric, CheckResult, QUARANTINE

# a leading NUMBER makes it precise ("$4.9 million of care" is kept); only a bare magnitude
# word ("thousands of", "millions of") with no number is vague.
_MAG = re.compile(r"(\d[\d.,]*\s+)?\b(thousands?|hundreds?|dozens?|scores|millions?|billions?)\s+of\b", re.I)
_WORDS = re.compile(r"\b(countless|numerous|myriad)\b", re.I)


def vague_quantity(m: Metric) -> CheckResult:
    t = m.statement or ""
    if _WORDS.search(t):
        return CheckResult(QUARANTINE, "vague quantity — not a precise count")
    mm = _MAG.search(t)
    if mm and not mm.group(1):          # magnitude word with NO number in front -> vague
        return CheckResult(QUARANTINE, "vague quantity — not a precise count")
    return CheckResult()

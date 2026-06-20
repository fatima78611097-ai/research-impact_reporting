"""is_a_metric — quarantine numbers that aren't impact metrics (the prompt's reject list).

Categories from the metric definition: rankings/ordinals, forecasts/goals, durations/tenure,
awards/honors, dates-of-founding. Deterministic, HIGH-PRECISION regexes — tuned to avoid
rejecting real metrics that merely mention a year ("served 500 families in 2023" is kept;
only founding/since dates are rejected). Tenure-anniversary is already in quality_text.
"""
from __future__ import annotations

import re

from ..types import Metric, CheckResult, QUARANTINE

_RANK = re.compile(r"#\s*\d+\b|\b\d+(st|nd|rd|th)\s+(largest|biggest|best|ranked|"
                   r"in the (nation|state|country))\b|\branked\s+#?\d+\b", re.I)
_FORECAST = re.compile(r"\b(goal of|target of|aim(s|ing)?\s+to|hope(s|ing)?\s+to|"
                       r"projected|on track to|by\s+20[3-9]\d)\b", re.I)
_YOS = re.compile(r"\b\d+\s+years?\s+of\s+(service|operation|experience|excellence)\b", re.I)


def _is_duration(m: Metric) -> bool:
    """True only when the VALUE itself is the duration (value=9 <-> '9-month'), so a real
    metric that merely mentions a program's length ('200 served via our 5-year program') is kept."""
    if _YOS.search(m.statement or ""):
        return True
    try:
        v = str(int(float(m.value)))
    except (TypeError, ValueError):
        return False
    return bool(re.search(rf"\b{re.escape(v)}[-\s](year|month|week|day)s?\b", m.statement or "", re.I))
# NOT bare "awarded" — "57 individuals awarded scholarships" is a real OUTPUT (the org gave them).
# Only awards/honors the org RECEIVED.
_AWARD = re.compile(r"\b(honou?ree|finalist|recognized as|named\s+(one of|to the)|"
                    r"accredited|\d+[-\s]star\b)\b", re.I)
_DATE = re.compile(r"\b(founded|established|est\.?|incorporated)\s+(in\s+)?(18|19|20)\d\d\b", re.I)


def _is_founding_date(m: Metric) -> bool:
    """Only when the VALUE itself is the year (value=1998 <-> 'founded 1998'), so a real metric
    with a timeframe ('99% of evictions prevented since 2002', value=99) is kept."""
    if not _DATE.search(m.statement or ""):
        return False
    try:
        v = int(float(m.value))
    except (TypeError, ValueError):
        return False
    return 1850 <= v <= 2099


def is_a_metric(m: Metric) -> CheckResult:
    t = m.statement or ""
    if _RANK.search(t):
        return CheckResult(QUARANTINE, "ranking / ordinal, not a metric")
    if _FORECAST.search(t):
        return CheckResult(QUARANTINE, "forecast / goal, not yet achieved")
    if _is_duration(m):
        return CheckResult(QUARANTINE, "duration / tenure, not a metric")
    if _AWARD.search(t):
        return CheckResult(QUARANTINE, "award / honor, not a metric")
    if _is_founding_date(m):
        return CheckResult(QUARANTINE, "founding date, not a metric")
    return CheckResult()

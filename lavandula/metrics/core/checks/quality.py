"""Quality checks — reject text the model emits as a "metric" that isn't one.

Ported verbatim from nlp/slot_render (`metric_quality` + `subject_quality`), with the
LENGTH rules DROPPED ("too long / reads as a sentence / long label") — those policed a
constructed short label and wrongly quarantine the model's natural descriptions.

Both run on the model's own `statement` (no construction). Deterministic, high-precision.
"""
from __future__ import annotations

import re

from ..types import Metric, CheckResult, QUARANTINE

# —— metric-level junk ——
_TENURE = re.compile(r"\b\d+\s*(st|nd|rd|th)\s+(year|anniversary)\b", re.I)
_FIN_FRAG = re.compile(r"\bgrowth on\b|\bawarded\s+\d+\s+grants?\b|%\s*of\s+(revenue|budget|overall|the\s+award)", re.I)

# —— subject-level junk ——
_GRATITUDE = re.compile(r"\b(thank you|thanks|grateful|gratitude|generous|sincere(?:ly)?|"
                        r"proud(?:ly)? to|honou?red|pleased to|delighted|we appreciate|"
                        r"appreciation|salute|shout[- ]?out|kudos)\b", re.I)
_BARE_META = re.compile(r"^(total|number|amount|count|sum|balance|net|gross|subtotal|figures?)\s*:?\s*$", re.I)
_FY_HEADER = re.compile(r"\b(number|figures?|count|amount)\b.*\b(beginning|end)\s+of\b", re.I)
_PREAUDIT = re.compile(r"\bpre-?audit\b", re.I)
_ONLY_PUNCT = re.compile(r"^[\W\d]+$")


def quality_text(m: Metric) -> CheckResult:
    t = m.statement or ""
    if _TENURE.search(t):
        return CheckResult(QUARANTINE, "tenure / anniversary, not a metric")
    if _FIN_FRAG.search(t):
        return CheckResult(QUARANTINE, "financial-statement fragment")
    return CheckResult()


def quality_subject(m: Metric) -> CheckResult:
    s = (m.statement or "").strip()
    if not s or _ONLY_PUNCT.match(s):
        return CheckResult(QUARANTINE, "empty / no words")
    if _GRATITUDE.search(s):
        return CheckResult(QUARANTINE, "gratitude / marketing blurb, not a metric")
    if _BARE_META.match(s) or _FY_HEADER.search(s) or _PREAUDIT.search(s):
        return CheckResult(QUARANTINE, "contentless statement header")
    return CheckResult()

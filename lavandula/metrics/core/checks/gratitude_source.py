"""gratitude_source — FLAG a metric whose VERBATIM source is acknowledgement, not a result.

The model can launder gratitude framing OUT of the statement ("Thank you to the 450 members
in our 18 conferences" -> "450 members in our 18 conferences"), defeating a statement-only
gratitude check. So this checks the SOURCE_SNIPPET (verbatim — the model can't sanitize it).

This is a FIND net, not a verdict: high recall, deliberately imprecise. It catches both
"thanking our 450 members" (fluff) and "thank you for delivering 12,000 meals" (a real result
in a thank-you). The keep/drop DECISION is a downstream model-judge's job (find -> fix). To
keep the net from being all-noise, it only fires on CAPACITY-tier metrics — the kind you thank
(members/supporters/donors/volunteers), where gratitude framing most often means "fluff",
while reach/outcome results in a thank-you are usually real and left for the judge.

FLAGS only, never quarantines.
"""
from __future__ import annotations

import re

from ..types import Metric, CheckResult

_GRATITUDE = re.compile(r"\b(thank you|thanks to|grateful|gratitude|we appreciate|appreciation|"
                        r"with thanks|deeply thankful|heartfelt thanks)\b", re.I)


def gratitude_source(m: Metric) -> CheckResult:
    src = m.prov.source_snippet or ""
    if not _GRATITUDE.search(src):
        return CheckResult()
    # narrow the net: only capacity-tier (the thing being thanked), so reach/outcome results
    # that merely appear inside a thank-you sentence are left to the model-judge, not flagged here.
    if m.tier != "capacity_input":
        return CheckResult()
    return CheckResult("flag", "source is acknowledgement/thank-you, not a reported result",
                       flag="gratitude_source")

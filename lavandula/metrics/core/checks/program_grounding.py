"""program_grounding — FLAG a program not supported by the metric's LOCAL context.

The model attributes `program` by reading the report. This grounds that against where the
metric actually sits: a real attribution has the program named in the metric's own vicinity —
its statement, its verbatim source snippet, its subject text, or its section heading. If the
program appears in NONE of those, it was pulled from elsewhere in the document and is suspect
(fabrication, or a real program name mis-attached to the wrong metric) — flag it.

Why local context, not the heading alone: reports state programs inline at least as often as in
headings ("our Camp Weaver program served…", "Our Annual Giving Campaign raised…"), and headings
are frequently decorative ("COMMUNITY IMPACT") or garbled. Heading-only over-flagged ~44%; local
context grounds those correctly.

FLAGS only (never quarantines). Only metrics that HAVE a program are judged.
"""
from __future__ import annotations

import re

from ..types import Metric, CheckResult


def _toks(s):
    return set(w for w in re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower()).split() if len(w) > 2)


# generic words that don't tie a program to a place (so "Community Health services" matching the
# word "community" in a "COMMUNITY IMPACT" heading wouldn't count as real support).
_STOP = {"program", "programs", "service", "services", "project", "projects", "initiative",
         "fund", "center", "centre", "community", "impact", "annual", "our", "the"}


def program_grounding(m: Metric) -> CheckResult:
    prog = (m.program or "").strip()
    if not prog:
        return CheckResult()                              # no program to ground
    pt = _toks(prog) - _STOP
    if not pt:
        return CheckResult()                              # program is only generic words — can't judge
    # local context the program should be named in if it really belongs to this metric
    context = _toks(" ".join(filter(None, [
        m.statement, m.prov.source_snippet, m.subject, m.prov.section_heading,
    ])))
    if pt & context:
        return CheckResult()                              # a distinctive program word is present locally
    return CheckResult("flag",
                       f'program "{prog}" not named in the metric\'s local context',
                       flag="program_ungrounded")

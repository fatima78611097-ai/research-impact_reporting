"""Measured tests for the gate checks — the discipline: every check must catch its known-bad
and flag none of its known-good. Cases live in fixtures/check_cases.json (data, not code).

    python3 -m lavandula.metrics.tests.test_checks
"""
from __future__ import annotations

import json
import os
import sys

from ..core.types import Metric
from ..core.checks.quality import quality_text, quality_subject
from ..core.checks.vague_quantity import vague_quantity
from ..core.checks.measurable import measurable
from ..core.checks.is_a_metric import is_a_metric
from ..core.checks.incompleteness import incompleteness
from ..core.checks.dedup import duplicate_indices

CASES = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "check_cases.json")))


def _mk(c, idx=0):
    return Metric(content_sha256="test", idx=idx, statement=c["statement"], value=c.get("value"))


def run() -> int:
    fails = 0
    for name, fn in (("quality_text", quality_text), ("quality_subject", quality_subject),
                     ("vague_quantity", vague_quantity), ("measurable", measurable),
                     ("is_a_metric", is_a_metric), ("incompleteness", incompleteness)):
        cases = CASES.get(name, [])
        ok = 0
        for c in cases:
            got = fn(_mk(c)).verdict
            if got == c["expect"]:
                ok += 1
            else:
                fails += 1
                print(f"  FAIL {name}: {c['statement'][:45]!r} expected {c['expect']} got {got}")
        print(f"{name}: {ok}/{len(cases)} pass")

    # program_grounding needs a section_heading on provenance — test inline
    from ..core.checks.program_grounding import program_grounding
    from ..core.types import Provenance
    # (program, statement, section_heading, expect) — program is grounded if named in local context
    pg_cases = [
        ("Camp Weaver", "1,763 children developed belonging at Camp Weaver", "FIND YOUR Y", "ok"),
        ("PAL", "100% obtained vital documents", "PAL PROGRAM", "ok"),       # in heading
        ("Annual Giving Campaign", "Our Annual Giving Campaign raised $657,713", "A LOOK BACK", "ok"),
        ("Foster Care", "103 total clients served", "COUNSELING", "flag"),   # nowhere local
        (None, "anything", "ANY", "ok"),
    ]
    okc = 0
    for prog, stmt, heading, exp in pg_cases:
        mm = Metric(content_sha256="t", idx=0, program=prog, statement=stmt)
        mm.prov = Provenance(section_heading=heading)
        got = program_grounding(mm).verdict
        if got == exp:
            okc += 1
        else:
            fails += 1
            print(f"  FAIL program_grounding: {prog!r} / {stmt[:30]!r} expected {exp} got {got}")
    print(f"program_grounding: {okc}/{len(pg_cases)} pass")

    dl = CASES.get("dedup", [])
    if dl:
        ms = [_mk(c, i) for i, c in enumerate(dl)]
        dups = duplicate_indices(ms)
        exp = {i for i, c in enumerate(dl) if c.get("expect") == "duplicate"}
        if dups == exp:
            print(f"dedup: pass (dups={sorted(dups)})")
        else:
            fails += 1
            print(f"  FAIL dedup: got {sorted(dups)} expected {sorted(exp)}")

    print("\n" + ("ALL CHECKS PASS" if fails == 0 else f"{fails} FAILURE(S)"))
    return fails


if __name__ == "__main__":
    sys.exit(1 if run() else 0)

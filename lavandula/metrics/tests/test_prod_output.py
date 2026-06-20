"""Offline test: the Metric -> lava_impact.metrics row mapping (no DB needed).

Guards the contract<->column mapping so a field never silently drops on the way to RDS.

    python3 -m lavandula.metrics.tests.test_prod_output
"""
from __future__ import annotations

import json
import sys

from ..core.types import Metric, Provenance
from ..adapters.prod_output import _row, _UPSERT


def run() -> int:
    m = Metric(content_sha256="abc12345def", idx=3, org="Test Org", report_year=2024,
               statement="685 families served", value=685, value_text="685", unit="families",
               tier="reach_output", subject="families served")
    m.prov = Provenance(value_page=5, subject_page=5, value_ref="c17", subject_ref="t42",
                        value_bbox={"l": 1, "t": 2, "r": 3, "b": 4}, same_marker=False,
                        source_snippet="685 families served")
    m.decision = "publish"
    m.add_flag("mispair_suspect", "far apart")

    r = _row(m, run_id=7)
    fails = 0

    def chk(cond, msg):
        nonlocal fails
        if not cond:
            fails += 1; print(f"  FAIL {msg}")

    chk(r["statement"] == "685 families served", "statement maps")
    chk(r["value"] == 685, "value maps")
    chk(r["logic_tier"] == "reach_output", "tier -> logic_tier")
    chk(r["gate_decision"] == "publish", "decision maps")
    chk(r["run_id"] == 7 and r["gate_run_id"] == 7, "run_id maps")
    chk(r["value_page"] == 5 and r["subject_ref"] == "t42", "provenance maps")
    chk(json.loads(r["value_bbox"]) == {"l": 1, "t": 2, "r": 3, "b": 4}, "bbox json-encoded")
    chk(json.loads(r["gate_flags"]) == ["mispair_suspect"], "flags json-encoded")

    # every bind param in the UPSERT must be present in the row dict (no missing keys at execute)
    import re
    binds = set(re.findall(r":(\w+)", str(_UPSERT)))
    missing = binds - set(r.keys()) - {"k", "r"}
    chk(not missing, f"UPSERT binds covered (missing: {missing})")

    print("ALL PASS" if fails == 0 else f"{fails} FAILURE(S)")
    return fails


if __name__ == "__main__":
    sys.exit(1 if run() else 0)

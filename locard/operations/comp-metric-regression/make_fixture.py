"""M0 — freeze the vision ground-truth as a regression fixture.

Merges the three vision-sweep result files (Opus verdicts: value_on_page / subject_pairing /
is_metric / tier / standalone) with the three sample files (gate_decision, code tier, validity) into
ONE frozen per-metric answer key. Every later milestone (M1-M4) scores its pipeline output against
this file. Deterministic; regenerating from the same inputs yields the same fixture.
"""
import json
import os

B = "/home/ubuntu/research/locard/operations/comp-metric-regression/"
RESULTS = ["vision-sweep-352-result.json", "vision-sweep-complement-result.json", "vision-sweep-rerun-result.json"]
SAMPLES = ["vision-sweep-sample.json", "vision-sweep-complement-sample.json", "vision-sweep-rerun-sample.json"]
OUT = B + "fixtures/vision-ground-truth.json"

raw = {}
for f in RESULTS:
    for v in json.load(open(B + f))["result"]["raw"]:
        raw[v["id"]] = v
refs = {}
for f in SAMPLES:
    for r in json.load(open(B + f))["records"]:
        refs[r["id"]] = r

records = []
for i in sorted(raw):
    v, r = raw[i], refs.get(i)
    if not r:
        continue
    records.append({
        "id": i,
        "gate_decision": r.get("ref_gate"),
        "code_tier": r.get("ref_tier"),
        "code_validity": r.get("ref_validity"),
        "value": r.get("value"),
        "statement": r.get("statement"),
        "vision": {
            "value_on_page": v["value_on_page"],
            "subject_pairing": v["subject_pairing"],
            "is_metric": v["is_metric"],
            "tier": v["tier"],
            "standalone": v.get("standalone"),
        },
    })

fixture = {
    "frozen": "2026-06-10",
    "source": "Opus vision sweep, 1605/1606, DEV-ONLY ground truth",
    "definition": "is-a-metric = vision is_metric=='metric' AND tier in {outcome_impact, reach_output, "
                  "capacity_input, financial}; activity + the 8 tier-0 types = not-a-metric. Tier = held.",
    "n": len(records),
    "records": records,
}
os.makedirs(B + "fixtures", exist_ok=True)
json.dump(fixture, open(OUT, "w"), indent=1)
print(f"froze {len(records)} ground-truth records -> {OUT}")
import collections
print("gate split:", dict(collections.Counter(r["gate_decision"] for r in records)))

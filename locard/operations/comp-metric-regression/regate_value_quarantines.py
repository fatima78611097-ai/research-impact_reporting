"""Re-run the gate (current, hyphen-fixed parser) over every value_not_at_marker quarantine
in the review set, to split the class accurately:

  recovered  = gate now PUBLISHES it (parser fix read a value it couldn't before) — free win, no pipeline change
  reground   = value still not at the cited marker, but it DOES appear at another marker → grounding-repair candidate
  derived    = value appears nowhere in the doc → genuinely model-derived, correct to quarantine
  other      = re-gate quarantined for a different reason (subject, no-numeric)

Faithful re-run: calls gate.verdict(metric, value_ref, subject_ref, idmap) with a fresh render,
not an approximation. The stale review-data.json `claude` field is NOT trusted.
"""
import json
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import gate
import render
from lavandula.common.db import make_app_engine

REVIEW = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/review-data.json"
RESULTS = {
    "batch1-clean": "/home/ubuntu/research/locard/operations/comp-metric-regression/scale100-results-tuned.json",
    "test2": "/home/ubuntu/research/locard/operations/comp-metric-regression/scale-test2-results.json",
}
OUT = "/home/ubuntu/research/locard/operations/comp-metric-regression/regate-value-quarantines.json"


def main():
    review = json.load(open(REVIEW))
    quar = [r for r in review if r.get("gate_decision") == "quarantine"
            and r.get("gate_reason") == "value_not_at_marker"]
    print(f"value_not_at_marker quarantines to re-gate: {len(quar) if False else len(quar := quar)}")

    results = {k: json.load(open(v)) for k, v in RESULTS.items()}
    # map sha8 -> (full_sha, metrics list) per set
    full_by_set = {k: {sha[:8]: sha for sha in res} for k, res in results.items()}

    eng = make_app_engine()
    buckets = Counter()
    rows = []
    idmap_cache = {}
    with eng.connect() as conn:
        for r in quar:
            setname, sha8, idx = r["set"], r["sha8"], int(r["id"].split(":")[1])
            full = full_by_set.get(setname, {}).get(sha8)
            if not full:
                buckets["unmapped"] += 1
                continue
            metric = results[setname][full][idx]
            if full not in idmap_cache:
                try:
                    _, _, idmap_cache[full] = render.render_tagged(conn, full)
                except Exception as e:
                    idmap_cache[full] = None
                    print(f"  render fail {sha8}: {e}")
            idmap = idmap_cache[full]
            if idmap is None:
                buckets["render-fail"] += 1
                continue

            # verdict() expects key 'metric_value' + 'label'; stored results use 'value' + 'label'
            m_for_gate = {"metric_value": metric.get("value"), "label": metric.get("label")}
            decision, reason = gate.verdict(m_for_gate, metric.get("value_ref"),
                                             metric.get("subject_ref"), idmap)
            co = distinct = None
            if decision == "publish":
                bucket = "recovered"
            elif reason == "value_not_at_marker":
                # value still not at cited marker — does it live elsewhere, and WITH the subject?
                v = gate.to_float(metric.get("value"))
                tol = max(1.0, 0.001 * abs(v)) if v is not None else 0
                subj_w = set(re.findall(r"[a-z]{4,}", (metric.get("label") or "").lower()))
                co = False
                for k, it in idmap.items():
                    if v is not None and any(abs(c - v) <= tol for c in gate.candidate_numbers(it.get("text") or "")):
                        if subj_w & set(re.findall(r"[a-z]{4,}", (it.get("text") or "").lower())):
                            co = True
                # distinctiveness: big/specific values rarely collide by chance; small ones do
                distinct = v is not None and abs(v) >= 1000
                anyloc = any(v is not None and any(abs(c - v) <= tol for c in gate.candidate_numbers(it.get("text") or ""))
                             for it in idmap.values())
                if not anyloc:
                    bucket = "derived"
                elif co:
                    bucket = "reground-strong"       # value + subject co-occur at another marker = real mis-grounding
                elif distinct:
                    bucket = "reground-distinct"      # distinctive value elsewhere, subject not co-located = likely real
                else:
                    bucket = "reground-weak"          # small/common value elsewhere, no subject = likely coincidence
            else:
                bucket = "other"
            buckets[bucket] += 1
            rows.append({"id": r["id"], "value": metric.get("value"), "bucket": bucket, "co": co, "distinct": distinct,
                         "new_decision": decision, "new_reason": reason,
                         "statement": (metric.get("statement") or "")[:90]})

    print()
    print("=== RE-GATE SPLITS (current parser) ===")
    for b in ["recovered", "reground-strong", "reground-distinct", "reground-weak", "derived", "other", "render-fail", "unmapped"]:
        if buckets[b]:
            print(f"  {buckets[b]:3}  {b}")
    tot = sum(buckets.values())
    print(f"  ---  {tot} total")
    json.dump({"total": tot, "buckets": dict(buckets), "rows": rows}, open(OUT, "w"), indent=1)
    print(f"saved {OUT}")
    # sanity: db75f054:7
    s = next((x for x in rows if x["id"] == "db75f054:7"), None)
    print("\nsanity db75f054:7 ->", s)


if __name__ == "__main__":
    main()

"""FAITHFUL re-gate: re-run the hardened gate.verdict() over every published metric using the REAL
grounding inputs (rendered idmap + the metric's value_ref/subject_ref), exactly like production --
not the v_text shortcut (which wrongly fails table-cell metrics). Verify against vision truth that
small-int fabrications get caught and real metrics (small or large) are NOT dropped."""
import collections
import json
import sys

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import gate
import render
from lavandula.common.db import make_app_engine

VIS = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision"
B = "/home/ubuntu/research/locard/operations/comp-metric-regression/"
RES = {"batch1-clean": B + "scale100-results-tuned.json", "test2": B + "scale-test2-results.json"}

review = {r["id"]: r for r in json.load(open(VIS + "/review-data.json"))}
mv_flags = set(json.load(open(B + "measurable-value-quarantine.json"))["quarantine"])
vision = {}
for f in ["vision-sweep-352-result.json", "vision-sweep-complement-result.json", "vision-sweep-rerun-result.json"]:
    for v in json.load(open(B + f))["result"]["raw"]:
        vision[v["id"]] = v
results = {k: json.load(open(p)) for k, p in RES.items()}
full_by_set = {k: {s[:8]: s for s in r} for k, r in results.items()}


def vision_real_metric(v):
    return v and v["value_on_page"] == "yes" and v["subject_pairing"] == "correct" and v["is_metric"] == "metric"


pub = [r for r in review.values() if r.get("gate_decision") == "publish"]
eng = make_app_engine()
cache = {}
old_pub_new_quar = []   # (id, reason, fix)
errors = 0
with eng.connect() as conn:
    def idm(f):
        if f not in cache:
            _, _, cache[f] = render.render_tagged(conn, f)
        return cache[f]
    for r in pub:
        setn, sha8 = r.get("set"), r.get("sha8")
        try:
            idx = int(r["id"].split(":")[1])
        except (ValueError, IndexError):
            errors += 1
            continue
        full = full_by_set.get(setn, {}).get(sha8)
        if not full:
            errors += 1
            continue
        try:
            m = results[setn][full][idx]
        except (KeyError, IndexError):
            errors += 1
            continue
        idmap = idm(full)
        val = m.get("value")
        measured = None
        if gate.is_small_int(val):
            measured = r["id"] not in mv_flags
        dec, reason = gate.verdict({"metric_value": val, "label": m.get("label")},
                                   m.get("value_ref"), m.get("subject_ref"), idmap, measured=measured)
        if dec == "quarantine":
            fix = "classifier" if reason == "not_a_metric" else "hardening/grounding"
            old_pub_new_quar.append((r["id"], reason, fix))

print(f"published re-gated: {len(pub) - errors}  (skipped {errors})")
print(f"newly QUARANTINED by hardened gate: {len(old_pub_new_quar)}")
print(f"   by fix: {dict(collections.Counter(fix for _, _, fix in old_pub_new_quar))}")
gc = sum(1 for i, _, _ in old_pub_new_quar if not vision_real_metric(vision.get(i)))
bd = [(i, fx) for i, _, fx in old_pub_new_quar if vision_real_metric(vision.get(i))]
print(f"   vision says BAD (correct catch): {gc}")
print(f"   vision says GOOD (wrong drop):   {len(bd)}")

small_fab = [r["id"] for r in pub if gate.is_small_int(r.get("value"))
             and vision.get(r["id"], {}).get("value_on_page") == "no"]
caught = set(i for i, _, _ in old_pub_new_quar)
fab_caught = [i for i in small_fab if i in caught]
print(f"\n=== TARGET small-int fabrications: {len(small_fab)} -> caught {len(fab_caught)} ({100*len(fab_caught)/max(1,len(small_fab)):.0f}%) ===")
print(f"   missed: {[i for i in small_fab if i not in caught]}")
print(f"\n=== SAFETY: real metrics wrongly dropped: {len(bd)} ===")
for i, fx in bd[:25]:
    print(f"   [{fx}] {i:13} val={review[i].get('value')} | {review[i].get('statement','')[:60]}")

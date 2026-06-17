"""Re-run the HARDENED gate over every currently-published metric and verify, against vision truth:
(1) the small-int fabrications get caught, (2) zero real small counts get dropped. The fix only makes
the gate stricter (exact small-int grounding + small-int classifier gate), so quarantines can only
grow -- we re-evaluate the 1,433 published; quarantined stay quarantined."""
import json
import sys

sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import gate

VIS = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision"
B = "/home/ubuntu/research/locard/operations/comp-metric-regression/"

review = {r["id"]: r for r in json.load(open(VIS + "/review-data.json"))}
mv_flags = set(json.load(open(B + "measurable-value-quarantine.json"))["quarantine"])  # classifier: NOT-a-metric
vision = {}
for f in ["vision-sweep-352-result.json", "vision-sweep-complement-result.json", "vision-sweep-rerun-result.json"]:
    for v in json.load(open(B + f))["result"]["raw"]:
        vision[v["id"]] = v


def new_decision(r):
    """Apply the hardened gate to a currently-published metric. Returns (decision, reason, which_fix)."""
    val = r.get("value")
    vg = gate.value_grounded(val, r.get("v_text") or "")
    if vg is None:
        return "quarantine", "no_numeric_value", "hardening"
    if not vg:
        return "quarantine", "value_not_at_marker", "hardening"   # Part 2: exact small-int grounding
    if gate.is_small_int(val):
        measured = r["id"] not in mv_flags
        if not measured:
            return "quarantine", "not_a_metric", "classifier"      # Part 1: small-int semantic gate
    return "publish", "ok", None


def vision_real_metric(v):
    return v and v["value_on_page"] == "yes" and v["subject_pairing"] == "correct" and v["is_metric"] == "metric"


pub = [r for r in review.values() if r.get("gate_decision") == "publish"]
newq = []
for r in pub:
    dec, reason, fix = new_decision(r)
    if dec == "quarantine":
        newq.append((r, reason, fix))

# safety: real small counts that would be dropped
small_pub = [r for r in pub if gate.is_small_int(r.get("value"))]
small_real = [r for r in small_pub if vision_real_metric(vision.get(r["id"]))]
dropped_real = [(r, fix) for r, reason, fix in newq if vision_real_metric(vision.get(r["id"]))]

# small fabrications (vision says value not on page, value<=20)
small_fab = [r for r in pub if gate.is_small_int(r.get("value")) and vision.get(r["id"], {}).get("value_on_page") == "no"]
fab_caught = [r for r, reason, fix in newq if r in small_fab]

print(f"currently published: {len(pub)}")
print(f"newly QUARANTINED by the hardened gate: {len(newq)}")
import collections
byfix = collections.Counter(fix for _, _, fix in newq)
print(f"   by fix: {dict(byfix)}")

# of the newly quarantined, how many does vision say were BAD (good catch) vs GOOD (bad drop)?
good_catch = sum(1 for r, _, _ in newq if not vision_real_metric(vision.get(r["id"])))
bad_drop = sum(1 for r, _, _ in newq if vision_real_metric(vision.get(r["id"])))
print(f"   vision says BAD (correct catch): {good_catch}")
print(f"   vision says GOOD (wrong drop):   {bad_drop}")

print(f"\n=== TARGET: small-int fabrications ===")
print(f"   small fabrications (value<=20, not on page): {len(small_fab)}")
print(f"   now CAUGHT (quarantined): {len(fab_caught)} ({100*len(fab_caught)/max(1,len(small_fab)):.0f}%)")
missed = [r['id'] for r in small_fab if r not in [x[0] for x in newq]]
print(f"   still missed: {len(missed)} {missed}")

print(f"\n=== SAFETY: real small counts ===")
print(f"   real small counts currently published (vision-confirmed): {len(small_real)}")
print(f"   wrongly dropped by the fix: {len(dropped_real)}")
for r, fix in dropped_real:
    print(f"      [{fix}] {r['id']:13} val={r['value']} | {r.get('statement','')[:64]}")
EOF_MARKER = None

"""Labeled true/false test set + gate scoring (confusion matrix).

TRUE cases  = the confirmed-correct published metrics (gate should PUBLISH).
FALSE cases = deliberately broken, each genuinely wrong by construction:
  fab_value   - value not present at its cited marker (fabricated/wrong number)
  invent_ref  - value_ref is a marker we never issued (fabrication)
  mispair_far - subject swapped to a far, unrelated marker (gross mispairing)
  mispair_row - subject swapped to a SAME-ROW sibling cell (the #9 column case)
Scores the gate (pure code, no API) and prints recall (catch-rate), false-publish,
and false-quarantine. mispair_row is expected to be MISSED — the known blind spot.
"""
import json
import re
import sys

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import gate
import render
from lavandula.common.db import make_app_engine

packets = json.load(open("locard/operations/comp-metric-regression/gold-sample.json"))
tuned = json.load(open("locard/operations/comp-metric-regression/scale100-results-tuned.json"))
full = {d["sha"][:8]: d["sha"] for d in json.load(open("/tmp/baseline-100.json"))["docs"]}
lut = {(sha[:8], r["statement"]): r for sha, recs in tuned.items() for r in recs}


def words(s):
    return set(re.findall(r"[a-z]{4,}", (s or "").lower()))


def make_bad_value(value, vtext):
    for cand in (str(value) + "1357", str(value) + "9", "98" + str(value)):
        try:
            n = float(cand)
        except ValueError:
            continue
        if not gate.value_grounded(n, vtext):
            return n
    return None


def pick_far_unrelated(label, vr, idmap):
    lw = words(label)
    best = None
    for k, v in idmap.items():
        if k == vr:
            continue
        if words(v.get("text", "")) & lw:
            continue
        if gate.co_located(vr, k, idmap):
            continue
        return k
    return best


def pick_samerow_sibling(label, vr, idmap):
    a = idmap.get(vr)
    if not a or a.get("row") is None:
        return None
    for k, v in idmap.items():
        if k == vr or v.get("kind") != "cell":
            continue
        if v.get("page") == a.get("page") and v.get("row") == a.get("row") and not (words(v.get("text", "")) & words(label)):
            return k
    return None


eng = make_app_engine()
idmaps = {}
cases = []
with eng.connect() as c:
    for p in packets:
        sha = full[p["sha8"]]
        if sha not in idmaps:
            idmaps[sha] = render.render_tagged(c, sha)[2]
        im = idmaps[sha]
        rec = lut.get((p["sha8"], p["statement"]), {})
        vr, sr = rec.get("value_ref"), rec.get("subject_ref")
        if not vr or not sr:
            continue
        val, lab = p["value"], p["label"]
        vtext = (im.get(vr) or {}).get("text", "")
        cases.append((p["sha8"], "TRUE", val, lab, vr, sr, "publish", im))
        bad = make_bad_value(val, vtext)
        if bad is not None:
            cases.append((p["sha8"], "fab_value", bad, lab, vr, sr, "quarantine", im))
        cases.append((p["sha8"], "invent_ref", val, lab, "zz_never_issued", sr, "quarantine", im))
        far = pick_far_unrelated(lab, vr, im)
        if far:
            cases.append((p["sha8"], "mispair_far", val, lab, vr, far, "quarantine", im))
        sib = pick_samerow_sibling(lab, vr, im)
        if sib:
            cases.append((p["sha8"], "mispair_row(#9)", val, lab, vr, sib, "quarantine", im))

import collections
bytype = collections.defaultdict(lambda: [0, 0])  # [correct, total]
TP = FN = TN = FP = 0
for sha8, typ, val, lab, vr, sr, expected, im in cases:
    dec, reason = gate.verdict({"metric_value": val, "label": lab}, vr, sr, im)
    correct = (dec == expected)
    bytype[typ][0] += int(correct)
    bytype[typ][1] += 1
    if expected == "publish":
        TP += int(dec == "publish"); FN += int(dec == "quarantine")
    else:
        TN += int(dec == "quarantine"); FP += int(dec == "publish")

print("=== per case type (gate did the EXPECTED thing) ===")
for typ in ("TRUE", "fab_value", "invent_ref", "mispair_far", "mispair_row(#9)"):
    if typ in bytype:
        ok, tot = bytype[typ]
        exp = "publish" if typ == "TRUE" else "quarantine"
        print(f"  {typ:18} {ok}/{tot}  (expected {exp})")
n_false = TN + FP
print("\n=== confusion matrix ===")
print(f"TRUE  metrics: {TP} published (correct)   {FN} wrongly quarantined  -> false-quarantine {FN}/{TP+FN}")
print(f"FALSE metrics: {TN} caught (quarantined)  {FP} MISSED (published)   -> recall {TN}/{n_false} = {TN/n_false*100:.0f}%")
print(f"FALSE-PUBLISH (wrong ones that slipped through): {FP}/{n_false}")

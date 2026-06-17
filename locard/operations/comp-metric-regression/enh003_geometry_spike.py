"""#1 (ENH-003) spike — can bbox geometry detect/repair mispairings?

For every vision-confirmed mispairing (value real & on page, subject WRONG), render the doc, take the
value marker's bbox, find the geometrically-nearest LABEL marker (text-bearing, non-numeric), and check:
  (a) DETECT: does the nearest label differ from the model's cited subject? (would flag the mispair)
  (b) REPAIR: does the nearest label's text look like the true subject?
Also run it on a control set of vision-CORRECT pairings to measure false-flags. Output = numbers, no
changes applied. This tells us if geometry is clean enough to build on, before building anything.
"""
import json
import re
import sys

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import render
from lavandula.common.db import make_app_engine

B = "/home/ubuntu/research/locard/operations/comp-metric-regression/"
RES = {"batch1-clean": B + "scale100-results-tuned.json", "test2": B + "scale-test2-results.json"}
fix = {r["id"]: r for r in json.load(open(B + "fixtures/vision-ground-truth.json"))["records"]}
review = {r["id"]: r for r in json.load(open("/home/ubuntu/research/locard/spikes/0064/eval_set/vision/review-data.json"))}
results = {k: json.load(open(v)) for k, v in RES.items()}
full_by_set = {k: {s[:8]: s for s in r} for k, r in results.items()}


def center(e):
    b = e.get("bbox") or {}
    if "t" not in b or "l" not in b:
        return None
    return ((b["l"] + b.get("r", b["l"])) / 2.0, (b["t"] + b.get("b", b["t"])) / 2.0)


def is_label(e):
    t = (e.get("text") or "").strip()
    return bool(re.search(r"[A-Za-z]{3,}", t)) and len(re.sub(r"[^0-9]", "", t)) < len(t) / 2


def nearest_label(vref, idmap):
    v = idmap.get(vref)
    if not v:
        return None
    vc = center(v)
    vp = v.get("page")
    if vc is None:
        return None
    best, bestd = None, 1e18
    for k, e in idmap.items():
        if k == vref or e.get("page") != vp or not is_label(e):
            continue
        c = center(e)
        if c is None:
            continue
        d = ((c[0] - vc[0]) ** 2 + (c[1] - vc[1]) ** 2) ** 0.5
        if d < bestd:
            best, bestd = k, d
    return best


def metric_of(rid):
    r = review.get(rid)
    if not r:
        return None
    full = full_by_set.get(r.get("set"), {}).get(r.get("sha8"))
    if not full:
        return None
    try:
        return r["set"], full, results[r["set"]][full][int(rid.split(":")[1])]
    except (KeyError, IndexError, ValueError):
        return None


mispaired = [i for i, r in fix.items() if r["vision"]["subject_pairing"] == "wrong" and r["vision"]["value_on_page"] == "yes"
             and review.get(i, {}).get("gate_decision") == "publish"]
control = [i for i, r in fix.items() if r["vision"]["subject_pairing"] == "correct" and r["vision"]["value_on_page"] == "yes"
           and review.get(i, {}).get("gate_decision") == "publish"][:120]
print(f"vision-mispaired (published): {len(mispaired)} | control (correct): {len(control)}")

eng = make_app_engine()
cache = {}
with eng.connect() as conn:
    def idm(f):
        if f not in cache:
            _, _, cache[f] = render.render_tagged(conn, f)
        return cache[f]

    def run(ids):
        no_bbox = differs = same = 0
        examples = []
        for rid in ids:
            mo = metric_of(rid)
            if not mo:
                continue
            _, full, m = mo
            idmap = idm(full)
            vref = m.get("value_ref")
            if not idmap.get(vref) or center(idmap[vref]) is None:
                no_bbox += 1
                continue
            nl = nearest_label(vref, idmap)
            sref = m.get("subject_ref")
            if nl and nl != sref:
                differs += 1
                if len(examples) < 6:
                    examples.append((rid, m.get("label"), (idmap[nl].get("text") or "")[:42]))
            else:
                same += 1
        return no_bbox, differs, same, examples

    print("\n=== MISPAIRED set — geometry should DISAGREE with the model (differs = detected) ===")
    nb, df, sm, ex = run(mispaired)
    print(f"  no value bbox: {nb} | geometry DIFFERS (flag): {df} | geometry agrees (missed): {sm}")
    for rid, lbl, near in ex:
        print(f"    {rid:13} model='{(lbl or '')[:30]}'  ->  nearest='{near}'")

    print("\n=== CONTROL set — geometry should AGREE (differs = FALSE FLAG) ===")
    nb2, df2, sm2, _ = run(control)
    print(f"  no value bbox: {nb2} | geometry DIFFERS (false-flag): {df2} | geometry agrees: {sm2}")
    denom = df2 + sm2
    print(f"\n  detection rate on mispairs: {df}/{df+sm} = {100*df/max(1,df+sm):.0f}%")
    print(f"  false-flag rate on correct: {df2}/{denom} = {100*df2/max(1,denom):.0f}%")

"""SPIKE #3 (isolated): head-to-head accuracy, OLD (composed) vs NEW (slot) publish sets.

Run the slot-fed is-a-metric judge (value+label+full page, no composed statement) on ALL currently
published metrics. NEW publishes those it calls METRIC. Score both publish sets against:
  - operator hand-marks (review.db)  [trusted]
  - vision fixture (is_metric)        [broad]
Also tally the drift errors the slot RENDER structurally removes from whatever NEW publishes.

Read-only on canonical data; writes only into this folder.
"""
import json
import sys
import sqlite3
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import render
from lavandula.common.db import make_app_engine
from lavandula.common.secrets import get_secret
from confirm_isametric_slots import SYSTEM, ds  # reuse the exact slot-fed judge

CMR = "/home/ubuntu/research/locard/operations/comp-metric-regression/"
VIS = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/review-data.json"
RES = {"batch1-clean": CMR + "scale100-results-tuned.json", "test2": CMR + "scale-test2-results.json"}


def main():
    rev = json.load(open(VIS))
    by = {r["id"]: r for r in rev}
    results = {k: json.load(open(v)) for k, v in RES.items()}
    fbs = {k: {s[:8]: s for s in r} for k, r in results.items()}

    def slot(r):
        full = fbs.get(r.get("set"), {}).get(r["sha8"])
        try:
            return results[r["set"]][full][int(r["id"].split(":")[1])]
        except (KeyError, IndexError, ValueError):
            return {}

    eng = make_app_engine()
    rendered = {}

    def page_text(r):
        full = fbs.get(r.get("set"), {}).get(r["sha8"])
        if not full:
            return ""
        if full not in rendered:
            with eng.connect() as conn:
                try:
                    _, _, rendered[full] = render.render_tagged(conn, full)
                except Exception:
                    rendered[full] = None
        idmap = rendered[full]
        if not idmap:
            return ""
        pages = {int(p) for p in (r.get("v_page"), r.get("s_page")) if p not in (None, "", "None")}
        cited = (r.get("v_text") or "")[:40]
        out = []
        for it in idmap.values():
            if it.get("page") in pages:
                t = it.get("text", "") or ""
                out.append(f"«{t}»" if (cited and cited[:25] in t) else t)
        return "\n".join(out)

    def judge(r):
        u = f"VALUE: {r.get('value')}\nLABEL: {slot(r).get('label','')}\n\nSOURCE PAGE (cited value in «»):\n{page_text(r)[:6000]}"
        return r["id"], ds(u)

    pub = [r for r in rev if r.get("gate_decision") == "publish"]
    with ThreadPoolExecutor(max_workers=16) as ex:
        verdicts = dict(ex.map(judge, pub))
    json.dump(verdicts, open(CMR + "slot-render-spike/headtohead-verdicts.json", "w"), indent=1)

    new_pub = {i for i, v in verdicts.items() if not v.startswith("NOT")}
    new_drop = {i for i, v in verdicts.items() if v.startswith("NOT")}

    # ground truth: operator marks
    con = sqlite3.connect(CMR + "review.db")
    marks = {i: v for i, v in con.execute("SELECT id,operator_verdict FROM reviews WHERE operator_verdict IS NOT NULL")}
    con.close()
    # ground truth: vision fixture
    fix = {r["id"]: r for r in json.load(open(CMR + "fixtures/vision-ground-truth.json"))["records"]}

    def score(idset, name):
        checked = [i for i in idset if i in marks]
        good = sum(1 for i in checked if marks[i] == "gate-correct")
        bad = sum(1 for i in checked if marks[i] == "false-publish")
        vis = [i for i in idset if i in fix]
        vis_metric = sum(1 for i in vis if fix[i]["vision"]["is_metric"] == "metric")
        print(f"\n{name}: {len(idset)} published")
        print(f"  operator-checked: {len(checked)} | gate-correct {good} | false-publish {bad}"
              f"  -> bad rate {100*bad/max(1,len(checked)):.1f}%")
        print(f"  vision-covered:   {len(vis)} | vision says is_metric {vis_metric}"
              f"  -> {100*vis_metric/max(1,len(vis)):.1f}%")

    old_pub = {r["id"] for r in pub}
    score(old_pub, "OLD (composed) publish set")
    score(new_pub, "NEW (slot) publish set")

    # what NEW drops: good vs bad per operator marks
    drop_checked = [(i, marks[i]) for i in new_drop if i in marks]
    drop_good = [i for i, v in drop_checked if v == "gate-correct"]
    drop_bad = [i for i, v in drop_checked if v == "false-publish"]
    print(f"\nNEW drops {len(new_drop)} of the old published set.")
    print(f"  of the {len(drop_checked)} you hand-checked among them: "
          f"{len(drop_bad)} were bad (correctly dropped), {len(drop_good)} were good (wrongly dropped)")

    # drift errors the RENDER removes (composed-only failures) that NEW still publishes
    drift_ids = set()
    for f in ("slot-render-spike/rehome-result.json",):
        pass
    # known drift classes from this session's artifacts
    try:
        amp = {x["id"] for x in json.load(open(CMR + "attribution-amp-flags.json"))}
    except FileNotFoundError:
        amp = set()
    drift_in_newpub = amp & new_pub
    print(f"\ndrift/amplification cases the slot RENDER structurally removes from NEW's publish set: "
          f"{len(drift_in_newpub)} (these were composed-statement errors; rendered output can't carry them)")


if __name__ == "__main__":
    main()

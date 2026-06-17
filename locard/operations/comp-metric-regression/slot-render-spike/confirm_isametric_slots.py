"""SPIKE #2 CONFIRM (isolated): does the is-a-metric LLM judgment survive when fed
{value, label, full-source-page} instead of the composed statement?

Unified judge (the 4 prose reject classes + the small-int semantic check, merged) fed ONLY
value + label + the full cited PAGE text (cited locus marked «»). NO composed statement.

Measured on:
  - the 86 metrics is-a-metric actually quarantined  -> RECALL (should say NOT)
  - a control of operator-confirmed good published metrics -> FALSE-REJECT (should say METRIC)

Reads review-data.json + review.db + extraction outputs read-only; renders pages via render.
Writes only into this folder.
"""
import json
import re
import sys
import sqlite3
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import render
from lavandula.common.db import make_app_engine
from lavandula.common.secrets import get_secret

CMR = "/home/ubuntu/research/locard/operations/comp-metric-regression/"
VIS = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/review-data.json"
RES = {"batch1-clean": CMR + "scale100-results-tuned.json", "test2": CMR + "scale-test2-results.json"}
KEY = get_secret("lavandula/deepseek/api_key")

SYSTEM = (
    "Decide whether a nonprofit-report data point is a METRIC of the organization's own work. "
    "You are given a VALUE, its LABEL, and the SOURCE PAGE it came from (cited spot marked «»). "
    "There is no pre-written sentence — judge from the value, label, and page.\n"
    "NOT a metric if the VALUE is any of:\n"
    "  TENURE/DURATION — years of operation/service/history, an anniversary, or one person's personal "
    "episode length ('45 years of service', '30th anniversary', 'stayed 60 days').\n"
    "  YEAR-AS-VALUE — the value is a calendar year ('incorporated in 1989' -> 1989).\n"
    "  FORECAST — a future goal/projection not yet achieved ('$15M goal', 'will serve 7,200', 'could "
    "raise $500k'). A goal that WAS met is a METRIC.\n"
    "  POPULATION FACTOID — measures society/the general population, not this org ('1 in 3 women "
    "experience...', '27% of US adults...').\n"
    "  NON-QUANTITY SMALL NUMBER — a bare 1/2 that only means one event happened or one thing exists "
    "('passed a bill'=1, 'the only shelter'=1), a multiplier ('doubled'=2), a rank, or an identifier.\n"
    "METRIC = the org's own counted/measured quantity: people/clients served, dollars raised/spent, "
    "percent improved, hours, facilities/vehicles/counties (even small: '1 client', '3 counties').\n"
    "When in doubt, reply METRIC.\nReply ONE word: METRIC or NOT."
)


def ds(user):
    body = json.dumps({"model": "deepseek-chat", "temperature": 0.0, "max_tokens": 6,
                       "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}).encode()
    for _ in range(3):
        try:
            req = urllib.request.Request("https://api.deepseek.com/v1/chat/completions", data=body,
                                         headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(req, timeout=60).read())["choices"][0]["message"]["content"].strip().upper()
        except Exception:
            pass
    return "ERR"


def main():
    rev = json.load(open(VIS))
    by = {r["id"]: r for r in rev}
    results = {k: json.load(open(v)) for k, v in RES.items()}
    fbs = {k: {s[:8]: s for s in r} for k, r in results.items()}

    def label_of(r):
        full = fbs.get(r.get("set"), {}).get(r["sha8"])
        try:
            return results[r["set"]][full][int(r["id"].split(":")[1])].get("label") or ""
        except (KeyError, IndexError, ValueError):
            return ""

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
            if it.get("page") not in pages:
                continue
            t = it.get("text", "") or ""
            if cited and cited[:25] in t:
                t = f"«{t}»"
            out.append(t)
        return "\n".join(out)

    def judge(r):
        u = f"VALUE: {r.get('value')}\nLABEL: {label_of(r)}\n\nSOURCE PAGE (cited value in «»):\n{page_text(r)[:6000]}"
        return r["id"], ds(u)

    # recall set: the 86 is-a-metric rejects
    rejects = [r for r in rev if r.get("gate_reason") in ("not_a_metric_rule", "not_a_metric")]
    # control set: operator-confirmed good published metrics
    con = sqlite3.connect(CMR + "review.db")
    good_ids = [i for (i,) in con.execute(
        "SELECT id FROM reviews WHERE operator_verdict='gate-correct'")]
    con.close()
    controls = [by[i] for i in good_ids if i in by and by[i].get("gate_decision") == "publish"][:100]

    with ThreadPoolExecutor(max_workers=16) as ex:
        rej_v = dict(ex.map(judge, rejects))
        con_v = dict(ex.map(judge, controls))

    rec = sum(1 for v in rej_v.values() if v.startswith("NOT"))
    fr = sum(1 for v in con_v.values() if v.startswith("NOT"))
    print(f"RECALL on {len(rejects)} known rejects: judge says NOT for {rec} ({100*rec/len(rejects):.0f}%)")
    print(f"FALSE-REJECT on {len(controls)} operator-confirmed good: judge says NOT for {fr} ({100*fr/len(controls):.0f}%)")

    # by mechanism
    for mech in ("item-1 rules", "is-a-metric classifier"):
        sub = [r for r in rejects if r.get("decided_by") == mech]
        c = sum(1 for r in sub if rej_v.get(r["id"], "").startswith("NOT"))
        print(f"   {mech}: {c}/{len(sub)} re-caught ({100*c/max(1,len(sub)):.0f}%)")

    print("\nmissed rejects (judge said METRIC — would re-publish):")
    for r in rejects:
        if not rej_v.get(r["id"], "").startswith("NOT"):
            print(f"  {r['id']:14} v={str(r.get('value'))[:6]:6} {(r.get('statement') or '')[:66]}")
    print("\nfalse rejects (judge killed an operator-good metric):")
    for r in controls:
        if con_v.get(r["id"], "").startswith("NOT"):
            print(f"  {r['id']:14} v={str(r.get('value'))[:6]:6} {(r.get('statement') or '')[:66]}")

    json.dump({"reject_verdicts": rej_v, "control_verdicts": con_v},
              open(CMR + "slot-render-spike/confirm-result.json", "w"), indent=1)


if __name__ == "__main__":
    main()

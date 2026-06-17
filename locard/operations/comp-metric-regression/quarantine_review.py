"""Selection method for gate quarantines → labeled data to tune the gate.

For every quarantined metric across the baseline result sets, scan the doc's located
items for one that ACTUALLY contains the value (gate number-matching), and whether the
subject words co-occur there. That tells us, per quarantine, whether it's a likely
FALSE quarantine (value real + subject co-located, grounding just cited the wrong
marker) or correctly held (value derived/absent). Ordered worst-first for fast review.

Outputs: quarantine-review.html (served at /p20/) + gate-eval-candidates.json (manifest
with stable ids sha8:idx so labels map back). NO fix applied — this is data gathering.

    python quarantine_review.py [out_html]   # results sets are fixed below
"""
import html
import json
import re
import sys

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import gate
import render
from lavandula.common.db import make_app_engine
from sqlalchemy import text

VDIR = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision"
OUTNAME = sys.argv[1] if len(sys.argv) > 1 else "quarantine-review.html"
SETS = [
    ("batch1-clean", "locard/operations/comp-metric-regression/scale100-results-tuned.json", "/tmp/baseline-100.json"),
    ("test2", "locard/operations/comp-metric-regression/scale-test2-results.json", "/tmp/test2-100.json"),
]
DISCARD = {"financial_report", "not_relevant"}


def words(s):
    return set(re.findall(r"[a-z]{4,}", (s or "").lower()))


def find_value_locations(value, idmap):
    """Items whose text actually expresses the value (gate matching). Returns list of dicts."""
    v = gate.to_float(value)
    if v is None:
        return []
    tol = max(1.0, 0.001 * abs(v))
    hits = []
    for k, it in idmap.items():
        txt = it.get("text") or ""
        if any(abs(c - v) <= tol for c in gate.candidate_numbers(txt)):
            hits.append({"id": k, "text": txt, "page": it.get("page"), "kind": it.get("kind")})
    return hits


eng = make_app_engine()
items = []
with eng.connect() as conn:
    # restrict batch1 to filter-survivors
    mt = {}
    allshas = []
    for _, _, dj in SETS:
        allshas += [d["sha"] for d in json.load(open(dj))["docs"]]
    for r in conn.execute(text("SELECT content_sha256, material_type FROM lava_corpus.corpus WHERE content_sha256=ANY(:s)"),
                          {"s": list(set(allshas))}).fetchall():
        mt[r[0]] = r[1]
    names = {r[0]: r[1] for r in conn.execute(text(
        "SELECT d.content_sha256, ns.name FROM lava_parse.documents d "
        "LEFT JOIN lava_corpus.nonprofits_seed ns ON ns.ein=d.source_org_ein WHERE d.content_sha256=ANY(:s)"),
        {"s": list(set(allshas))}).fetchall()}
    for setname, rj, dj in SETS:
        res = json.load(open(rj))
        shas = [d["sha"] for d in json.load(open(dj))["docs"] if mt.get(d["sha"]) not in DISCARD]
        for sha in shas:
            recs = res.get(sha)
            if not recs:
                continue
            quar = [(i, r) for i, r in enumerate(recs) if r.get("decision") == "quarantine"]
            if not quar:
                continue
            _, _, idmap = render.render_tagged(conn, sha)
            for i, r in quar:
                reason = r.get("reason")
                locs = find_value_locations(r.get("value"), idmap) if reason in ("value_not_at_marker", "subject_not_grounded") else []
                subj_w = words(r.get("label"))
                loc_with_subj = [L for L in locs if subj_w & words(L["text"])]
                if reason == "value_not_at_marker" and loc_with_subj:
                    sig, rank = "FALSE-QUARANTINE? value+subject co-occur elsewhere", 0
                elif reason == "value_not_at_marker" and locs:
                    sig, rank = "value present elsewhere (subject pairing unclear)", 1
                elif reason == "value_not_at_marker":
                    sig, rank = "value NOT in doc — derived/computed (likely correct hold)", 3
                elif reason == "subject_not_grounded":
                    sig, rank = ("value present; subject pairing to check" if locs else "value+subject both unclear"), 2
                else:
                    sig, rank = "no numeric value — qualitative (likely correct hold)", 4
                vref = idmap.get(r.get("value_ref")) or {}
                sref = idmap.get(r.get("subject_ref")) or {}
                items.append({
                    "id": f"{sha[:8]}:{i}", "set": setname, "sha8": sha[:8],
                    "org": str(names.get(sha) or "?"), "reason": reason, "rank": rank, "signal": sig,
                    "value": r.get("value"), "subject": r.get("label"), "statement": r.get("statement"),
                    "v_id": r.get("value_ref"), "v_page": vref.get("page"), "v_text": (vref.get("text") or "")[:200],
                    "s_id": r.get("subject_ref"), "s_page": sref.get("page"), "s_text": (sref.get("text") or "")[:200],
                    "true_locations": [{"page": L["page"], "kind": L["kind"], "text": L["text"][:200]} for L in (loc_with_subj or locs)[:3]],
                })

items.sort(key=lambda x: (x["rank"], x["set"]))
json.dump(items, open("locard/operations/comp-metric-regression/gate-eval-candidates.json", "w"), indent=1)

import collections
bysig = collections.Counter(x["signal"] for x in items)
RANKC = {0: "#c0392b", 1: "#e67e22", 2: "#e67e22", 3: "#0a8a0a", 4: "#888"}
cards = []
for n, x in enumerate(items, 1):
    locs_html = "".join(
        f'<div class=loc>↳ <b>found value</b> @ page {L["page"]} [{L["kind"]}]: {html.escape(L["text"])}</div>'
        for L in x["true_locations"]) or '<div class=loc style="color:#0a8a0a">↳ value not found anywhere in doc (derived/computed)</div>'
    vloc = f"page {x['v_page']}" if x["v_page"] else "page ?"
    sloc = f"page {x['s_page']}" if x["s_page"] else "page ?"
    cards.append(f"""
  <div class=card id="{x['id']}">
    <div class=hd><b>Q{n}</b> <span class=qid>{x['id']}</span> · {html.escape(x['org'])[:40]}
      <span class=reason>{x['reason']}</span></div>
    <div class=sig style="color:{RANKC[x['rank']]}">{html.escape(x['signal'])}</div>
    <div class=metric>"{html.escape(str(x['statement']))}"<br>
      <span class=kv>value=<b>{html.escape(str(x['value']))}</b> · subject=<b>{html.escape(str(x['subject']))}</b></span></div>
    <div class=cited><b>model cited VALUE</b> @ <b>{vloc}</b> ({html.escape(str(x['v_id']))}) → {html.escape(x['v_text']) or '<i>(marker not found / invented)</i>'}</div>
    <div class=cited><b>model cited SUBJECT</b> @ <b>{sloc}</b> ({html.escape(str(x['s_id']))}) → {html.escape(x['s_text']) or '<i>(marker not found / invented)</i>'}</div>
    {locs_html}
  </div>""")

summary = "".join(f"<li>{v} — {html.escape(k)}</li>" for k, v in bysig.most_common())
doc = f"""<!doctype html><meta charset=utf-8><title>Quarantine review — gate training data</title>
<style>
body{{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;color:#1a1a1a}}
.wrap{{max-width:1040px;margin:0 auto;padding:24px}}
h1{{font-size:20px;margin:0 0 6px}} .sub{{color:#555}} ul{{font-size:13px}}
.card{{border:1px solid #ddd;border-radius:8px;padding:11px 14px;margin:10px 0;background:#fafafa}}
.hd{{font-size:13px;color:#444}} .qid{{font:11px ui-monospace,monospace;color:#aaa}}
.reason{{float:right;font-size:11px;color:#c0392b;font-weight:600}}
.sig{{font-size:12px;font-weight:600;margin:3px 0}}
.metric{{font-size:15px;margin:5px 0}} .kv{{font-size:12px;color:#555}}
.cited{{font:12px ui-monospace,Menlo,monospace;color:#777;background:#fff;border:1px solid #eee;padding:5px 7px;margin:4px 0}}
.loc{{font:12px ui-monospace,Menlo,monospace;color:#1a5e1a;background:#f1f8f1;border:1px solid #d8ecd8;padding:5px 7px;margin:3px 0}}
</style>
<div class=wrap>
<h1>Quarantine review — labeled data to tune the gate</h1>
<div class=sub>{len(items)} quarantined metrics across the 166-doc baseline, ordered worst-first. For each:
the metric, <b>where the model said the value is</b> and <b>where it said the subject is</b> (with page + marker id),
and <b>where the value actually appears</b> in the doc (green). Compare the cited value-page to the green pages —
when they differ but the value is real, the grounding cited the wrong marker.</div>
<ul>{summary}</ul>
<p style="font-size:13px;color:#555">To label: tell me the <b>Q#</b> (or id) of each one where the model was correct
(false quarantine) vs correctly held — I'll record them into the gate-eval set.</p>
{''.join(cards)}
</div>"""
open(f"{VDIR}/{OUTNAME}", "w").write(doc)
print(f"quarantines={len(items)}")
for k, v in bysig.most_common():
    print(f"  {v:4d}  {k}")
print(f"\nmanifest: gate-eval-candidates.json")
print(f"URL:  https://cloud2.lavandulagroup.com/p20/{OUTNAME}")

"""Per-metric table of what the location check confirmed: value + subject, each
with its cited location text and confirmed Y/N. (Only value+subject are grounded
in this version — unit/temporal/direction are not separately located.)"""
import html as H
import json
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

eng = make_app_engine()
idmaps, rows = {}, []
with eng.connect() as c:
    for i, p in enumerate(packets, 1):
        sha = full[p["sha8"]]
        if sha not in idmaps:
            idmaps[sha] = render.render_tagged(c, sha)[2]
        im = idmaps[sha]
        rec = lut.get((p["sha8"], p["statement"]), {})
        vr, srf = rec.get("value_ref"), rec.get("subject_ref")
        vtext = (im.get(vr) or {}).get("text", "")
        stext = (im.get(srf) or {}).get("text", "")
        rows.append(dict(i=i, sha8=p["sha8"], value=p["value"], vr=vr, vtext=vtext,
                         vok=bool(gate.value_grounded(p["value"], vtext)),
                         label=p["label"], sr=srf, stext=stext,
                         sok=bool(gate.subject_grounded(p["label"], srf, vr, im))))

# ---- inline ----
print(f"{'#':>2} {'VALUE':>11} {'v?':>2}  {'@ location (value)':<40}  {'s?':>2}  SUBJECT / @location")
print("-" * 120)
for r in rows:
    print(f"{r['i']:>2} {str(r['value'])[:11]:>11} {'Y' if r['vok'] else 'N':>2}  "
          f"{str(r['vtext'])[:40]:<40}  {'Y' if r['sok'] else 'N':>2}  "
          f"{str(r['label'])[:20]} :: {str(r['stext'])[:28]}")
print("-" * 120)
print(f"VALUE grounded to location: {sum(r['vok'] for r in rows)}/{len(rows)}    "
      f"SUBJECT grounded to location: {sum(r['sok'] for r in rows)}/{len(rows)}")
print("NOTE: only value + subject are grounded in this version; unit/temporal/direction are NOT separately located.")

# ---- HTML ----
def td(x, ok=None):
    c = "" if ok is None else (' style="color:#0a0;font-weight:700"' if ok else ' style="color:#c00;font-weight:700"')
    return f"<td{c}>{H.escape(str(x))}</td>"


trs = "".join(
    f"<tr><td>{r['i']}</td><td class=val>{H.escape(str(r['value']))}</td>"
    f"{td('✓' if r['vok'] else '✗', r['vok'])}<td class=loc>{H.escape(str(r['vtext'])[:120])}</td>"
    f"<td>{H.escape(str(r['label']))}</td>"
    f"{td('✓' if r['sok'] else '✗', r['sok'])}<td class=loc>{H.escape(str(r['stext'])[:120])}</td></tr>"
    for r in rows)
html_doc = f"""<!doctype html><meta charset=utf-8><meta http-equiv="Cache-Control" content="no-store">
<title>Per-metric datapoints grounded to location</title>
<style>body{{font:13px/1.4 -apple-system,Segoe UI,sans-serif;margin:20px}}
h1{{font-size:18px}} .sub{{color:#555;margin-bottom:12px}}
table{{border-collapse:collapse;width:100%}} td,th{{border:1px solid #ddd;padding:5px 7px;vertical-align:top;text-align:left}}
th{{background:#f4f4f4}} .val{{font-weight:700;white-space:nowrap}} .loc{{font:11px ui-monospace,Menlo,monospace;color:#555}}</style>
<h1>What the location check confirmed — per metric</h1>
<div class=sub>Each metric's <b>value</b> and <b>subject</b> with the exact text it was confirmed at.
✓ = present at the cited location. <b>Only value + subject are grounded in this version</b> — unit/temporal/direction are not separately located yet.</div>
<table><tr><th>#</th><th>value</th><th>val?</th><th>value @ cited location</th><th>subject (label)</th><th>subj?</th><th>subject @ cited location</th></tr>
{trs}</table>
<p>VALUE grounded {sum(r['vok'] for r in rows)}/{len(rows)} &nbsp; SUBJECT grounded {sum(r['sok'] for r in rows)}/{len(rows)}</p>"""
open("/home/ubuntu/research/locard/spikes/0064/eval_set/vision/gold-datapoints.html", "w").write(html_doc)
print("\nHTML: https://cloud2.lavandulagroup.com/p20/gold-datapoints.html")

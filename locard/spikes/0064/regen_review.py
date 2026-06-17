"""Regenerate eval_set/review.html from the build outputs (eval_manifest.csv +
img/*.png) WITHOUT re-parsing. Adds a full instructions panel + class legend so
the human labeler knows exactly what to do."""
import base64
import csv
import io
import os
import sys
sys.path.insert(0, ".")
from PIL import Image

OUT = "eval_set"
CLASSES = ["kpi_clean", "kpi_dense", "prose", "financial_table", "garble",
           "cover_divider", "mixed"]
LANE_ORDER = {"APPLY_regrouping": 0, "LOG_only(medium)": 1,
              "normal_pipeline": 2, "normal(garbled)": 3}

rows = list(csv.DictReader(open(f"{OUT}/eval_manifest.csv")))
for r in rows:
    p = f"{OUT}/{r['img']}"
    im = Image.open(p).convert("RGB"); im.thumbnail((360, 480))
    buf = io.BytesIO(); im.save(buf, format="JPEG", quality=70)
    r["b64"] = base64.b64encode(buf.getvalue()).decode()
rows.sort(key=lambda x: (LANE_ORDER.get(x["pred_lane"], 9), x["doc"], int(x["page"])))
counts = {}
for r in rows:
    counts[r["pred_lane"]] = counts.get(r["pred_lane"], 0) + 1

LEGEND = """
<div id='help'>
<h3>What you're doing</h3>
<p>Each card is one page from a sample of reports. The detector <b>guessed</b> whether
the page is a KPI infographic that needs visual regrouping. Tell us the <b>truth</b> for
each page (two dropdowns) so we can measure how often the detector is right.</p>
<h3>1) class — what kind of page is it?</h3>
<ul>
<li><b>kpi_clean</b> — designed metric cards in clean, <i>separated</i> groups/columns; each
label sits next to its number(s). The fix WORKS here. <i>(reference: 001eb5f8 p13)</i></li>
<li><b>kpi_dense</b> — a KPI/infographic page but tightly packed / overlapping / chaotic;
cards are NOT cleanly separated. The fix would scramble it. <i>(ref: 61d4b4bb p1)</i></li>
<li><b>prose</b> — mostly narrative paragraphs (a single callout stat box is fine).
<i>(ref: 001eb5f8 p7, p14)</i></li>
<li><b>financial_table</b> — financial statements / data tables (revenue, expenses, grids).
<i>(ref: 001eb5f8 p20)</i></li>
<li><b>garble</b> — the text is scrambled / mojibake / unreadable. <i>(ref: 7ce2c7fd p4)</i></li>
<li><b>cover_divider</b> — cover, section divider, photo page, donor/sponsor list; little/no
metric content.</li>
<li><b>mixed</b> — a genuine mix of substantial prose AND KPI cards on one page.</li>
</ul>
<h3>2) regroup? — should the regrouping fix run on this page?</h3>
<p><b>yes</b> only for <b>kpi_clean</b> (cleanly separable cards) — that's the shape the fix
is built for. <b>no</b> for everything else (prose, financial tables, garble, covers, AND
dense/chaotic KPI where regrouping would scramble). Rule of thumb: <b>regroup=yes ⇔ class=kpi_clean</b>.</p>
<h3>Where to focus</h3>
<ol>
<li><b>APPLY_regrouping</b> section first — the detector wants to run the fix there, so a wrong
call (prose/financial) is the <b>expensive</b> error. Label all of these.</li>
<li><b>LOG_only</b> next — did it MISS a clean KPI page (regroup should've been yes)?</li>
<li><b>normal / garbled</b> — spot-check a few for missed KPI; you needn't do them all.</li>
</ol>
<h3>When done</h3>
<p>Click <b>⬇ Download labels (CSV)</b> at the top and send me the file (or just tell me the
corrections). Seed pages are pre-filled as examples — change them if you disagree. You do
NOT have to label every page; the APPLY and LOG lanes are what matter most.</p>
</div>"""

cards, cur = [], None
LANE_NOTE = {"APPLY_regrouping": "REVIEW ALL — false positives here are the expensive error",
             "LOG_only(medium)": "did it miss a clean KPI page? (false negatives)",
             "normal_pipeline": "spot-check for missed KPI pages",
             "normal(garbled)": "garble guard — confirm these are glyph-soup"}
for r in rows:
    if r["pred_lane"] != cur:
        cur = r["pred_lane"]
        cards.append(f"<h2 class='lane'>{cur} <small>({counts[cur]}) — {LANE_NOTE.get(cur,'')}</small></h2><div class='grid'>")
    seed = r.get("seed_class", "")
    seed_rg = ("yes" if seed == "kpi_clean" else ("no" if seed else ""))
    cls = "".join(f"<option value='{c}' {'selected' if c==seed else ''}>{c or '— pick —'}</option>" for c in [""] + CLASSES)
    rg = "".join(f"<option value='{v}' {'selected' if v==seed_rg else ''}>{v or '?'}</option>" for v in ["", "yes", "no"])
    cards.append(f"""<div class='card' data-doc='{r['doc']}' data-page='{r['page']}' data-pred='{r['pred_lane']}'>
<a href='{r['img']}' target='_blank'><img src='data:image/jpeg;base64,{r['b64']}'></a>
<div class='meta'><b>{r['doc']} p{r['page']}</b> · sc {r['score']} · wl {r['whitelist'][0]} · metrics {r['n_metrics']} · cards {r['n_cards']} {'· SEED:'+seed if seed else ''}<br><span class='rc'>{r['reasons']}</span></div>
<label>class <select class='cls'>{cls}</select></label> <label>regroup? <select class='rg'>{rg}</select></label></div>""")
    nxt = rows[rows.index(r) + 1] if rows.index(r) + 1 < len(rows) else None
    if nxt is None or nxt["pred_lane"] != cur:
        cards.append("</div>")

html = f"""<!doctype html><html><head><meta charset='utf-8'><title>0064 KPI detector — eval labeling</title><style>
body{{font-family:system-ui,Arial;margin:0;background:#fafafa}}
#bar{{position:sticky;top:0;background:#fff;padding:.5rem 1rem;border-bottom:2px solid #1e293b;z-index:10}}
#help{{background:#fffbe6;border:1px solid #fde68a;margin:1rem;padding:.5rem 1rem;border-radius:6px;font-size:13px;max-width:1100px}}
#help h3{{margin:.6rem 0 .2rem;font-size:13px;color:#92400e}} #help ul,#help ol{{margin:.2rem 0;padding-left:1.2rem}}
h2.lane{{margin:1.2rem 1rem .4rem;padding:.3rem .6rem;background:#1e293b;color:#fff;border-radius:4px}}
h2.lane small{{font-weight:400;color:#cbd5e1}}
.grid{{display:flex;flex-wrap:wrap;gap:10px;padding:0 1rem}}
.card{{width:300px;border:1px solid #e2e8f0;border-radius:6px;background:#fff;padding:6px}}
.card img{{width:100%;border:1px solid #eee;cursor:zoom-in}}
.meta{{font-size:11px;color:#334155;margin:4px 0}} .rc{{color:#94a3b8;font-size:10px}}
select{{font-size:12px}} label{{font-size:11px;margin-right:8px}}
button{{font-size:14px;padding:.4rem .8rem;background:#2563eb;color:#fff;border:0;border-radius:4px;cursor:pointer}}
</style></head><body>
<div id='bar'><b>Spec 0064 — KPI detector eval labeling</b>
&nbsp;<button onclick='dl()'>⬇ Download labels (CSV)</button>
&nbsp;<button onclick="document.getElementById('help').style.display=document.getElementById('help').style.display=='none'?'block':'none'">toggle instructions</button>
<span id='prog'></span></div>
{LEGEND}
{''.join(cards)}
<script>
function dl(){{let o=[['doc','page','pred_lane','true_class','true_regroup']];
document.querySelectorAll('.card').forEach(c=>o.push([c.dataset.doc,c.dataset.page,c.dataset.pred,c.querySelector('.cls').value,c.querySelector('.rg').value]));
let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([o.map(r=>r.join(',')).join('\\n')],{{type:'text/csv'}}));a.download='eval_labels.csv';a.click();}}
function prog(){{let n=document.querySelectorAll('.card').length,d=[...document.querySelectorAll('.card .cls')].filter(s=>s.value).length;document.getElementById('prog').textContent=` — ${{d}}/${{n}} labeled`;}}
document.addEventListener('change',prog);prog();
</script></body></html>"""
open(f"{OUT}/review.html", "w").write(html)
print(f"regenerated {OUT}/review.html with instructions — {len(rows)} pages, lanes {counts}")

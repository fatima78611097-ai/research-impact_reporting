"""0064 — build the labeled eval set for the KPI detector.

Parses a stratified sample of docs (all PDFs in pdfs/), renders every page,
runs the detector per page, and emits:
  eval_set/img/<doc8>_p<NN>.png       full-res page renders
  eval_set/eval_manifest.csv          one row/page: prediction + blank gold cols
  eval_set/review.html                self-contained gallery (thumbnails grouped
                                       by predicted lane) with per-page label
                                       dropdowns + a 'download labels' button.
Ground truth is supplied by a HUMAN in review.html (per the project principle
that LLM visual judgement is not trustworthy as ground truth). Seed labels for
the pages already verified by eye are pre-filled as references.
"""
import base64
import csv
import glob
import io
import os
import sys
sys.path.insert(0, ".")
import pypdfium2 as pdfium
from grouping_lib import load_doc, page_cells
from kpi_detector import classify_page

OUT = "eval_set"
os.makedirs(f"{OUT}/img", exist_ok=True)

# pages already verified by eye (seed references; doc8,page -> class)
SEED = {
    ("001eb5f8", 13): "kpi_clean", ("001eb5f8", 7): "prose",
    ("001eb5f8", 14): "prose", ("001eb5f8", 20): "financial_table",
    ("001eb5f8", 16): "financial_table", ("61d4b4bb", 1): "kpi_dense",
    ("7ce2c7fd", 4): "garble",
}
CLASSES = ["kpi_clean", "kpi_dense", "prose", "financial_table", "garble",
           "cover_divider", "mixed"]
LANE_ORDER = {"APPLY_regrouping": 0, "LOG_only(medium)": 1,
              "normal_pipeline": 2, "normal(garbled)": 3}

rows = []
for pdf in sorted(glob.glob("pdfs/*.pdf")):
    sha8 = os.path.basename(pdf)[:8]
    print(f"processing {sha8} ...", flush=True)
    doc, n = load_doc(pdf)
    rend = pdfium.PdfDocument(pdf)
    for p in range(1, n + 1):
        seg = doc.get_page(p)
        cells = page_cells(seg)
        r = (classify_page(cells, seg.dimension.width, seg.dimension.height)
             if cells else {"lane": "normal_pipeline", "score": 0, "whitelist": False,
                            "garbled": False, "n_metrics": 0, "n_cards": 0,
                            "head_adj": 0, "table_like": False, "reason_codes": []})
        img = rend[p - 1].render(scale=1.3).to_pil()
        img.convert("RGB").save(f"{OUT}/img/{sha8}_p{p:02d}.png")
        thumb = img.convert("RGB")
        thumb.thumbnail((360, 480))
        buf = io.BytesIO(); thumb.save(buf, format="JPEG", quality=70)
        b64 = base64.b64encode(buf.getvalue()).decode()
        rows.append({
            "doc": sha8, "page": p, "pred_lane": r["lane"], "score": r["score"],
            "whitelist": r["whitelist"], "garbled": r["garbled"],
            "n_metrics": r["n_metrics"], "n_cards": r["n_cards"],
            "head_adj": r["head_adj"], "table_like": r["table_like"],
            "reasons": ";".join(r["reason_codes"]),
            "seed_class": SEED.get((sha8, p), ""),
            "img": f"img/{sha8}_p{p:02d}.png", "b64": b64,
        })

rows.sort(key=lambda x: (LANE_ORDER.get(x["pred_lane"], 9), x["doc"], x["page"]))

# ---- CSV (no b64) ----
with open(f"{OUT}/eval_manifest.csv", "w", newline="") as f:
    cols = ["doc", "page", "pred_lane", "score", "whitelist", "garbled",
            "n_metrics", "n_cards", "head_adj", "table_like", "reasons",
            "seed_class", "true_class", "true_regroup", "notes", "img"]
    w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({**r, "true_class": r["seed_class"], "true_regroup": "", "notes": ""})

# ---- review.html ----
counts = {}
for r in rows:
    counts[r["pred_lane"]] = counts.get(r["pred_lane"], 0) + 1
opts = "".join(f"<option value='{c}'>{c}</option>" for c in [""] + CLASSES)
cards = []
cur_lane = None
for r in rows:
    if r["pred_lane"] != cur_lane:
        cur_lane = r["pred_lane"]
        note = {"APPLY_regrouping": "REVIEW ALL — false positives here are the expensive error",
                "LOG_only(medium)": "monitoring lane — check for missed KPI (false negatives)",
                "normal_pipeline": "spot-check for missed KPI pages",
                "normal(garbled)": "garble guard — confirm these are glyph-soup"}.get(cur_lane, "")
        cards.append(f"<h2 class='lane'>{cur_lane} &nbsp;<small>({counts[cur_lane]}) — {note}</small></h2>")
    seed = r["seed_class"]
    sel = "".join(f"<option value='{c}' {'selected' if c==seed else ''}>{c or '— pick —'}</option>"
                  for c in [""] + CLASSES)
    cards.append(f"""
<div class='card' data-doc='{r['doc']}' data-page='{r['page']}' data-pred='{r['pred_lane']}'>
  <a href='{r['img']}' target='_blank'><img src='data:image/jpeg;base64,{r['b64']}'></a>
  <div class='meta'><b>{r['doc']} p{r['page']}</b> · score {r['score']} · wl {str(r['whitelist'])[0]}
   · metrics {r['n_metrics']} · cards {r['n_cards']} · hAdj {r['head_adj']}
   {'· SEED:'+seed if seed else ''}<br><span class='rc'>{r['reasons']}</span></div>
  <label>class <select class='cls'>{sel}</select></label>
  <label>regroup? <select class='rg'><option value=''>?</option><option>yes</option><option>no</option></select></label>
</div>""")

html = f"""<!doctype html><html><head><meta charset='utf-8'><title>0064 KPI detector — eval labeling</title>
<style>
body{{font-family:system-ui,Arial;margin:1rem;background:#fafafa}}
h1{{font-size:1.2rem}} h2.lane{{margin:1.5rem 0 .5rem;padding:.3rem .5rem;background:#1e293b;color:#fff;border-radius:4px}}
h2.lane small{{font-weight:400;color:#cbd5e1}}
.grid{{display:flex;flex-wrap:wrap;gap:10px}}
.card{{width:300px;border:1px solid #e2e8f0;border-radius:6px;background:#fff;padding:6px}}
.card img{{width:100%;border:1px solid #eee;cursor:zoom-in}}
.meta{{font-size:11px;color:#334155;margin:4px 0}} .rc{{color:#94a3b8;font-size:10px}}
select{{font-size:12px}} label{{font-size:11px;margin-right:8px}}
#bar{{position:sticky;top:0;background:#fff;padding:.5rem;border-bottom:2px solid #1e293b;z-index:10}}
button{{font-size:14px;padding:.4rem .8rem;background:#2563eb;color:#fff;border:0;border-radius:4px;cursor:pointer}}
</style></head><body>
<div id='bar'><h1>Spec 0064 — KPI detector eval labeling &nbsp;
<button onclick='dl()'>⬇ Download labels (CSV)</button>
<span id='prog'></span></h1>
<small>For each page set the true <b>class</b> and whether regrouping should be applied (<b>regroup?</b>).
APPLY lane first — those are the precision-critical ones. Click an image to zoom. Seed pages are pre-filled.</small></div>
<div class='grid'>{''.join(cards)}</div>
<script>
function dl(){{
  let out=[['doc','page','pred_lane','true_class','true_regroup']];
  document.querySelectorAll('.card').forEach(c=>{{
    out.push([c.dataset.doc,c.dataset.page,c.dataset.pred,
      c.querySelector('.cls').value, c.querySelector('.rg').value]);
  }});
  let csv=out.map(r=>r.join(',')).join('\\n');
  let a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob([csv],{{type:'text/csv'}}));
  a.download='eval_labels.csv'; a.click();
}}
function prog(){{
  let n=document.querySelectorAll('.card').length;
  let d=[...document.querySelectorAll('.card .cls')].filter(s=>s.value).length;
  document.getElementById('prog').textContent=` — ${{d}}/${{n}} labeled`;
}}
document.addEventListener('change',prog); prog();
</script></body></html>"""
with open(f"{OUT}/review.html", "w") as f:
    f.write(html)

print(f"\nDONE: {len(rows)} pages across {len(set(r['doc'] for r in rows))} docs")
print("lane counts:", counts)
print(f"-> {OUT}/review.html  +  {OUT}/eval_manifest.csv  +  {OUT}/img/")

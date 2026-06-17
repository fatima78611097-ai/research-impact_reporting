"""0064 — render the gate's ACCEPTED pages + their recovered/enriched metrics into a
review gallery so the operator can confirm each recovery is real (not scramble).
Reads eval_set/gate_run_v2.csv; emits eval_set/accept_review.html (self-contained)."""
import base64
import csv
import glob
import io
import os
import sys
import pypdfium2 as pdfium

OUT = "eval_set"
PDF = {os.path.basename(p)[:8]: p for p in glob.glob("pdfs/*.pdf")}
rows = [r for r in csv.DictReader(open(f"{OUT}/gate_run_v2.csv")) if r["decision"] == "ACCEPT"]
print(f"{len(rows)} accepted pages")

cards = []
_doccache = {}
for r in rows:
    doc8, page = r["doc"], int(r["page"])
    if doc8 not in _doccache:
        _doccache[doc8] = pdfium.PdfDocument(PDF[doc8])
    pil = _doccache[doc8][page - 1].render(scale=1.6).to_pil().convert("RGB")
    pil.thumbnail((620, 820))
    buf = io.BytesIO(); pil.save(buf, format="JPEG", quality=72)
    b64 = base64.b64encode(buf.getvalue()).decode()
    rec = [x.strip() for x in (r.get("rec_ex") or "").split("|") if x.strip()]
    enr = [x.strip() for x in (r.get("enr_ex") or "").split("|") if x.strip()]
    items = "".join(f"<li class='rec'>{x}</li>" for x in rec) + \
            "".join(f"<li class='enr'>{x}</li>" for x in enr)
    cards.append(f"""
<div class='card' data-doc='{doc8}' data-page='{page}'>
  <img src='data:image/jpeg;base64,{b64}'>
  <div class='side'>
    <div class='hdr'>{doc8} p{page}</div>
    <div class='stat'>base_g {r['base_g']} · recovered {r['rec']} · enriched {r['enr']} · lost {r['lost']}</div>
    <ul class='metrics'>{items or '<li>(none captured)</li>'}</ul>
    <label>verdict
      <select class='v'><option value=''>— judge —</option>
        <option>real</option><option>partial</option><option>false</option></select>
    </label>
  </div>
</div>""")

html = f"""<!doctype html><html><head><meta charset='utf-8'><title>0064 — accepted recoveries review</title><style>
body{{font-family:system-ui,Arial;margin:0;background:#f6f7f9}}
#bar{{position:sticky;top:0;background:#fff;padding:.6rem 1rem;border-bottom:2px solid #1e293b;z-index:5}}
#help{{background:#eef6ff;border:1px solid #cfe3ff;margin:1rem;padding:.6rem 1rem;border-radius:6px;font-size:13px}}
.card{{display:flex;gap:14px;background:#fff;border:1px solid #e2e8f0;border-radius:8px;margin:12px;padding:10px}}
.card img{{width:46%;border:1px solid #eee;align-self:flex-start}}
.side{{flex:1;min-width:0}}
.hdr{{font-weight:700;font-size:14px}} .stat{{font-size:12px;color:#475569;margin:2px 0 8px}}
ul.metrics{{margin:0;padding-left:1.1rem;font-size:13px}} ul.metrics li{{margin:3px 0}}
li.rec{{color:#0f5132}} li.enr{{color:#1e40af}}
button{{font-size:14px;padding:.4rem .8rem;background:#2563eb;color:#fff;border:0;border-radius:4px;cursor:pointer}}
select{{font-size:13px}}
</style></head><body>
<div id='bar'><b>Spec 0064 — accepted recoveries ({len(rows)} pages)</b>
&nbsp;<button onclick='dl()'>⬇ Download verdicts (CSV)</button> <span id='prog'></span></div>
<div id='help'>For each ACCEPTED page, the gate recovered/enriched the metrics listed on the right
(<span style='color:#0f5132'>green = recovered</span>, <span style='color:#1e40af'>blue = enriched with parent</span>).
Look at the page and judge: <b>real</b> (the metrics + groupings are correct), <b>partial</b> (some right,
some wrong), or <b>false</b> (scramble / wrong). This is the trustworthy check on the gate's ~94%-clean claim.</div>
{''.join(cards)}
<script>
function dl(){{let o=[['doc','page','verdict']];
document.querySelectorAll('.card').forEach(c=>o.push([c.dataset.doc,c.dataset.page,c.querySelector('.v').value]));
let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([o.map(r=>r.join(',')).join('\\n')],{{type:'text/csv'}}));a.download='accept_verdicts.csv';a.click();}}
function prog(){{let n=document.querySelectorAll('.card').length,d=[...document.querySelectorAll('.v')].filter(s=>s.value).length;document.getElementById('prog').textContent=` — ${{d}}/${{n}} judged`;}}
document.addEventListener('change',prog);prog();
</script></body></html>"""
open(f"{OUT}/accept_review.html", "w").write(html)
print(f"wrote {OUT}/accept_review.html ({os.path.getsize(OUT+'/accept_review.html')//1024} KB)")

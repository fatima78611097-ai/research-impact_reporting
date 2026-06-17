"""0064 — regenerate the accept gallery with the context fixes applied:
  - complete-thought PROMPT (composes card/prose context),
  - deterministic header-attach for ranked-list/table rows.
Shows BEFORE (skinny, from gate_run_v2) vs AFTER for each of the 35 accepted pages.
"""
import base64
import csv
import glob
import io
import json
import os
import re
import sys
import httpx
import pypdfium2 as pdfium
sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, ".")
from grouping_lib import load_doc, page_cells, to_lines, detect_columns
from lavandula.common.secrets import get_secret
from lavandula.nlp.llm_extract import _METRICS_PROMPT
from lavandula.faithfulness.grounding import check as gate_check, normalize

KEY = get_secret("lavandula/deepseek/api_key")
OLD = '- "metric_text": Short natural language description (e.g., "1,514 cancer patients and caregivers served")'
NEW = ('- "metric_text": A COMPLETE, SELF-CONTAINED sentence stating the metric so it is fully '
       'understandable WITHOUT the source page. Combine the number with its label, the section/list/'
       'card heading, and the measured subject — all present on the page — adding only minimal connective '
       'words to read naturally (e.g. "the expanded Child Tax Credit cut child poverty by about 30%", '
       '"63,955 emails delivered via the Email Campaign"). Add NO facts not on the page.')
CT_PROMPT = _METRICS_PROMPT.replace(OLD, NEW)
NUM = re.compile(r"\d[\d.,]*\s*%|\$\s?[\d,]+|\b\d[\d,]*\b")
OUT = "eval_set"


def col_text(cells, w):
    parts = []
    for c in detect_columns(cells, 0.09 * w):
        parts.extend(to_lines(c)); parts.append("")
    return "\n".join(parts)


def extract(client, prompt, text):
    r = client.post("https://api.deepseek.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {KEY}"},
                    json={"model": "deepseek-chat", "temperature": 0.0, "max_tokens": 2200,
                          "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": text}]})
    t = r.json()["choices"][0]["message"]["content"].strip()
    if t.startswith("```"):
        t = t.split("```")[1].lstrip("json").strip()
    try:
        m = json.loads(t)
        return m if isinstance(m, list) else m.get("metrics", [])
    except Exception:
        return []


def num_cell(cells, snip, val):
    tok = re.search(r"\d[\d,\.]*\+?", snip or "")
    key = normalize(tok.group(0)) if tok else normalize(str(val))
    if not key:
        return None
    for c in cells:
        if key in normalize(c["text"]):
            return c
    return None


import statistics


def _vtype(t):
    return "pct" if "%" in t else ("dol" if "$" in t else "num")


def list_header(cells, nc, w, h):
    """Fire ONLY on a true ranked list/table: >=5 SAME-UNIT values in one tight,
    evenly-spaced column. Card grids (email stats column of 3, mixed-unit media
    stack) and prose fail this, so the page title is never grabbed."""
    band = [c for c in cells if abs(c["xc"] - nc["xc"]) < 0.05 * w
            and NUM.search(c["text"]) and _vtype(c["text"]) == _vtype(nc["text"])]
    if len(band) < 5:
        return None
    band.sort(key=lambda c: c["top"])
    gaps = [band[i + 1]["top"] - band[i]["top"] for i in range(len(band) - 1)]
    med = statistics.median(gaps) if gaps else 0
    if med <= 0 or max(gaps) > 3.5 * med:        # irregular spacing -> not a clean list
        return None
    top = band[0]["top"]
    above = sorted([c for c in cells if c["top"] < top - 0.005 * h
                    and abs(c["xc"] - nc["xc"]) < 0.18 * w
                    and not NUM.search(c["text"]) and len(c["text"].split()) <= 5],
                   key=lambda c: c["top"])
    title = " ".join(c["text"] for c in above[:2]).strip()
    if not title or "." in title or len(title.split()) > 6:
        return None
    return title


rows = [r for r in csv.DictReader(open(f"{OUT}/gate_run_v2.csv")) if r["decision"] == "ACCEPT"]
PDF = {os.path.basename(p)[:8]: p for p in glob.glob("pdfs/*.pdf")}
CACHE_F = f"{OUT}/regen_extractions.json"
ext_cache = json.load(open(CACHE_F)) if os.path.exists(CACHE_F) else {}

# resolve EIN per doc (sha8 -> source_org_ein) from the corpus
from sqlalchemy import text as _sqltext
from lavandula.common.db import make_app_engine
_full = {os.path.basename(p)[:8]: os.path.basename(p)[:-4] for p in glob.glob("pdfs/*.pdf")}
ein_of = {}
with make_app_engine().connect() as _c:
    for _d in {r["doc"] for r in rows}:
        fs = _full.get(_d)
        if fs:
            ein_of[_d] = _c.execute(_sqltext(
                "SELECT source_org_ein FROM lava_corpus.corpus WHERE content_sha256=:s LIMIT 1"),
                {"s": fs}).scalar() or "?"

cards = []
cache = {}
with httpx.Client(timeout=120) as client:
    for r in rows:
        doc8, page = r["doc"], int(r["page"])
        if doc8 not in cache:
            cache[doc8] = (load_doc(PDF[doc8]), pdfium.PdfDocument(PDF[doc8]))
        (doc, n), rend = cache[doc8]
        seg = doc.get_page(page); cells = page_cells(seg)
        W, H = seg.dimension.width, seg.dimension.height
        txt = col_text(cells, W)
        ekey = f"{doc8}_{page}"
        if ekey in ext_cache:
            metrics = ext_cache[ekey]
        else:
            metrics = extract(client, CT_PROMPT, txt)
            ext_cache[ekey] = metrics
            json.dump(ext_cache, open(CACHE_F, "w"))
        after = []
        for m in metrics:
            snip = m.get("source_snippet", ""); mt = m.get("metric_text", "")
            g = getattr(gate_check(snippet=snip or "", source_text=txt, tables=[], value=m.get("metric_value")), "grounded", False)
            if not g:
                continue
            nc = num_cell(cells, snip, m.get("metric_value"))
            hdr = list_header(cells, nc, W, H) if nc else None
            # only attach a header to a GENUINELY SKINNY row (no composed-thought
            # connective from the prompt) AND only if the header is a short TITLE
            # (not a prose sentence). This stops over-firing on cards/prose.
            skinny = not re.search(r"\b(of|in|via|for|to|from|with|served|provided|"
                                   r"delivered|cut|reached|received|by|the|and|who|that)\b", mt.lower())
            good_hdr = (hdr and 0 < len(hdr.split()) <= 6 and "." not in hdr
                        and hdr.lower() not in mt.lower())
            if skinny and good_hdr:
                after.append((True, f"{hdr} — {mt}"))      # header-attached
            else:
                after.append((False, mt))
        before = [x.strip() for x in (r.get("rec_ex") or "").split("|") if x.strip()] + \
                 [x.strip() for x in (r.get("enr_ex") or "").split("|") if x.strip()]
        pil = rend[page - 1].render(scale=1.6).to_pil().convert("RGB"); pil.thumbnail((560, 760))
        buf = io.BytesIO(); pil.save(buf, format="JPEG", quality=72)
        b64 = base64.b64encode(buf.getvalue()).decode()
        bhtml = "".join(f"<li>{x}</li>" for x in before[:8]) or "<li>(none)</li>"
        ahtml = "".join(f"<li class='{'hdr' if h else ''}'>{t}</li>" for h, t in after[:10]) or "<li>(none grounded)</li>"
        cards.append(f"""<div class='card' data-doc='{doc8}' data-page='{page}'>
<img src='data:image/jpeg;base64,{b64}'>
<div class='cols'><div class='hdrlbl'>{doc8} p{page} &nbsp;·&nbsp; EIN {ein_of.get(doc8, '?')}</div>
<div class='ba'><div class='b'><b>BEFORE (skinny)</b><ul>{bhtml}</ul></div>
<div class='a'><b>AFTER (complete-thought · <span class='hdr'>green=header-attached</span>)</b><ul>{ahtml}</ul></div></div>
<label>verdict <select class='v'><option value=''>— judge —</option><option>complete</option><option>still-skinny</option><option>wrong</option></select></label>
</div></div>""")
    print(f"built {len(cards)} cards", flush=True)

html = f"""<!doctype html><html><head><meta charset='utf-8'><title>0064 — regenerated accepts</title><style>
body{{font-family:system-ui,Arial;margin:0;background:#f6f7f9}}
#bar{{position:sticky;top:0;background:#fff;padding:.6rem 1rem;border-bottom:2px solid #1e293b;z-index:5}}
.card{{display:flex;gap:14px;background:#fff;border:1px solid #e2e8f0;border-radius:8px;margin:12px;padding:10px}}
.card img{{width:40%;border:1px solid #eee;align-self:flex-start}}
.cols{{flex:1;min-width:0}} .hdrlbl{{font-weight:700;font-size:14px;margin-bottom:4px}}
.ba{{display:flex;gap:16px}} .b,.a{{flex:1;font-size:12.5px}}
.b ul{{color:#9ca3af}} .a ul li{{color:#0f5132;margin:3px 0}} .a ul li.hdr{{color:#15803d;font-weight:600}}
ul{{margin:3px 0;padding-left:1.1rem}} li{{margin:2px 0}}
button{{font-size:14px;padding:.4rem .8rem;background:#2563eb;color:#fff;border:0;border-radius:4px;cursor:pointer}}
.hdr{{color:#15803d}}
</style></head><body>
<div id='bar'><b>Spec 0064 — regenerated accepts (complete-thought + header-attach)</b>
&nbsp;<button onclick='dl()'>⬇ Download verdicts</button> <span id='prog'></span></div>
{''.join(cards)}
<script>
function dl(){{let o=[['doc','page','verdict']];document.querySelectorAll('.card').forEach(c=>o.push([c.dataset.doc,c.dataset.page,c.querySelector('.v').value]));
let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([o.map(r=>r.join(',')).join('\\n')],{{type:'text/csv'}}));a.download='regen_verdicts.csv';a.click();}}
function prog(){{let n=document.querySelectorAll('.card').length,d=[...document.querySelectorAll('.v')].filter(s=>s.value).length;document.getElementById('prog').textContent=` — ${{d}}/${{n}} judged`;}}
document.addEventListener('change',prog);prog();
</script></body></html>"""
open(f"{OUT}/regen_review.html", "w").write(html)
print(f"wrote {OUT}/regen_review.html ({os.path.getsize(OUT+'/regen_review.html')//1024} KB)")

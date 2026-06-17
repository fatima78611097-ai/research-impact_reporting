"""Boxed review: for every metric, highlight on the source page where the gate
grounded the VALUE (red) and the SUBJECT (blue), using the Docling bounding boxes.

Per-metric image: render the value_ref's page once (dedup), draw the value box red
and the subject box blue (each transformed per its own coord_origin), downscale,
serve. Grouped by document, badged publish/quarantine, with a nav index. /p20/.

    python build_boxed_review.py [results_json] [docs_json] [img_subdir] [out_html] [title]
"""
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import boto3
import render
from PIL import Image, ImageDraw
from lavandula.common.db import make_app_engine
from lavandula.parse import config as pc
from sqlalchemy import text

VDIR = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision"
RESULTS = sys.argv[1] if len(sys.argv) > 1 else "locard/operations/comp-metric-regression/scale-test2-results.json"
DOCSJSON = sys.argv[2] if len(sys.argv) > 2 else "/tmp/test2-100.json"
IMGSUB = sys.argv[3] if len(sys.argv) > 3 else "test2box"
OUTNAME = sys.argv[4] if len(sys.argv) > 4 else "test2-boxed-review.html"
TITLE = sys.argv[5] if len(sys.argv) > 5 else "Boxed review — value (red) & subject (blue) highlighted on the source page"
IMG = VDIR + "/" + IMGSUB
os.makedirs(IMG, exist_ok=True)
DPI = 150
SCALE = DPI / 72.0
MAXW = 1200
VAL_C, SUBJ_C = "#e02020", "#1565ff"

results = json.load(open(RESULTS))
shas = [d["sha"] for d in json.load(open(DOCSJSON))["docs"]]
eng = make_app_engine()
s3 = boto3.client("s3")
with eng.connect() as c:
    names = {r[0]: r[1] for r in c.execute(text(
        "SELECT d.content_sha256, ns.name FROM lava_parse.documents d "
        "LEFT JOIN lava_corpus.nonprofits_seed ns ON ns.ein=d.source_org_ein "
        "WHERE d.content_sha256=ANY(:s)"), {"s": shas}).fetchall()}
clean = [s for s in shas if results.get(s)]
BADGE = {"publish": ("PUBLISH", "#0a8a0a"), "quarantine": ("QUARANTINE", "#c0392b")}


def base_page(s8, pdf_dst, page, cache):
    """Render (once) the page PNG and return a PIL image + its (W,H)."""
    key = (s8, page)
    if key in cache:
        return cache[key]
    png = f"{IMG}/{s8}_p{page}.png"
    if not os.path.exists(png) and os.path.exists(pdf_dst):
        subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-png", "-r", str(DPI),
                        "-singlefile", pdf_dst, f"{IMG}/{s8}_p{page}"], capture_output=True, timeout=90)
    im = Image.open(png).convert("RGB") if os.path.exists(png) else None
    cache[key] = im
    return im


def draw_box(d, bb, H, color):
    x0, x1 = bb["l"] * SCALE, bb["r"] * SCALE
    if str(bb.get("coord_origin")) == "BOTTOMLEFT":
        y0, y1 = H - bb["t"] * SCALE, H - bb["b"] * SCALE
    else:
        y0, y1 = bb["t"] * SCALE, bb["b"] * SCALE
    d.rectangle([min(x0, x1) - 3, min(y0, y1) - 3, max(x0, x1) + 3, max(y0, y1) + 3],
                outline=color, width=6)


nav, sections = [], []
tot_pub = tot_q = tot_m = 0
with eng.connect() as conn:
    for di, sha in enumerate(clean, 1):
        recs = results[sha]
        s8 = sha[:8]
        _, _, idmap = render.render_tagged(conn, sha)
        pdf_dst = f"{IMG}/{s8}.pdf"
        if not os.path.exists(pdf_dst):
            try:
                f = str(Path(tempfile.mkdtemp()) / f"{sha}.pdf")
                s3.download_file(pc.S3_BUCKET, f"{pc.S3_PREFIX}{sha}.pdf", f)
                shutil.copy(f, pdf_dst)
            except Exception:
                pass
        cache = {}
        npub = sum(1 for r in recs if r["decision"] == "publish")
        tot_pub += npub
        tot_q += len(recs) - npub
        tot_m += len(recs)
        name = html.escape(str(names.get(sha) or "(unknown org)"))
        nav.append(f'<a href="#d{di}">{di}. {name[:40]} <span style="color:#888">({npub}✓/{len(recs)-npub}✗)</span></a>')
        cards = []
        for mi, r in enumerate(sorted(recs, key=lambda x: x["decision"] != "publish")):
            v = idmap.get(r["value_ref"]) or {}
            s = idmap.get(r["subject_ref"]) or {}
            vpage = v.get("page")
            spage = s.get("page")
            page = vpage or spage
            imgtag = "<i style='color:#999'>(no located box — cited marker not found)</i>"
            note = ""
            if page:
                im = base_page(s8, pdf_dst, page, cache)
                if im is not None:
                    canvas = im.copy()
                    d = ImageDraw.Draw(canvas)
                    H = canvas.size[1]
                    if v.get("bbox") and (vpage == page):
                        draw_box(d, v["bbox"], H, VAL_C)
                    if s.get("bbox") and (spage == page):
                        draw_box(d, s["bbox"], H, SUBJ_C)
                    if spage and vpage and spage != vpage:
                        note = f" · <span style='color:#1565ff'>subject is on page {spage} (shown: value page {vpage})</span>"
                    canvas.thumbnail((MAXW, 99999))
                    out = f"{IMG}/{s8}_m{mi}.png"
                    canvas.save(out, optimize=True)
                    imgtag = f'<img loading="lazy" src="{IMGSUB}/{s8}_m{mi}.png" style="max-width:100%;border:1px solid #ccc">'
            label, color = BADGE.get(r["decision"], ("?", "#888"))
            sub = f" · {html.escape(r['reason'])}" if r["decision"] != "publish" else ""
            pdf_link = (f'<a href="{IMGSUB}/{s8}.pdf#page={page or 1}" target=_blank>📄 open report → page {page or "?"}</a>'
                        if os.path.exists(pdf_dst) else "")
            cards.append(f"""
      <div class=card>
        <div class=hd><span class=badge style="background:{color}">{label}</span>{sub}{note}</div>
        <div class=metric>"{html.escape(str(r['statement']))}"<br>
          <span class=kv><span class=vd>■</span> value=<b>{html.escape(str(r['value']))}</b> &nbsp;
          <span class=sb>■</span> subject=<b>{html.escape(str(r['label']))}</b></span></div>
        <div class=imgwrap>{imgtag}</div>
        <div class=pdf>{pdf_link}</div>
      </div>""")
        sections.append(f"""
    <section id="d{di}">
      <h2>{di}. {name} <span class=shaid>{s8}</span>
        <span class=cnt>{npub} published / {len(recs)-npub} quarantined</span></h2>
      {''.join(cards)}
    </section>""")
        print(f"  [{di}/{len(clean)}] {s8} {str(names.get(sha))[:28]:28} {npub}p/{len(recs)-npub}q", flush=True)

doc = f"""<!doctype html><meta charset=utf-8><title>Boxed review</title>
<style>
body{{font:14px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;color:#1a1a1a}}
.wrap{{max-width:1060px;margin:0 auto;padding:24px}}
h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#555;margin-bottom:14px}}
.legend{{font-size:13px;margin-bottom:10px}} .vd{{color:{VAL_C};font-weight:700}} .sb{{color:{SUBJ_C};font-weight:700}}
nav{{position:sticky;top:0;background:#fff;border-bottom:1px solid #e0e0e0;padding:10px 0;max-height:150px;overflow:auto}}
nav a{{display:inline-block;font-size:12px;color:#06c;text-decoration:none;margin:2px 10px 2px 0;white-space:nowrap}}
h2{{font-size:16px;border-top:2px solid #eee;padding-top:14px;margin-top:24px}}
.shaid{{font:11px ui-monospace,monospace;color:#aaa}} .cnt{{font-size:11px;color:#888;font-weight:400;float:right}}
.card{{border:1px solid #ddd;border-radius:8px;padding:12px 14px;margin:14px 0;background:#fafafa}}
.hd{{font-size:12px;color:#666;margin-bottom:6px}}
.badge{{color:#fff;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:700;margin-right:6px}}
.metric{{font-size:15px;margin:4px 0}} .kv{{font-size:12px;color:#555}}
.imgwrap{{margin:8px 0}} .pdf a{{color:#06c;font-weight:600;text-decoration:none;font-size:13px}}
</style>
<div class=wrap>
<h1>{html.escape(TITLE)}</h1>
<div class=sub>{len(clean)} documents · {tot_m} metrics ·
<b style="color:#0a8a0a">{tot_pub} published</b> / <b style="color:#c0392b">{tot_q} quarantined</b>.</div>
<div class=legend>On each page: <span class=vd>■ red box = the VALUE</span> the gate grounded to,
<span class=sb>■ blue box = the SUBJECT</span>. Look for boxes landing on the wrong number/label (a mispairing).</div>
<nav>{''.join(nav)}</nav>
{''.join(sections)}
</div>"""
open(f"{VDIR}/{OUTNAME}", "w").write(doc)
print(f"\ndocs={len(clean)} metrics={tot_m} pub={tot_pub} quar={tot_q}")
print(f"URL:  https://cloud2.lavandulagroup.com/p20/{OUTNAME}")

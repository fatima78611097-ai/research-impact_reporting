"""Browser review over ALL metrics of the filter-surviving (non-financial/text) docs.

Same card style as the gold-15 review (review_viewer.py): each metric next to the
text it grounded to, its gate decision, a jump-to-page PDF link, and the rendered
source page. Grouped by document, with a top nav index. Static page served at /p20/.

Source = scale100-results-tuned.json (batch-1, current gate), restricted to docs
whose material_type is NOT in {financial_report, not_relevant} (the filter keep set).
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
from lavandula.common.db import make_app_engine
from lavandula.parse import config as pc
from sqlalchemy import text

VDIR = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision"
# argv: [results_json] [docs_json] [img_subdir] [output_html_name] [title]
RESULTS = sys.argv[1] if len(sys.argv) > 1 else "locard/operations/comp-metric-regression/scale100-results-tuned.json"
DOCSJSON = sys.argv[2] if len(sys.argv) > 2 else "/tmp/baseline-100.json"
IMGSUB = sys.argv[3] if len(sys.argv) > 3 else "clean"
OUTNAME = sys.argv[4] if len(sys.argv) > 4 else "clean-review.html"
TITLE = sys.argv[5] if len(sys.argv) > 5 else "Clean-set review — all comp-metrics, filtered reports excluded"
IMG = VDIR + "/" + IMGSUB
os.makedirs(IMG, exist_ok=True)
DISCARD = {"financial_report", "not_relevant"}
tuned = json.load(open(RESULTS))
shas = [d["sha"] for d in json.load(open(DOCSJSON))["docs"]]

eng = make_app_engine()
s3 = boto3.client("s3")
with eng.connect() as c:
    mt = {r[0]: r[1] for r in c.execute(text(
        "SELECT content_sha256, material_type FROM lava_corpus.corpus WHERE content_sha256=ANY(:s)"),
        {"s": shas}).fetchall()}
    names = {r[0]: r[1] for r in c.execute(text(
        "SELECT d.content_sha256, ns.name FROM lava_parse.documents d "
        "LEFT JOIN lava_corpus.nonprofits_seed ns ON ns.ein=d.source_org_ein "
        "WHERE d.content_sha256=ANY(:s)"), {"s": shas}).fetchall()}

clean = [s for s in shas if mt.get(s) not in DISCARD and tuned.get(s)]
BADGE = {"publish": ("PUBLISH", "#0a8a0a"), "quarantine": ("QUARANTINE", "#c0392b")}


def render_page(s8, pdf_dst, page):
    out = f"{IMG}/{s8}_p{page}.png"
    if not os.path.exists(out) and os.path.exists(pdf_dst):
        subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-png", "-r", "110",
                        "-singlefile", pdf_dst, f"{IMG}/{s8}_p{page}"], capture_output=True, timeout=90)
    return os.path.exists(out)


nav, sections = [], []
tot_pub = tot_q = tot_m = 0
with eng.connect() as conn:
    for di, sha in enumerate(clean, 1):
        recs = tuned[sha]
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
        npub = sum(1 for r in recs if r["decision"] == "publish")
        tot_pub += npub
        tot_q += len(recs) - npub
        tot_m += len(recs)
        name = html.escape(str(names.get(sha) or "(unknown org)"))
        nav.append(f'<a href="#d{di}">{di}. {name[:42]} '
                   f'<span style="color:#888">({npub}✓/{len(recs)-npub}✗)</span></a>')
        cards = []
        for r in sorted(recs, key=lambda x: x["decision"] != "publish"):
            vr = idmap.get(r["value_ref"]) or {}
            srf = idmap.get(r["subject_ref"]) or {}
            page = vr.get("page") or srf.get("page") or 1
            has_img = render_page(s8, pdf_dst, page)
            label, color = BADGE.get(r["decision"], ("?", "#888"))
            sub = f" · {html.escape(r['reason'])}" if r["decision"] != "publish" else ""
            vtext = html.escape(str(vr.get("text", ""))[:140]) or "<i>(cited marker not found — unverifiable)</i>"
            stext = html.escape(str(srf.get("text", ""))[:140]) or "<i>(not found)</i>"
            img = (f'<img loading="lazy" src="{IMGSUB}/{s8}_p{page}.png" style="max-width:100%;border:1px solid #ccc;margin-top:8px">'
                   if has_img else "")
            pdf_link = (f'<a href="{IMGSUB}/{s8}.pdf#page={page}" target=_blank>\U0001F4C4 open report → page {page}</a>'
                        if os.path.exists(pdf_dst) else "")
            cards.append(f"""
      <div class=card>
        <div class=hd><span class=badge style="background:{color}">{label}</span>{sub}</div>
        <div class=metric>"{html.escape(str(r['statement']))}"<br>
          <span class=kv>value=<b>{html.escape(str(r['value']))}</b> &nbsp; subject=<b>{html.escape(str(r['label']))}</b></span></div>
        <div class=ground>grounded value &rarr; {vtext}<br>grounded subject &rarr; {stext}</div>
        <div class=pdf>{pdf_link}</div>
        <details><summary>show source page {page}</summary>{img}</details>
      </div>""")
        sections.append(f"""
    <section id="d{di}">
      <h2>{di}. {name} <span class=tag>[{html.escape(str(mt.get(sha)))}]</span>
        <span class=shaid>{s8}</span> <span class=cnt>{npub} published / {len(recs)-npub} quarantined</span></h2>
      {''.join(cards)}
    </section>""")
        print(f"  [{di}/{len(clean)}] {s8} {str(names.get(sha))[:30]:30} {npub}p/{len(recs)-npub}q", flush=True)

doc = f"""<!doctype html><meta charset=utf-8><title>Clean-set review — comp-metrics</title>
<style>
body{{font:14px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;color:#1a1a1a}}
.wrap{{max-width:1040px;margin:0 auto;padding:24px}}
h1{{font-size:21px;margin:0 0 4px}} .sub{{color:#555;margin-bottom:16px}}
nav{{position:sticky;top:0;background:#fff;border-bottom:1px solid #e0e0e0;padding:10px 0;max-height:170px;overflow:auto}}
nav a{{display:inline-block;font-size:12px;color:#06c;text-decoration:none;margin:2px 10px 2px 0;white-space:nowrap}}
h2{{font-size:16px;border-top:2px solid #eee;padding-top:14px;margin-top:26px}}
.tag{{font-size:11px;color:#888;font-weight:400}} .shaid{{font:11px ui-monospace,monospace;color:#aaa}}
.cnt{{font-size:11px;color:#888;font-weight:400;float:right}}
.card{{border:1px solid #ddd;border-radius:8px;padding:12px 14px;margin:12px 0;background:#fafafa}}
.hd{{font-size:12px;color:#666;margin-bottom:6px}}
.badge{{color:#fff;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:700;margin-right:6px}}
.metric{{font-size:15px;margin:4px 0}} .kv{{font-size:12px;color:#555}}
.ground{{font:12px ui-monospace,Menlo,monospace;color:#666;background:#fff;border:1px solid #eee;padding:6px 8px;margin:6px 0;white-space:pre-wrap}}
.pdf a{{color:#06c;font-weight:600;text-decoration:none;font-size:13px}}
details{{margin-top:6px}} summary{{cursor:pointer;font-size:12px;color:#888}}
</style>
<div class=wrap>
<h1>{html.escape(TITLE)}</h1>
<div class=sub>{len(clean)} documents (financial/compilation/not-relevant excluded) &middot;
{tot_m} metrics &middot; <b style="color:#0a8a0a">{tot_pub} published</b> /
<b style="color:#c0392b">{tot_q} quarantined</b>.
Each card shows the metric, the source text it grounded to, the gate decision, and a jump-to-page link.
Tell me which published metrics are wrong (value or subject mispaired) and which quarantines should have passed.</div>
<nav>{''.join(nav)}</nav>
{''.join(sections)}
</div>"""
open(f"{VDIR}/{OUTNAME}", "w").write(doc)
print(f"\ndocs={len(clean)} metrics={tot_m} pub={tot_pub} quar={tot_q}")
print(f"URL:  https://cloud2.lavandulagroup.com/p20/{OUTNAME}")

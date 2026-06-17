"""Build a visual gold-review page: each sampled published metric next to its
rendered source page + grounding + my (frontier) verdict, served at /p20/."""
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/home/ubuntu/research")
import boto3
from lavandula.parse import config as pc

VDIR = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision"
IMG = VDIR + "/gold"
os.makedirs(IMG, exist_ok=True)
PACKETS = json.load(open("locard/operations/comp-metric-regression/gold-sample.json"))
FULL = {d["sha"][:8]: d["sha"] for d in json.load(open("/tmp/baseline-100.json"))["docs"]}

# verdicts — ALL value-confirmed verbatim against the FULL document (15/15).
# "precision" = value correct & in text, but the cited value_ref points to a neighbor
# sentence (provenance-tightening item, not a value error).
V = {
 1: ("confirmed", "'TOTAL EXPENSES … $752,858' (Program Services column) — verbatim."),
 2: ("confirmed", "'Total Assets $52,364,342' — verbatim."),
 3: ("confirmed", "'e. White 2832' — value correct; but it's a demographic line-item the prompt should EXCLUDE (low-value SELECTION, not an error)."),
 4: ("confirmed", "Body text: 'In Year 3, 27% of participants… making employment gains'; 39% & 23% on the next page; '256 participants'. All verbatim. (I was wrong earlier — NOT chart-derived.)"),
 5: ("confirmed", "'Foster Family Care 5,963,689' — verbatim."),
 6: ("confirmed", "'These expansions represent 332 net new jobs' (pp.6-7) — verbatim. (value_ref cited a neighbor sentence — precision item.)"),
 7: ("confirmed", "'of whom 81 percent now have jobs' — verbatim."),
 8: ("confirmed", "'through 144 applications from youth in need of just such housing' — verbatim."),
 9: ("confirmed", "'77 PLTI alumni attended one or more of 19 events' — verbatim."),
 10: ("confirmed", "'over 5,000 people with disabilities and their families received support' — verbatim. (value_ref points one sentence early — precision item.)"),
 11: ("confirmed", "'All seven organizations implemented at least one new effective practice' (page 1) — verbatim. (value_ref cited the intro sentence — precision item.)"),
 12: ("confirmed", "'Our Summer Program… served a daily average of 42 youth' (page 1) — verbatim. (value_ref cited the After-School sentence — precision item.)"),
 13: ("confirmed", "'$5,606,969.08 in assistance provided in Calendar Year 2022' — verbatim (metric rounds the cents)."),
 14: ("confirmed", "'85% of youth demonstrate skills, knowledge and growth mindset' — verbatim."),
 15: ("confirmed", "'63,791 pounds … was distributed' — verbatim."),
}
COLOR = {"confirmed": "#0a0", "precision": "#a80", "error": "#c00"}

s3 = boto3.client("s3")
pdfs = {}
cards = []
for i, p in enumerate(PACKETS, 1):
    sha = FULL.get(p["sha8"], p["sha8"])
    page = p["page"] or 1
    name = f"{p['sha8']}_p{page}.png"
    pdf_dst = f"{IMG}/{p['sha8']}.pdf"
    if not os.path.exists(pdf_dst):
        if sha not in pdfs:
            f = str(Path(tempfile.mkdtemp()) / f"{sha}.pdf")
            try:
                s3.download_file(pc.S3_BUCKET, f"{pc.S3_PREFIX}{sha}.pdf", f)
                pdfs[sha] = f
            except Exception:
                pdfs[sha] = None
        if pdfs.get(sha):
            shutil.copy(pdfs[sha], pdf_dst)
    if not os.path.exists(f"{IMG}/{name}") and os.path.exists(pdf_dst):
        subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-png", "-r", "110",
                        "-singlefile", pdf_dst, f"{IMG}/{p['sha8']}_p{page}"],
                       capture_output=True, timeout=90)
    verdict, note = V.get(i, ("?", ""))
    img = f'<img src="gold/{name}" style="max-width:100%;border:1px solid #ccc">' if os.path.exists(f"{IMG}/{name}") else "<i>(page image unavailable)</i>"
    pdf_link = (f'<a href="gold/{p["sha8"]}.pdf#page={page}" target=_blank>📄 Open full report PDF (jumps to page {page})</a>'
                if os.path.exists(pdf_dst) else "")
    cards.append(f"""
    <div class=card>
      <div class=hd><span class=badge style="background:{COLOR.get(verdict,'#888')}">{verdict.upper()}</span>
        <b>[{i}] {html.escape(p['class'])}</b> &nbsp; {html.escape(p['sha8'])} &nbsp; page {page}</div>
      <div class=metric>"{html.escape(p['statement'])}"<br>
        <span class=kv>value=<b>{html.escape(str(p['value']))}</b> &nbsp; label=<b>{html.escape(str(p['label']))}</b></span></div>
      <div class=ground>grounded&nbsp;value → {html.escape(str(p['value_ref_text'])[:120])}<br>
        grounded&nbsp;subject → {html.escape(str(p['subject_ref_text'])[:120])}</div>
      <div class=note><b>verdict:</b> {html.escape(note)}</div>
      <div class=pdf>{pdf_link}</div>
      <div class=img>{img}</div>
    </div>""")

page_html = f"""<!doctype html><meta charset=utf-8><title>Gold review — published comp-metrics</title>
<style>
body{{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:24px;max-width:1000px;color:#1a1a1a}}
h1{{font-size:20px}} .sub{{color:#555;margin-bottom:18px}}
.card{{border:1px solid #ddd;border-radius:8px;padding:14px 16px;margin:16px 0;background:#fafafa}}
.hd{{font-size:13px;color:#444;margin-bottom:8px}}
.badge{{color:#fff;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:700;margin-right:8px}}
.metric{{font-size:15px;margin:6px 0}} .kv{{font-size:12px;color:#555}}
.ground{{font:12px ui-monospace,Menlo,monospace;color:#666;background:#fff;border:1px solid #eee;padding:6px 8px;margin:6px 0;white-space:pre-wrap}}
.note{{font-size:13px;margin:8px 0;padding:6px 10px;background:#fffbe6;border:1px solid #f0e0a0;border-radius:5px}}
.pdf{{margin:8px 0;font-size:14px}} .pdf a{{color:#06c;font-weight:600;text-decoration:none}}
.img{{margin-top:10px}}
</style>
<h1>Gold review — 15 published comp-metrics vs source pages</h1>
<div class=sub>Each card: the metric, the text it grounded to, my (frontier) verdict, and the rendered source page.
Focus on the <b style="color:#c00">ERROR</b>/<b style="color:#c40">SUSPECT</b> cards — confirm or overturn my calls.</div>
{''.join(cards)}
"""
open(f"{VDIR}/gold-review.html", "w").write(page_html)
print("wrote", f"{VDIR}/gold-review.html")
print("URL:  https://cloud2.lavandulagroup.com/p20/gold-review.html")
print("rendered pages:", len([1 for p in PACKETS if os.path.exists(f"{IMG}/{p['sha8']}_p{p['page'] or 1}.png")]), "/", len(PACKETS))

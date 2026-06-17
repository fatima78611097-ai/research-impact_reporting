"""Examine ONE doc end-to-end on a dense-graphic page: render the page, extract
comp-metrics, ground them, run the gate, and build a viewer (page image + each
metric's value/subject grounding + gate decision) so mispairings are visible."""
import html as H
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import gate
import render
from lavandula.common.db import make_app_engine
from lavandula.common.secrets import get_secret
from lavandula.parse import config as pc
from sqlalchemy import text

SHA8 = sys.argv[1] if len(sys.argv) > 1 else "d1d53b04"
VIRGIN = open("locard/operations/prompt-backups/2026-06-06/comp-metric-prompt.KNOWN-GOOD.txt").read()
GROUND = open("locard/operations/comp-metric-regression/comp-metric-grounding.v1.txt").read()
KEY = get_secret("lavandula/deepseek/api_key")
VDIR = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/ymca"


def chat(system, user):
    body = json.dumps({"model": "deepseek-chat", "temperature": 0.0, "max_tokens": 3000,
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
    req = urllib.request.Request("https://api.deepseek.com/v1/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    t = json.loads(urllib.request.urlopen(req, timeout=180).read())["choices"][0]["message"]["content"].strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        t = t[4:].strip() if t.lower().startswith("json") else t.strip()
    try:
        o = json.loads(t)
        return o if isinstance(o, list) else []
    except Exception:
        return []


def norm(x):
    return x.strip().strip("⟨⟩").strip() if isinstance(x, str) else None


eng = make_app_engine()
import boto3
s3 = boto3.client("s3")
with eng.connect() as c:
    full = c.execute(text("SELECT content_sha256, page_count FROM lava_parse.documents WHERE left(content_sha256,8)=:s"),
                     {"s": SHA8}).fetchone()
    sha, npages = full[0], full[1]
    sectext = "\n".join(r[0] for r in c.execute(text(
        "SELECT body_text FROM lava_parse.sections WHERE content_sha256=:s ORDER BY section_index"),
        {"s": sha}).fetchall() if r[0])
    _, tagged, idmap = render.render_tagged(c, sha)

# render every page
pdf = str(Path(tempfile.mkdtemp()) / "d.pdf")
s3.download_file(pc.S3_BUCKET, f"{pc.S3_PREFIX}{sha}.pdf", pdf)
imgs = []
for pg in range(1, min(npages, 4) + 1):
    subprocess.run(["pdftoppm", "-f", str(pg), "-l", str(pg), "-png", "-r", "120", "-singlefile",
                    pdf, f"{VDIR}/{SHA8}_p{pg}"], capture_output=True, timeout=90)
    imgs.append(f"{SHA8}_p{pg}.png")

metrics = chat(VIRGIN, sectext)
g = chat(GROUND, "METRICS:\n" + "\n".join(
    f"M{i+1}: {m.get('statement')} (value={m.get('metric_value')}, subject={m.get('label')})"
    for i, m in enumerate(metrics)) + "\n\nDOCUMENT:\n" + tagged)
bym = {x.get("m"): x for x in g if isinstance(x, dict)}

cards = []
for i, m in enumerate(metrics):
    row = bym.get(i + 1) or (g[i] if i < len(g) and isinstance(g[i], dict) else {})
    vr, sr = norm(row.get("value_ref")), norm(row.get("subject_ref"))
    dec, reason = gate.verdict({"metric_value": m.get("metric_value"), "label": m.get("label"),
                                "statement": m.get("statement")}, vr, sr, idmap)
    vtext = (idmap.get(vr) or {}).get("text", "")
    stext = (idmap.get(sr) or {}).get("text", "")
    color = "#0a0" if dec == "publish" else "#c00"
    cards.append(f"""<div style="border:1px solid #ddd;border-radius:7px;padding:10px;margin:8px 0;background:#fafafa">
      <div style="font-size:15px">"{H.escape(str(m.get('statement')))}"</div>
      <div style="font-size:12px;color:#555">value=<b>{H.escape(str(m.get('metric_value')))}</b> · subject=<b>{H.escape(str(m.get('label')))}</b>
       · <b style="color:{color}">{dec.upper()}</b> ({H.escape(reason)})</div>
      <div style="font:11px ui-monospace,Menlo,monospace;color:#666">value@ {H.escape(str(vtext)[:90])}<br>subj@ {H.escape(str(stext)[:90])}</div>
    </div>""")

imgtags = "".join(f'<img src="{im}" style="max-width:100%;border:1px solid #ccc;margin:6px 0">' for im in imgs)
doc = f"""<!doctype html><meta charset=utf-8><meta http-equiv="Cache-Control" content="no-store">
<title>Examine {SHA8}</title><style>body{{font:14px/1.5 -apple-system,sans-serif;margin:20px;max-width:1000px}}</style>
<h1>Examine {SHA8} — page vs extracted comp-metrics</h1>
<p>Read the page, then check each metric below for number↔label mispairing. Gate decision shown per metric.</p>
<h2>The page(s)</h2>{imgtags}
<h2>Extracted metrics ({len(metrics)})</h2>{''.join(cards)}"""
open(f"{VDIR}/{SHA8}-examine.html", "w").write(doc)
print("extracted", len(metrics), "metrics")
for i, m in enumerate(metrics, 1):
    print(f"  {i}. value={m.get('metric_value')!r:>10}  label={str(m.get('label'))[:24]:24}  {str(m.get('statement'))[:60]}")
print(f"\nURL: https://cloud2.lavandulagroup.com/p20/ymca/{SHA8}-examine.html")

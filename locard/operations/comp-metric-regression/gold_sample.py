"""Gold-baseline sampler: pull a stratified, seeded sample of PUBLISHED metrics and
assemble the evidence to adjudicate each — the metric, the exact text it grounded to
(value_ref / subject_ref), and the deterministic pdftotext of its source page. For
the human(operator)+frontier(this session) verification of the <1% published-error bar.
"""
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import render
from lavandula.common.db import make_app_engine
from lavandula.parse import config as pc
from sqlalchemy import text

TUNED = json.load(open("locard/operations/comp-metric-regression/scale100-results-tuned.json"))
PER_CLASS = 5
random.seed(1234)


def classify(tc, fc):
    if tc >= 5:
        return "table-heavy"
    if fc >= 20 and tc <= 2:
        return "infographic"
    return "prose"


eng = make_app_engine()
import boto3
s3 = boto3.client("s3")
pdfcache = {}


def page_text(sha, page):
    if not page:
        return "(no page)"
    key = (sha, page)
    if key in pdfcache:
        return pdfcache[key]
    p = pdfcache.get(sha + "_pdf")
    if p is None:
        p = str(Path(tempfile.mkdtemp()) / f"{sha}.pdf")
        try:
            s3.download_file(pc.S3_BUCKET, f"{pc.S3_PREFIX}{sha}.pdf", p)
        except Exception:
            p = ""
        pdfcache[sha + "_pdf"] = p
    if not p:
        return "(pdf unavailable)"
    try:
        out = subprocess.run(["pdftotext", "-f", str(page), "-l", str(page), "-layout", p, "-"],
                             capture_output=True, text=True, timeout=60).stdout
    except Exception as e:
        out = f"(pdftotext failed: {e})"
    pdfcache[key] = out
    return out


with eng.connect() as conn:
    prof = {r[0]: (r[1] or 0, r[2] or 0) for r in conn.execute(text(
        "SELECT content_sha256, table_count, figure_count FROM lava_parse.documents "
        "WHERE content_sha256 = ANY(:s)"), {"s": list(TUNED.keys())}).fetchall()}
    # group published metrics by doc class
    byclass = {"table-heavy": [], "infographic": [], "prose": []}
    for sha, recs in TUNED.items():
        cls = classify(*prof.get(sha, (0, 0)))
        for r in recs:
            if r["decision"] == "publish":
                byclass[cls].append((sha, r))
    sample = []
    for cls, items in byclass.items():
        random.shuffle(items)
        sample += [(cls, sha, r) for sha, r in items[:PER_CLASS]]

    idmaps = {}
    packets = []
    for cls, sha, r in sample:
        if sha not in idmaps:
            idmaps[sha] = render.render_tagged(conn, sha)[2]
        im = idmaps[sha]
        vr, srf = im.get(r["value_ref"]) or {}, im.get(r["subject_ref"]) or {}
        page = vr.get("page") or srf.get("page")
        packets.append({
            "class": cls, "sha8": sha[:8], "statement": r["statement"],
            "value": r["value"], "label": r["label"],
            "value_ref_text": vr.get("text"), "subject_ref_text": srf.get("text"),
            "page": page, "page_text": page_text(sha, page)[:1600],
        })

json.dump(packets, open("locard/operations/comp-metric-regression/gold-sample.json", "w"), indent=1)
for i, p in enumerate(packets, 1):
    print(f"\n===== [{i}] {p['class']}  {p['sha8']}  (page {p['page']}) =====")
    print(f"METRIC: {p['statement']}")
    print(f"  value={p['value']!r}  label={p['label']!r}")
    print(f"  grounded value_ref -> {str(p['value_ref_text'])[:80]!r}")
    print(f"  grounded subject_ref -> {str(p['subject_ref_text'])[:80]!r}")
    print(f"  --- page {p['page']} (pdftotext) ---")
    print("  " + (p["page_text"] or "").strip()[:1100].replace("\n", "\n  "))
print(f"\n[{len(packets)} packets saved to gold-sample.json]")

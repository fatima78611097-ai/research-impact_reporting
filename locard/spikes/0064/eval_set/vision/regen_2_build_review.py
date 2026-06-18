"""Regenerate the metric-review data — STEP 2 of 2: BUILD + GATE.

Reads `prompt_test.json` (STEP 1), then:
  1. dedup()            — drop near-duplicate metrics per doc        (slot_render)
  2. resolve markers    — value_ref / subject_ref -> page + bbox + text
  3. render boxed image — source page with value (red) + subject (blue) boxes
  4. render_and_grade() — display text + publish/quarantine decision (slot_render)
and writes `new-review-data.json` (consumed by new-metric-review.html).

The gate is folded in here, so the output IS the final gated data — no separate
apply step. Run after regen_1_extract.py:

    python3 regen_2_build_review.py
"""
import json
import os
import glob
import subprocess
import shutil
import tempfile
import sys

sys.path.insert(0, "/home/ubuntu/research")
from lavandula.common.db import make_app_engine
from lavandula.nlp.marker_render import render_tagged, SkipDocument
from lavandula.nlp.slot_render import render_and_grade, dedup
from lavandula.parse import config as pc
from sqlalchemy import text
import boto3
from PIL import Image, ImageDraw

VDIR = os.path.dirname(os.path.abspath(__file__))
IMGSUB = "new_review_img"
os.makedirs(os.path.join(VDIR, IMGSUB), exist_ok=True)
DPI = 170
SCALE = DPI / 72
VAL, SUB = "#e02020", "#1565ff"

R = json.load(open(os.path.join(VDIR, "prompt_test.json")))
eng = make_app_engine()
s3 = boto3.client("s3")
pdfmap = {p.split("/")[-1][:8]: p for p in glob.glob(f"{VDIR}/*_img/*.pdf")}


def draw(dr, bb, H, color, w):
    if not bb:
        return
    x0, x1 = bb["l"] * SCALE, bb["r"] * SCALE
    if str(bb.get("coord_origin")) == "BOTTOMLEFT":
        y0, y1 = H - bb["t"] * SCALE, H - bb["b"] * SCALE
    else:
        y0, y1 = bb["t"] * SCALE, bb["b"] * SCALE
    dr.rectangle([min(x0, x1) - 4, min(y0, y1) - 4, max(x0, x1) + 4, max(y0, y1) + 4], outline=color, width=w)


def getpdf(s8, full):
    dst = f"{VDIR}/{IMGSUB}/{s8}.pdf"
    if os.path.exists(dst):
        return dst
    if s8 in pdfmap:
        shutil.copy(pdfmap[s8], dst)
        return dst
    try:
        f = tempfile.mkdtemp() + f"/{full}.pdf"
        s3.download_file(pc.S3_BUCKET, f"{pc.S3_PREFIX}{full}.pdf", f)
        shutil.copy(f, dst)
        return dst
    except Exception:
        return None


_page_cache = {}
def base_page(s8, full, pg):
    k = (s8, pg)
    if k in _page_cache:
        return _page_cache[k]
    png = f"{VDIR}/{IMGSUB}/{s8}_p{pg}.png"
    if not os.path.exists(png):
        pdf = getpdf(s8, full)
        if pdf:
            subprocess.run(["pdftoppm", "-f", str(pg), "-l", str(pg), "-png", "-r", str(DPI),
                            "-singlefile", pdf, f"{VDIR}/{IMGSUB}/{s8}_p{pg}"], capture_output=True, timeout=90)
    im = Image.open(png).convert("RGB") if os.path.exists(png) else None
    _page_cache[k] = im
    return im


records = []
with eng.connect() as c:
    for di, d in enumerate(R):
        if "error" in d:
            continue
        s8, org = d["sha8"], d.get("org", "")
        d["new"] = dedup(d["new"])                                 # GATE 1 — dedup
        full = c.execute(text("SELECT content_sha256 FROM lava_parse.sections WHERE content_sha256 LIKE :p LIMIT 1"),
                         {"p": s8 + "%"}).scalar()
        try:
            idmap = render_tagged(c, full).idmap
        except SkipDocument:
            idmap = {}
        for idx, m in enumerate(d["new"]):
            vr = str(m.get("value_ref", "")).strip("⟨⟩ ")
            sr = str(m.get("subject_ref", "")).strip("⟨⟩ ")
            vc = idmap.get(vr) or idmap.get(f"⟨{vr}⟩") or {}
            sc = idmap.get(sr) or idmap.get(f"⟨{sr}⟩") or {}
            vpage, spage = vc.get("page"), sc.get("page")
            page = vpage or spage
            img = ""
            if page:
                base = base_page(s8, full, page)
                if base is not None:
                    cv = base.copy()
                    drw = ImageDraw.Draw(cv)
                    H = cv.size[1]
                    if vc.get("bbox") and vpage == page:
                        draw(drw, vc["bbox"], H, VAL, 7)
                    if sc.get("bbox") and spage == page:
                        draw(drw, sc["bbox"], H, SUB, 6)
                    cv.thumbnail((1100, 99999))
                    cv.save(f"{VDIR}/{IMGSUB}/{s8}_m{idx}.png", optimize=True)
                    img = f"{IMGSUB}/{s8}_m{idx}.png"
            disp, decision, detail = render_and_grade(m, sc.get("text"))   # GATE 2-5 — render + filter
            rec = {"id": f"np:{s8}:{idx}", "set": "new-prompt", "sha8": s8, "org": org,
                   "statement": disp, "value": m.get("metric_value"), "subject": sc.get("text") or "",
                   "gate_decision": decision, "gate_reason": m.get("tier"), "same_marker": (vr == sr),
                   "v_page": vpage, "v_text": vc.get("text"), "s_page": spage, "s_text": sc.get("text"),
                   "found": [], "page": page, "v_bbox": vc.get("bbox"), "s_bbox": sc.get("bbox"),
                   "img": img, "pdf": f"{IMGSUB}/{s8}.pdf", "claude": {}}
            if decision == "quarantine":
                rec["decided_by"] = "filter"
                rec["decided_detail"] = detail
            records.append(rec)
        print(f"  [{di + 1}/{len(R)}] {s8} {str(org)[:24]:24} +{len(d['new'])}", flush=True)

json.dump(records, open(f"{VDIR}/new-review-data.json", "w"), default=str)
pub = sum(1 for r in records if r["gate_decision"] == "publish")
print(f"DONE records={len(records)} ({pub} publish / {len(records) - pub} quarantine) -> new-review-data.json", flush=True)

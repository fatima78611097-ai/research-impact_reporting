"""Generate the boxed source-page image per metric for the review viewer, into
reviewbox/{sha8}_{idx}.png, and copy each source PDF to reviewbox/{sha8}.pdf.

ENH-001: when value & subject share a marker (same passage), draw ONE purple box
instead of red-over-blue. Otherwise red=value, blue=subject. Reads review-data.json.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/home/ubuntu/research")
import boto3
from PIL import Image, ImageDraw
from lavandula.parse import config as pc

VDIR = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision"
IMG = VDIR + "/reviewbox"
os.makedirs(IMG, exist_ok=True)
DPI = 150
SCALE = DPI / 72.0
MAXW = 1200
RED, BLUE, PURPLE = "#e02020", "#1565ff", "#8e24aa"

data = json.load(open(VDIR + "/review-data.json"))
s3 = boto3.client("s3")
# group by sha for PDF reuse
by_sha = {}
for r in data:
    by_sha.setdefault(r["sha8"], []).append(r)

# need full sha per sha8
import re
sha_full = {}
from lavandula.common.db import make_app_engine
from sqlalchemy import text
with make_app_engine().connect() as c:
    for s8 in by_sha:
        row = c.execute(text("SELECT content_sha256 FROM lava_parse.documents WHERE left(content_sha256,8)=:s"), {"s": s8}).fetchone()
        if row:
            sha_full[s8] = row[0]


def draw(d, bb, H, color):
    if not bb:
        return
    x0, x1 = bb["l"] * SCALE, bb["r"] * SCALE
    if str(bb.get("coord_origin")) == "BOTTOMLEFT":
        y0, y1 = H - bb["t"] * SCALE, H - bb["b"] * SCALE
    else:
        y0, y1 = bb["t"] * SCALE, bb["b"] * SCALE
    d.rectangle([min(x0, x1) - 3, min(y0, y1) - 3, max(x0, x1) + 3, max(y0, y1) + 3], outline=color, width=6)


done = 0
# process in VIEWER order (order docs first appear in review-data.json) so the
# top of the operator's list fills first, not sha8 order.
ordered, seen = [], set()
for r in data:
    if r["sha8"] not in seen:
        seen.add(r["sha8"])
        ordered.append(r["sha8"])
for di, s8 in enumerate(ordered, 1):
    recs = by_sha[s8]
    full = sha_full.get(s8)
    if not full:
        continue
    pdf_dst = f"{IMG}/{s8}.pdf"
    if not os.path.exists(pdf_dst):
        try:
            f = str(Path(tempfile.mkdtemp()) / f"{s8}.pdf")
            s3.download_file(pc.S3_BUCKET, f"{pc.S3_PREFIX}{full}.pdf", f)
            shutil.copy(f, pdf_dst)
        except Exception as e:
            print(f"  PDF fail {s8}: {e}", flush=True)
            continue
    page_cache = {}
    for r in recs:
        out = f"{IMG}/{s8}_{r['idx']}.png"
        if os.path.exists(out):
            done += 1
            continue
        page = r.get("page")
        if not page:
            continue
        if page not in page_cache:
            png = f"{IMG}/_{s8}_p{page}.png"
            if not os.path.exists(png):
                subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-png", "-r", str(DPI),
                                "-singlefile", pdf_dst, f"{IMG}/_{s8}_p{page}"], capture_output=True, timeout=90)
            page_cache[page] = Image.open(png).convert("RGB") if os.path.exists(png) else None
        base = page_cache[page]
        if base is None:
            continue
        canvas = base.copy()
        dd = ImageDraw.Draw(canvas)
        H = canvas.size[1]
        if r.get("same_marker") and r.get("v_bbox"):
            draw(dd, r["v_bbox"], H, PURPLE)   # ENH-001
        else:
            if r.get("v_bbox") and r.get("v_page") == page:
                draw(dd, r["v_bbox"], H, RED)
            if r.get("s_bbox") and r.get("s_page") == page:
                draw(dd, r["s_bbox"], H, BLUE)
        canvas.thumbnail((MAXW, 99999))
        canvas.save(out, optimize=True)
        done += 1
    # clean page temp files for this doc
    for f in os.listdir(IMG):
        if f.startswith(f"_{s8}_p"):
            os.remove(os.path.join(IMG, f))
    if di % 10 == 0:
        print(f"  {di}/{len(by_sha)} docs, {done} images", flush=True)

print(f"DONE: {done} boxed images in {IMG}")

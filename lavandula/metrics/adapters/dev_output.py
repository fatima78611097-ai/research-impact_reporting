"""Dev OUTPUT adapter — renders the review surface: JSON + boxed page images.

I/O / presentation only: it serializes Metric records (model text + gate result + provenance)
into the viewer's JSON shape and draws each source page with the value (red) and label (blue)
boxes. It makes NO decisions — every field comes straight off the Metric. The prod output
adapter differs ONLY here (an RDS commit instead of JSON+images).

Image-render helpers lifted verbatim from the spike's regen_2_model_text.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import tempfile

import boto3
from PIL import Image, ImageDraw
from sqlalchemy import text

from lavandula.parse import config as pc

DPI = 170
SCALE = DPI / 72
VAL, SUB = "#e02020", "#1565ff"
IMGSUB = "img"


def _draw(dr, bb, H, color, w):
    if not bb:
        return
    x0, x1 = bb["l"] * SCALE, bb["r"] * SCALE
    if str(bb.get("coord_origin")) == "BOTTOMLEFT":
        y0, y1 = H - bb["t"] * SCALE, H - bb["b"] * SCALE
    else:
        y0, y1 = bb["t"] * SCALE, bb["b"] * SCALE
    dr.rectangle([min(x0, x1) - 4, min(y0, y1) - 4, max(x0, x1) + 4, max(y0, y1) + 4], outline=color, width=w)


def _record(m, img, ver=""):
    """One Metric -> the viewer's JSON record (every field straight off the metric).
    `ver` cache-busts img/pdf URLs — changes only when the extraction is re-frozen, so
    re-gates don't force image re-downloads but a reshuffle can't show a stale page."""
    rec = {
        "id": f"mt:{m.sha8}:{m.idx}", "set": "model-text", "sha8": m.sha8, "org": m.org,
        "statement": m.statement, "value": m.value, "unit": m.unit, "tier": m.tier,
        "program": m.program, "section_heading": m.prov.section_heading,
        "source_snippet": m.prov.source_snippet, "subject": m.subject,
        "same_marker": m.prov.same_marker,
        "v_page": m.prov.value_page, "v_text": m.prov.value_text,
        "s_page": m.prov.subject_page, "s_text": m.prov.subject_text,
        "v_bbox": m.prov.value_bbox, "s_bbox": m.prov.subject_bbox, "page": m.prov.page,
        "gate_decision": m.decision, "gate_reason": m.reason,
        "flags": m.flags, "flag_detail": m.flag_detail,
        "ver": ver,
        "img": img, "pdf": f"{IMGSUB}/{m.sha8}.pdf",
    }
    if "incomplete" in m.flags:                      # kept for the existing Complete filter
        rec["incomplete"] = True
        rec["incomplete_reason"] = m.flag_detail.get("incomplete", "")
    return rec


def write_review(metrics, conn, outdir, data_name="metrics-review-data.json", ver=""):
    """Render boxed images + write the review JSON for `metrics` into `outdir`."""
    imgdir = os.path.join(outdir, IMGSUB)
    os.makedirs(imgdir, exist_ok=True)
    s3 = boto3.client("s3")
    pdfmap = {p.split("/")[-1][:8]: p for p in glob.glob(f"{imgdir}/*.pdf")}
    page_cache: dict = {}

    def getpdf(s8, full):
        dst = f"{imgdir}/{s8}.pdf"
        if os.path.exists(dst):
            return dst
        if s8 in pdfmap:
            shutil.copy(pdfmap[s8], dst); return dst
        try:
            f = tempfile.mkdtemp() + f"/{full}.pdf"
            s3.download_file(pc.S3_BUCKET, f"{pc.S3_PREFIX}{full}.pdf", f)
            shutil.copy(f, dst); return dst
        except Exception:
            return None

    def base_page(s8, full, pg):
        k = (s8, pg)
        if k in page_cache:
            return page_cache[k]
        png = f"{imgdir}/{s8}_p{pg}.png"
        if not os.path.exists(png):
            pdf = getpdf(s8, full)
            if pdf:
                subprocess.run(["pdftoppm", "-f", str(pg), "-l", str(pg), "-png", "-r", str(DPI),
                                "-singlefile", pdf, f"{imgdir}/{s8}_p{pg}"], capture_output=True, timeout=90)
        im = Image.open(png).convert("RGB") if os.path.exists(png) else None
        page_cache[k] = im
        return im

    # ensure every doc's full PDF is available, even docs with no resolved page (pdftotext-parsed
    # docs have no bbox/page -> no boxed image -> the full-report link is their only grounding).
    for sha8 in {m.sha8 for m in metrics}:
        full = conn.execute(text("SELECT content_sha256 FROM lava_parse.sections WHERE content_sha256 LIKE :p LIMIT 1"),
                            {"p": sha8 + "%"}).scalar() or sha8
        getpdf(sha8, full)

    records = []
    for m in metrics:
        full = conn.execute(text("SELECT content_sha256 FROM lava_parse.sections WHERE content_sha256 LIKE :p LIMIT 1"),
                            {"p": m.sha8 + "%"}).scalar() or m.content_sha256
        img = ""
        pg = m.prov.page
        if pg:
            base = base_page(m.sha8, full, pg)
            if base is not None:
                cv = base.copy(); drw = ImageDraw.Draw(cv); H = cv.size[1]
                if m.prov.value_bbox and m.prov.value_page == pg:
                    _draw(drw, m.prov.value_bbox, H, VAL, 7)
                if m.prov.subject_bbox and m.prov.subject_page == pg:
                    _draw(drw, m.prov.subject_bbox, H, SUB, 6)
                cv.thumbnail((1100, 99999))
                cv.save(f"{imgdir}/{m.sha8}_m{m.idx}.png", optimize=True)
                img = f"{IMGSUB}/{m.sha8}_m{m.idx}.png"
        records.append(_record(m, img, ver))

    json.dump(records, open(os.path.join(outdir, data_name), "w"), default=str)
    # ship the reused viewer alongside the data so the run is self-contained
    viewer = os.path.join(os.path.dirname(__file__), "..", "viewer", "review.html")
    if os.path.exists(viewer):
        shutil.copy(os.path.abspath(viewer), os.path.join(outdir, "review.html"))
    return records

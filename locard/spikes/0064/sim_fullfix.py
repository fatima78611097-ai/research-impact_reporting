"""0064 — FULL-FIX SIMULATION (real pipeline, existing prompt).

Hypothesis: the chunker linearizes designed pages in ROW-MAJOR order, reading
ACROSS columns and interleaving cards (why "47,155,255 IMPRESSIONS" repeats
before each email stat). Fix = COLUMN-MAJOR serialization from cell coordinates.

Test: extract cells+coords on CPU (docling_parse), serialize two ways, feed BOTH
to the REAL production extractor (call_deepseek + _METRICS_PROMPT, deepseek-chat,
temp 0). Only the cell ORDER differs. Compare the metrics that come back.
"""
import sys
import json
import httpx
sys.path.insert(0, "/home/ubuntu/research")
from docling_parse.pdf_parser import DoclingPdfParser
from lavandula.common.secrets import get_secret
from lavandula.nlp.llm_extract import call_deepseek, _METRICS_PROMPT, _MODEL

PDF = sys.argv[1]
COL_GAP = float(sys.argv[2]) if len(sys.argv) > 2 else 90.0   # x-center gap => new column
SHOW_PAGE = int(sys.argv[3]) if len(sys.argv) > 3 else None   # which page to display clustering for
ROW_TOL = 13.0   # cells within this vertical distance are the same line

parser = DoclingPdfParser()
doc = parser.load(PDF) if hasattr(parser, "load") else parser.load_document(PDF)
npages = doc.number_of_pages() if hasattr(doc, "number_of_pages") else len(doc)


def page_cells(seg):
    H = seg.dimension.height
    cells = getattr(seg, "textline_cells", None) or getattr(seg, "cells", [])
    out = []
    for c in cells:
        t = (c.text or "").strip()
        if not t:
            continue
        bb = c.rect.to_bounding_box()
        if hasattr(bb, "to_top_left_origin"):
            bb = bb.to_top_left_origin(page_height=H)
        l = float(getattr(bb, "l", 0)); r = float(getattr(bb, "r", 0))
        t0 = float(getattr(bb, "t", 0)); b0 = float(getattr(bb, "b", 0))
        top, bot = min(t0, b0), max(t0, b0)
        out.append({"text": t, "l": l, "r": r, "top": top, "bot": bot,
                    "xc": (l + r) / 2})
    return out


def to_lines(cells):
    """Group cells on ~same row into one line (left-to-right), top-to-bottom."""
    cells = sorted(cells, key=lambda c: (c["top"], c["l"]))
    lines, cur = [], []
    for c in cells:
        if cur and abs(c["top"] - cur[-1]["top"]) > ROW_TOL:
            cur.sort(key=lambda x: x["l"])
            lines.append(" ".join(x["text"] for x in cur)); cur = []
        cur.append(c)
    if cur:
        cur.sort(key=lambda x: x["l"])
        lines.append(" ".join(x["text"] for x in cur))
    return lines


def detect_columns(cells):
    """Split cells into columns by gaps in sorted x-centers."""
    cs = sorted(cells, key=lambda c: c["xc"])
    cols, cur = [], [cs[0]]
    for i in range(1, len(cs)):
        if cs[i]["xc"] - cs[i - 1]["xc"] > COL_GAP:
            cols.append(cur); cur = []
        cur.append(cs[i])
    cols.append(cur)
    return cols


def serialize(all_pages, mode):
    parts = []
    for pno, cells in all_pages:
        if not cells:
            continue
        if mode == "row":          # current: row-major (interleaves columns)
            parts.extend(to_lines(cells))
        else:                       # fix: column-major (each column top-to-bottom)
            for col in detect_columns(cells):
                parts.extend(to_lines(col))
                parts.append("")    # blank line between columns
    return "\n".join(parts)


all_pages = [(p, page_cells(doc.get_page(p))) for p in range(1, npages + 1)]

# ---- show the clustering on the infographic page (transparency) ----
print(f"PDF={PDF}  pages={npages}  COL_GAP={COL_GAP}")
if SHOW_PAGE:
    infop = next(pc for pc in all_pages if pc[0] == SHOW_PAGE)
else:
    infop = max(all_pages, key=lambda pc: len(pc[1]))
print(f"\n=== COLUMN-MAJOR clustering of page {infop[0]} ({len(infop[1])} cells) ===")
for ci, col in enumerate(detect_columns(infop[1])):
    print(f"  --- column {ci+1} ---")
    for ln in to_lines(col):
        print(f"    {ln}")

row_text = serialize(all_pages, "row")
col_text = serialize(all_pages, "col")

api_key = get_secret("lavandula/deepseek/api_key")


def extract(label, doc_text):
    with httpx.Client(headers={"Authorization": f"Bearer {api_key}"}) as hc:
        metrics, pt, ct = call_deepseek(api_key, doc_text, hc, _METRICS_PROMPT)
    if isinstance(metrics, dict):
        metrics = metrics.get("metrics", [])
    print(f"\n{'='*72}\n{label}  ({_MODEL}, {len(doc_text)} chars in -> {len(metrics)} metrics)\n{'='*72}")
    KEYS = ["deliver", "open", "click", "campaign", "email", "impression",
            "media", "social", "radio", "web ad", "print", " tv", "texting",
            "volunteer", "page view", "tax credit"]
    for m in metrics:
        blob = json.dumps(m).lower()
        mark = "  <<<" if any(k in blob for k in KEYS) else ""
        mt = m.get("metric_text") or m.get("metric") or ""
        snip = m.get("source_snippet", "")
        print(f"  [{m.get('metric_value', m.get('value',''))!s:>10}] {mt}{mark}")
        if mark:
            print(f"             snip: {snip!r}")
    return metrics


extract("CONDITION A — ROW-MAJOR (current linearization)", row_text)
extract("CONDITION B — COLUMN-MAJOR (the fix)", col_text)

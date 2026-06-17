"""0064 — shared coordinate-grouping helpers (CPU, docling_parse cells).
Row-major = current linearization; column-major = the grouping fix."""
import re
from docling_parse.pdf_parser import DoclingPdfParser

ROW_TOL = 13.0


def load_doc(pdf):
    parser = DoclingPdfParser()
    doc = parser.load(pdf) if hasattr(parser, "load") else parser.load_document(pdf)
    n = doc.number_of_pages() if hasattr(doc, "number_of_pages") else len(doc)
    return doc, n


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
        out.append({"text": t, "l": l, "r": r, "top": top, "bot": bot, "xc": (l + r) / 2})
    return out


def to_lines(cells):
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


_NUMCELL = re.compile(r"^[\$\(]?\s*-?[\d][\d,]*(?:\.\d+)?\s*%?[\)]?$")


def as_number(t):
    """Parse a cell/value string to float, or None. '$1,944' '100%' '(3,093.58)' -> number."""
    s = re.sub(r"[^\d.\-]", "", str(t))
    if s in ("", "-", ".", "-."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def is_num(t):
    """True if the cell is essentially a single number (not prose containing a number)."""
    return bool(_NUMCELL.match(t.strip())) and any(ch.isdigit() for ch in t)


def same_number(cell_text, value):
    a, b = as_number(cell_text), as_number(value)
    return a is not None and b is not None and abs(a - b) < 1e-6


def contested_bands(cells, spread_frac=0.20):
    """Designed-grid signal. Return bands of >=2 display-number cells sharing a y-row
    and spread across >=spread_frac of page width -- a row of numbers competing for a
    row of labels. A metric whose value matches a cell in such a band cannot be paired
    by row-major text and must be deferred to vision."""
    nums = [c for c in cells if is_num(c["text"])]
    if len(nums) < 2:
        return []
    pw = max((c["r"] for c in cells), default=600) - min((c["l"] for c in cells), default=0)
    med_h = sorted(c["bot"] - c["top"] for c in nums)[len(nums) // 2]
    tol = max(8.0, 0.6 * med_h)
    nums.sort(key=lambda c: c["top"])
    bands, cur = [], [nums[0]]
    for c in nums[1:]:
        if c["top"] - cur[-1]["top"] <= tol:
            cur.append(c)
        else:
            bands.append(cur); cur = [c]
    bands.append(cur)
    out = []
    for b in bands:
        if len(b) < 2:
            continue
        xs = sorted(c["xc"] for c in b)
        if (xs[-1] - xs[0]) > spread_frac * pw:
            out.append(sorted(b, key=lambda c: c["xc"]))
    return out


def grid_status(metric_value, bands_by_page):
    """Classify one metric against precomputed page bands.
    Returns (status, band_members_or_None). status in {'published','grid_ambiguous'}."""
    for _pg, bands in bands_by_page.items():
        for b in bands:
            if any(same_number(c["text"], metric_value) for c in b):
                return "grid_ambiguous", [c["text"] for c in b]
    return "published", None


def detect_columns(cells, col_gap):
    cs = sorted(cells, key=lambda c: c["xc"])
    cols, cur = [], [cs[0]]
    for i in range(1, len(cs)):
        if cs[i]["xc"] - cs[i - 1]["xc"] > col_gap:
            cols.append(cur); cur = []
        cur.append(cs[i])
    cols.append(cur)
    return cols


def serialize(all_pages, mode, col_gap=90.0):
    parts = []
    for _pno, cells in all_pages:
        if not cells:
            continue
        if mode == "row":
            parts.extend(to_lines(cells))
        else:
            for col in detect_columns(cells, col_gap):
                parts.extend(to_lines(col))
                parts.append("")
    return "\n".join(parts)


def all_pages_cells(doc, n):
    return [(p, page_cells(doc.get_page(p))) for p in range(1, n + 1)]

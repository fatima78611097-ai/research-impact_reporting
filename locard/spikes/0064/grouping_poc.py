"""0064 — PoC: can we recover visual grouping from CPU cell coordinates?
Extract page-13 textline cells of 001eb5f8 WITH bounding boxes (docling_parse,
no GPU) and test whether the infographic 'cards' are spatially separable.
If yes, the proposed coordinate-clustering fix is viable on CPU."""
import sys
sys.path.insert(0, "/home/ubuntu/research")
from docling_parse.pdf_parser import DoclingPdfParser

PDF = "pdfs/001eb5f86e4c833852f428e6200c21a937dd8b9204ae88b5b91b81a5b6d3dc76.pdf"
PAGE = 13

parser = DoclingPdfParser()
doc = parser.load(PDF) if hasattr(parser, "load") else parser.load_document(PDF)
seg = doc.get_page(PAGE)

cells = getattr(seg, "textline_cells", None) or getattr(seg, "cells", [])
print(f"page {PAGE}: {len(cells)} textline cells")
dim = getattr(seg, "dimension", None)
H = getattr(dim, "height", None); W = getattr(dim, "width", None)
print(f"page dim: width={W} height={H}")

# introspect one cell's geometry attributes
c0 = cells[0]
print("cell attrs:", [a for a in dir(c0) if not a.startswith("_")][:20])
rect = getattr(c0, "rect", None)
print("rect attrs:", [a for a in dir(rect) if not a.startswith("_")][:20] if rect else "no rect")


def box(cell):
    r = cell.rect
    # try common accessors
    for attr in ("to_bounding_box",):
        if hasattr(r, attr):
            bb = getattr(r, attr)()
            l = getattr(bb, "l", None); t = getattr(bb, "t", None)
            rr = getattr(bb, "r", None); b = getattr(bb, "b", None)
            return (l, t, rr, b)
    # fallback: r has x0/y0 or coord_origin points
    return tuple(getattr(r, k, None) for k in ("r_x0", "r_y0", "r_x1", "r_y1"))


rows = []
for cell in cells:
    t = (cell.text or "").strip()
    if not t:
        continue
    l, top, r, b = box(cell)
    rows.append((round(l or 0), round(top or 0), round(r or 0), round(b or 0), t))

# sort by vertical then horizontal (reading-ish), print with coords
rows.sort(key=lambda x: (x[1], x[0]))
print("\n=== page-13 cells (x0, y0, x1, y1) text ===")
for l, top, r, b, t in rows:
    print(f"  x0={l:>4} y0={top:>4} x1={r:>4} y1={b:>4}  | {t[:50]}")

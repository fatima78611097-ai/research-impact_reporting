"""0064 — DETERMINISTIC list/table header-attach (no LLM for this pattern).

For a ranked list / table: detect the label+value rows, find the section header
block above them (title + descriptor) from coordinates, and compose
metric_text = "<header title> — <label> <value>" with the row as the verbatim
source_snippet. Demonstrates that the list-header context can be attached without
the LLM (the header is a category, not a semantic connective).
"""
import re
import sys
sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, ".")
from grouping_lib import load_doc, page_cells, to_lines
from lavandula.faithfulness.grounding import check as gate_check
import glob

PDF = [p for p in glob.glob("pdfs/*.pdf") if "4d2d0f16" in p][0]
doc, n = load_doc(PDF)
seg = doc.get_page(13)
cells = page_cells(seg)
W, H = seg.dimension.width, seg.dimension.height
page_text = "\n".join(to_lines(cells))   # for grounding the row snippet

PCT = re.compile(r"\d[\d.,]*\s*%")
right = [c for c in cells if c["xc"] > 0.50 * W]

# value cells = the % column (right side of the panel)
values = [c for c in right if PCT.search(c["text"]) and c["xc"] > 0.80 * W]
values.sort(key=lambda c: c["top"])
first_row_top = values[0]["top"]
last_row_top = values[-1]["top"]

# header block = right-panel cells above the first row (title + descriptor)
header_cells = sorted([c for c in right if c["top"] < first_row_top - 0.01 * H],
                      key=lambda c: (c["top"], c["l"]))
title = " ".join(c["text"] for c in header_cells[:2]).strip()           # "3rd Grade Reading Levels"
descriptor = " ".join(c["text"] for c in header_cells[2:]).strip()
print(f"detected section TITLE: {title!r}")
print(f"detected DESCRIPTOR  : {descriptor[:90]!r}...\n")


def label_for(value_cell):
    """nearest non-% cell to the LEFT at the same row (y)."""
    cand = [c for c in right if not PCT.search(c["text"])
            and abs(c["top"] - value_cell["top"]) < 0.015 * H
            and c["xc"] < value_cell["xc"]]
    return min(cand, key=lambda c: value_cell["l"] - c["r"]) if cand else None


print(f"{'grounded':9} composed metric_text")
print("-" * 90)
for v in values:
    lab = label_for(v)
    if not lab:
        continue
    row = f"{lab['text']} {v['text']}".strip()
    composed = f"{title} — {row}"                 # deterministic header-attach
    g = getattr(gate_check(snippet=row, source_text=page_text, tables=[], value=None), "grounded", False)
    print(f"  [{'GND' if g else 'qan'}]    {composed}")

# also show the richer form using the descriptor's measure phrase
print("\n--- richer form (title + the measure phrase from the descriptor) ---")
measure = "achieved the MN reading standard"
for v in values[:3]:
    lab = label_for(v)
    if lab:
        print(f"  {v['text'].strip()} of children in {lab['text']} {measure} (3rd grade, 2018-2019)")

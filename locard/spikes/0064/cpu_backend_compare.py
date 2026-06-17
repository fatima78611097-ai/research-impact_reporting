"""Spec 0064 — free CPU backend comparison on the garble doc 7ce2c7fd.

Reproduces the two real PDF text decoders WITHOUT the GPU pipeline:
  - PRODUCTION: docling_parse (DoclingPdfParser) — what we run today.
  - CANDIDATE #1: pypdfium2 (PDFium) — the only public alternate backend.

Question: does PDFium decode "27,577" where docling_parse garbles it to "725,747"?
Pure text extraction, no layout/OCR models, no GPU.
"""
import sys
import re

PDF = sys.argv[1] if len(sys.argv) > 1 else \
    "pdfs/7ce2c7fdfec69d1a735b4feafb75f94430cbb2c24801ed8f2e0a6e8d1627e014.pdf"

TOKENS = (sys.argv[2].split("|") if len(sys.argv) > 2 else
          ["27,577", "27577", "725,747", "725747", "Patient Visit", "PATIENT",
           "2020 IMPACT", "IMPACT", "GLYPH"])
HIGHLIGHT = set(t for t in (sys.argv[3].split("|") if len(sys.argv) > 3 else
                            ["27,577", "27577", "Patient Visit"]))


def search(label, pages):
    """pages: dict page_no(1-based) -> text. Print where each token appears."""
    print(f"\n{'='*70}\n{label}\n{'='*70}")
    total_chars = sum(len(t) for t in pages.values())
    print(f"pages={len(pages)}  total_chars={total_chars}")
    for tok in TOKENS:
        hits = [pn for pn, t in pages.items() if tok.lower() in t.lower()]
        flag = "  <<<" if tok in HIGHLIGHT and hits else ""
        print(f"  {tok!r:18} -> pages {hits if hits else '—'}{flag}")
    # Dump a context window around the page containing a highlight token
    for pn, t in pages.items():
        if any(h.lower() in t.lower() for h in HIGHLIGHT):
            print(f"\n  --- page {pn} snippet (first 1200 chars) ---")
            print("  " + t[:1200].replace("\n", "\n  "))
            break


def via_pypdfium2(path):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(path)
    out = {}
    for i in range(len(pdf)):
        tp = pdf[i].get_textpage()
        out[i + 1] = tp.get_text_range()
    return out


def via_docling_parse(path):
    from docling_parse.pdf_parser import DoclingPdfParser
    parser = DoclingPdfParser()
    # load API varies by version; try the documented forms
    doc = None
    for loader in ("load", "load_document"):
        fn = getattr(parser, loader, None)
        if fn is None:
            continue
        for kw in ({"path_or_stream": path}, {"path": path}):
            try:
                doc = fn(**kw)
                break
            except TypeError:
                continue
        if doc is None:
            try:
                doc = fn(path)
            except Exception:
                doc = None
        if doc is not None:
            break
    if doc is None:
        raise RuntimeError("could not load via DoclingPdfParser; methods=" +
                           str([m for m in dir(parser) if not m.startswith('_')]))
    n = doc.number_of_pages() if hasattr(doc, "number_of_pages") else len(doc)
    out = {}
    for i in range(1, n + 1):
        seg = doc.get_page(i)
        cells = getattr(seg, "textline_cells", None) or getattr(seg, "cells", [])
        out[i] = "\n".join(getattr(c, "text", "") for c in cells)
    return out


print(f"PDF: {PDF}")
prod = via_docling_parse(PDF)
search("PRODUCTION decoder: docling_parse (DoclingPdfParser)", prod)
cand = via_pypdfium2(PDF)
search("CANDIDATE #1 decoder: pypdfium2 (PDFium)", cand)

# Persist both full texts for the before/after artifact
import os
pref = os.path.basename(PDF)[:8]
with open(f"evidence-{pref}-docling_parse.txt", "w") as f:
    for pn in sorted(prod):
        f.write(f"\n===== PAGE {pn} (docling_parse) =====\n{prod[pn]}\n")
with open(f"evidence-{pref}-pypdfium2.txt", "w") as f:
    for pn in sorted(cand):
        f.write(f"\n===== PAGE {pn} (pypdfium2) =====\n{cand[pn]}\n")
print(f"\nWrote evidence-{pref}-docling_parse.txt + evidence-{pref}-pypdfium2.txt")

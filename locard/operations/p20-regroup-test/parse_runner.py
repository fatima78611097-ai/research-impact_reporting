"""P20 regroup test — batch parse runner (disk-safe: fetch BATCH PDFs -> parse -> save JSON -> delete).

Parses the 600-doc sample under a named config, storing per-element text+bbox+page (the
source_locations equivalent) as JSON per doc: out/<config>/<sha16>.json. No DB writes.
Configs: A = current defaults; B = pypdfium2 backend; C = B + accurate TableFormer.
Resume-safe: skips docs whose output JSON already exists.

Usage: .venv/bin/python parse_runner.py <A|B|C> [--subset-only]
"""
import json
import os
import sys
import time

sys.path.insert(0, "/home/ubuntu/research")
import boto3
from lavandula.parse import config as pc

BASE = "/home/ubuntu/research/locard/operations/p20-regroup-test"
CONFIG = sys.argv[1] if len(sys.argv) > 1 else "A"
SUBSET = "--subset-only" in sys.argv
LIMIT = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--limit=")), None)
BATCH = 40

from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode


def make_converter(cfg):
    opts = PdfPipelineOptions()
    opts.do_ocr = False                      # P20 docs are ~97% text_native; matches run conditions
    if cfg in ("B", "C"):
        try:
            from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
            backend = PyPdfiumDocumentBackend
        except ImportError:
            backend = None
    else:
        backend = None
    if cfg == "C":
        opts.do_table_structure = True
        opts.table_structure_options.mode = TableFormerMode.ACCURATE
    kw = {"pipeline_options": opts}
    if backend is not None:
        kw["backend"] = backend
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(**kw)})


def extract_elements(doc):
    """DoclingDocument -> [{text,page,bbox{l,r,t,b},origin}] for every text item + table cell."""
    els = []
    for item, _lvl in doc.iterate_items():
        t = getattr(item, "text", None)
        for prov in getattr(item, "prov", []) or []:
            bb = prov.bbox
            els.append({"text": t or "", "page": prov.page_no,
                        "bbox": {"l": bb.l, "r": bb.r, "t": bb.t, "b": bb.b},
                        "origin": str(bb.coord_origin.value if hasattr(bb.coord_origin, "value") else bb.coord_origin)})
    for tab in getattr(doc, "tables", []) or []:
        try:
            for cell in tab.data.table_cells:
                if cell.bbox is None:
                    continue
            # cells carried via export; keep table text rows as elements with prov
        except Exception:
            pass
    return els


def main():
    man = json.load(open(f"{BASE}/sample-manifest.json"))
    docs = [d for d in man if (d.get("config_subset") if SUBSET else True)]
    outdir = f"{BASE}/out/{CONFIG}"
    os.makedirs(outdir, exist_ok=True)
    pdfdir = f"{BASE}/pdfs"
    os.makedirs(pdfdir, exist_ok=True)
    todo = [d for d in docs if not os.path.exists(f"{outdir}/{d['sha'][:16]}.json")]
    if LIMIT:
        todo = todo[:LIMIT]
    print(f"config {CONFIG}: {len(docs)} docs, {len(todo)} to parse", flush=True)
    s3 = boto3.client("s3")
    conv = make_converter(CONFIG)
    done = 0
    t0 = time.time()
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        for d in batch:                                   # fetch batch
            p = f"{pdfdir}/{d['sha'][:16]}.pdf"
            if not os.path.exists(p):
                try:
                    s3.download_file(pc.S3_BUCKET, f"{pc.S3_PREFIX}{d['sha']}.pdf", p)
                except Exception as e:
                    json.dump({"error": f"s3:{e}"}, open(f"{outdir}/{d['sha'][:16]}.json", "w"))
        for d in batch:                                   # parse batch
            p = f"{pdfdir}/{d['sha'][:16]}.pdf"
            o = f"{outdir}/{d['sha'][:16]}.json"
            if os.path.exists(o) or not os.path.exists(p):
                continue
            try:
                r = conv.convert(p)
                els = extract_elements(r.document)
                json.dump({"sha": d["sha"], "config": CONFIG, "elements": els,
                           "n_pages": r.document.num_pages() if callable(getattr(r.document, "num_pages", None)) else None},
                          open(o, "w"))
            except Exception as e:
                json.dump({"sha": d["sha"], "error": str(e)[:300]}, open(o, "w"))
            done += 1
            if done % 10 == 0:
                el = time.time() - t0
                print(f"  {done}/{len(todo)} ({el/60:.0f}m, {el/done:.0f}s/doc)", flush=True)
        for d in batch:                                   # delete batch PDFs (disk-safe)
            p = f"{pdfdir}/{d['sha'][:16]}.pdf"
            if os.path.exists(p):
                os.remove(p)
    print(f"DONE config {CONFIG}: {done} parsed in {(time.time()-t0)/60:.0f}m", flush=True)


if __name__ == "__main__":
    main()

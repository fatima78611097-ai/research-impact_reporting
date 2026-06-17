"""Run the advisor's KPI detector over every page of the given PDFs and print
lane + score + signals. Validates against known cases."""
import sys
sys.path.insert(0, ".")
from grouping_lib import load_doc, page_cells
from kpi_detector import classify_page

NOTES = {
    ("001eb5f8", 13): "TARGET: EMAIL CAMPAIGN clean card grid (fix WORKS here)",
    ("7ce2c7fd", 4): "garble infographic (27,577) — designed KPI page",
}

for pdf in sys.argv[1:]:
    doc, n = load_doc(pdf)
    sha8 = pdf.split("/")[-1][:8]
    print(f"\n{'#'*78}\n# {sha8}  ({n} pages)\n{'#'*78}")
    print(f"{'pg':>3} {'lane':18} {'sc':>3} {'wl':>3} | metr card hAdj tbl gfrc | reasons")
    for p in range(1, n + 1):
        seg = doc.get_page(p)
        cells = page_cells(seg)
        if not cells:
            continue
        r = classify_page(cells, seg.dimension.width, seg.dimension.height)
        note = NOTES.get((sha8, p), "")
        flag = " <<<APPLY" if r["lane"] == "APPLY_regrouping" else ("  ~log" if "LOG" in r["lane"] else "")
        print(f"{p:>3} {r['lane']:18} {r['score']:>3} {str(r['whitelist'])[:1]:>3} | "
              f"{r['n_metrics']:>4} {r['n_cards']:>4} {r['head_adj']:>4} "
              f"{str(r['table_like'])[:1]:>3} {r['gfrac']:>4} | {','.join(r['reason_codes'])}{flag}")
        if note:
            print(f"      ^^^ {note}")

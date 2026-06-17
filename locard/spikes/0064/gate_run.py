"""0064 — run the try-and-measure gate across all pages of all docs in pdfs/.
Outputs gate_run.csv + a summary (decision distribution, accepted-page recoveries,
and agreement with the human eval labels where available)."""
import csv
import glob
import os
from collections import Counter
import httpx
import sys
sys.path.insert(0, ".")
from regroup_gate_spatial import gate_page, load_doc

PDFS = sorted(glob.glob("pdfs/*.pdf"))
KEYS = ["cand", "decision", "base_g", "base_n", "kept", "rec", "enr", "lost"]
rows = []
with httpx.Client(timeout=120) as client:
    for pdf in PDFS:
        sha8 = os.path.basename(pdf)[:8]
        doc, n = load_doc(pdf)
        print(f"{sha8}: {n} pages", flush=True)
        for p in range(1, n + 1):
            try:
                r = gate_page(client, doc, p)
            except Exception as e:
                print(f"  p{p} ERR {type(e).__name__}", flush=True); continue
            if not r:
                continue
            rows.append({"doc": sha8, "page": p, **{k: r.get(k) for k in KEYS},
                         "rec_ex": " | ".join(r.get("rec_examples", []) or []),
                         "enr_ex": " | ".join(r.get("enr_examples", []) or [])})

with open("eval_set/gate_run.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["doc", "page"] + KEYS + ["rec_ex", "enr_ex"])
    w.writeheader(); w.writerows(rows)

dec = Counter(r["decision"] for r in rows)
cand = sum(1 for r in rows if r["cand"] == "yes")
print(f"\n=== SUMMARY: {len(rows)} pages across {len(PDFS)} docs ===")
print(f"  candidates (broken baseline): {cand}  ({cand/len(rows):.0%})")
print(f"  decisions: {dict(dec)}")
print(f"\n=== ACCEPTED pages — recoveries/enrichments for human review ===")
for r in rows:
    if r["decision"] == "ACCEPT":
        ex = (r["rec_ex"] + (" || " if r["rec_ex"] and r["enr_ex"] else "") + r["enr_ex"])[:200]
        print(f"  {r['doc']} p{r['page']}: +{r['rec']}rec +{r['enr']}enr  {ex}")

# agreement with human labels (for the 8 originally-labeled docs)
try:
    gt = {(x["doc"], int(x["page"])): x for x in csv.DictReader(open("eval_set/eval_labels.csv"))}
    print("\n=== gate decision vs human regroup label (labeled docs only) ===")
    tab = Counter()
    for r in rows:
        g = gt.get((r["doc"], r["page"]))
        if not g:
            continue
        tab[(g["true_regroup"], r["decision"])] += 1
    for (hr, dec_), c in sorted(tab.items()):
        print(f"  human_regroup={hr:3}  gate={dec_:14} : {c}")
except Exception as e:
    print("label compare skipped:", e)

"""Decisive test: run the DeepSeek layout-gate on all 97 labeled pages, compare
its regroup decision to human ground truth + to the heuristic. Parses each doc
once. Output: per-page preds + precision/recall, incl. the hybrid (heuristic
candidate AND deepseek says regroup)."""
import csv
import glob
import json
import sys
import httpx
sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, ".")
from grouping_lib import load_doc, page_cells
from deepseek_layout_classify import PROMPT, layout_text
from lavandula.common.secrets import get_secret

KEY = get_secret("lavandula/deepseek/api_key")
MODEL = "deepseek-chat"
PATH = {p.split("/")[-1][:8]: p for p in glob.glob("pdfs/*.pdf")}
labels = list(csv.DictReader(open("eval_set/eval_labels.csv")))

by_doc = {}
for r in labels:
    by_doc.setdefault(r["doc"], []).append(r)

preds = {}
with httpx.Client(timeout=90) as client:
    for sha8, rows in by_doc.items():
        doc, n = load_doc(PATH[sha8])
        print(f"  {sha8}: classifying {len(rows)} pages", flush=True)
        for r in rows:
            seg = doc.get_page(int(r["page"]))
            cells = page_cells(seg)
            body = layout_text(cells[:170], seg.dimension.width, seg.dimension.height)
            try:
                resp = client.post("https://api.deepseek.com/v1/chat/completions",
                                   headers={"Authorization": f"Bearer {KEY}"},
                                   json={"model": MODEL, "temperature": 0.0, "max_tokens": 120,
                                         "messages": [{"role": "system", "content": PROMPT},
                                                      {"role": "user", "content": body}]})
                t = resp.json()["choices"][0]["message"]["content"].strip()
                if t.startswith("```"):
                    t = t.split("```")[1].lstrip("json").strip()
                v = json.loads(t)
            except Exception as e:
                v = {"class": "?", "regroup": "?", "reason": str(e)[:40]}
            preds[(r["doc"], r["page"])] = v

# write preds + compute metrics
out = []
for r in labels:
    v = preds.get((r["doc"], r["page"]), {})
    out.append({**r, "ds_class": v.get("class", "?"), "ds_regroup": v.get("regroup", "?")})
with open("eval_set/deepseek_preds.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)


def metrics(name, predicate):
    tp = sum(1 for r in out if predicate(r) and r["true_regroup"] == "yes")
    fp = sum(1 for r in out if predicate(r) and r["true_regroup"] == "no")
    fn = sum(1 for r in out if not predicate(r) and r["true_regroup"] == "yes")
    p = tp / (tp + fp) if tp + fp else 0
    rc = tp / (tp + fn) if tp + fn else 0
    print(f"  {name:38} TP={tp} FP={fp} FN={fn}  precision={p:.0%} recall={rc:.0%}")

print("\n=== regroup decision: precision/recall vs human ground truth ===")
metrics("HEURISTIC (pred_lane==APPLY)", lambda r: r["pred_lane"] == "APPLY_regrouping")
metrics("DEEPSEEK gate (ds_regroup==yes)", lambda r: r["ds_regroup"] == "yes")
metrics("HYBRID (heuristic APPLY & ds yes)",
        lambda r: r["pred_lane"] == "APPLY_regrouping" and r["ds_regroup"] == "yes")
print("\n=== DeepSeek class agreement with human (exact) ===")
exact = sum(1 for r in out if r["ds_class"] == r["true_class"])
print(f"  exact class match: {exact}/{len(out)} = {exact/len(out):.0%}")
# confusion on the regroup-positive pages
print("=== where DeepSeek says regroup=yes (its candidates) ===")
for r in out:
    if r["ds_regroup"] == "yes":
        flag = "OK" if r["true_regroup"] == "yes" else "FP"
        print(f"  [{flag}] {r['doc']} p{r['page']}: ds={r['ds_class']} truth={r['true_class']}")

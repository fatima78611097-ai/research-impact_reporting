"""Assemble the Qwen eval set: pages + ground-truth (value -> true caption) pairs.

Truth sources (all page-truth, from Opus adjudications):
  - p20-flag:      113 adjudicated flags  -> true_caption per value (all verdicts carry it)
  - p20-recovery:  55 RECOVER_YES         -> truth = the metric's own claim (page-confirmed)
  - dev-adjudicated: 13 dev disputed cases -> true_label (render CLEAN pages; reviewbox PNGs have
    red/blue boxes drawn which would leak hints)
Output: qwen_eval/eval-set.json + qwen_eval/pages/*.png (clean renders).
"""
import json
import os
import re
import subprocess
import sys

B = "/home/ubuntu/research/locard/operations/p20-regroup-test"
Q = f"{B}/qwen_eval"
VIS = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision"
os.makedirs(f"{Q}/pages", exist_ok=True)

items = []

# --- P20 flags (true_caption from adjudication) ---
adj = json.load(open(f"{B}/p20-adjudication-result.json"))["result"]
flags = {f["metric_id"]: f for f in json.load(open(f"{B}/regroup-p20-flags.json"))}
recs = {r["metric_id"]: r for r in json.load(open(f"{B}/regroup-p20-recovery-sample.json"))}
for v in adj["flag_detail"]:
    src = flags.get(v["metric_id"])
    if not src or v["verdict"] == "VALUE_NOT_FOUND" or not v.get("true_caption"):
        continue
    png = f"{B}/adj_pages/{src['sha'][:16]}_p{int(src['page'])}.png"
    if not os.path.exists(png):
        continue
    items.append({"source": "p20-flag", "img": png, "value": src["value"],
                  "truth": v["true_caption"], "verdict_ctx": v["verdict"]})
for v in adj["recovery_detail"]:
    if v["verdict"] != "RECOVER_YES":
        continue
    src = recs.get(v["metric_id"])
    if not src:
        continue
    png = f"{B}/adj_pages/{src['sha'][:16]}_p{int(src['page'])}.png"
    if not os.path.exists(png):
        continue
    items.append({"source": "p20-recovery", "img": png, "value": src["value"],
                  "truth": src["metric_text"], "verdict_ctx": "RECOVER_YES"})

# --- dev adjudicated (render clean pages from reviewbox PDFs) ---
dev = json.load(open("/home/ubuntu/research/locard/operations/comp-metric-regression/regroup-adjudication-result.json"))["result"]
review = {r["id"]: r for r in json.load(open(VIS + "/review-data.json"))}
for v in dev:
    r = review.get(v["id"])
    if not r or not v.get("true_label"):
        continue
    pg = r.get("v_page") or r.get("page")
    pdf = f"{VIS}/reviewbox/{r['sha8']}.pdf"
    if not pg or not os.path.exists(pdf):
        continue
    out = f"{Q}/pages/{r['sha8']}_p{pg}"
    if not os.path.exists(out + ".png"):
        subprocess.run(["pdftoppm", "-png", "-r", "130", "-f", str(pg), "-l", str(pg),
                        "-singlefile", pdf, out], check=False)
    if os.path.exists(out + ".png"):
        items.append({"source": "dev-adjudicated", "img": out + ".png", "value": str(r.get("value")),
                      "truth": v["true_label"], "verdict_ctx": v["verdict"]})

# copy P20 pngs referenced into the bundle dir for a self-contained tarball
import shutil
for it in items:
    if it["img"].startswith(f"{B}/adj_pages/"):
        dst = f"{Q}/pages/{os.path.basename(it['img'])}"
        if not os.path.exists(dst):
            shutil.copy(it["img"], dst)
        it["img"] = f"pages/{os.path.basename(it['img'])}"
    else:
        it["img"] = f"pages/{os.path.basename(it['img'])}"

json.dump(items, open(f"{Q}/eval-set.json", "w"), indent=1)
import collections
print(f"eval items: {len(items)} | {dict(collections.Counter(i['source'] for i in items))}")
print(f"distinct pages: {len({i['img'] for i in items})}")

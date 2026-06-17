"""RECALL check v2 — adds LABEL agreement (review fix: value-only CAPTURED overstates capture).
Buckets each vision-featured number:
  CAPTURED              value in our metrics AND label agrees
  CAPTURED_MISLABELED   value in our metrics but label disagrees (we have the number, wrong thing)
  PARSED_NOT_EXTRACTED  parser read the number but we made no metric
  NOT_PARSED            parser never read it (true image-only gap) -- reliable regardless of labels
Reuses recall_check helpers; vision = gemini-2.5-flash-lite (locked tier).
"""
import sys, json, tempfile, os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/p20-regroup-test")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression/bake-off")
import recall_check as rc
from regroup import labels_agree
import vision_judge as vj

rc.PAGE_CAP = 20
N_DOCS = int(sys.argv[1]) if len(sys.argv) > 1 else 40
BASE = rc.BASE
os.makedirs(f"{BASE}/scratch", exist_ok=True)

man = json.load(open(f"{BASE}/sample-manifest.json"))
docs = [man[i] for i in range(0, len(man), max(1, len(man) // N_DOCS))][:N_DOCS]
cur = json.load(open(f"{BASE}/new-metrics-current.json"))
ours = {}
for d in cur:
    mp = {}
    for m in d["metrics"]:
        nk = rc.numkey(m.get("metric_value"))
        lab = ((m.get("metric_type") or "") + " " + (m.get("metric_text") or "")).strip()
        mp.setdefault(nk, []).append(lab)
    ours[d["sha"]] = mp

key = vj.gemini_key()
tmp = tempfile.mkdtemp(prefix="recallv2_", dir=f"{BASE}/scratch")
print(f"recall v2 (label-match) on {len(docs)} docs, page_cap={rc.PAGE_CAP}", flush=True)


def work(d):
    sha = d["sha"]; npages, _, parsed_nums = rc.doc_pages(sha[:16])
    rec = {"sha": sha, "stratum": d.get("stratum"), "featured": [], "err": None}
    if not npages:
        rec["err"] = "no_parse"; return rec
    try:
        pngs = rc.render_all(sha, npages, tmp)
        seen = {}
        for i, png in enumerate(pngs, 1):
            cap, _, _ = vj.gemini("gemini-2.5-flash-lite", rc.FEATURED_PROMPT, png, key, temperature=0.0)
            if os.path.exists(png):
                os.remove(png)
            for nk, raw, lab in rc.parse_featured(cap):
                seen.setdefault(nk, (raw, lab, i))
        omap = ours.get(sha, {})
        for nk, (raw, lab, pg) in seen.items():
            if nk in omap:
                bucket = "CAPTURED" if any(labels_agree(lab, ol) for ol in omap[nk]) else "CAPTURED_MISLABELED"
            elif nk in parsed_nums:
                bucket = "PARSED_NOT_EXTRACTED"
            else:
                bucket = "NOT_PARSED"
            rec["featured"].append({"num": raw, "label": lab, "page": pg, "bucket": bucket})
    except Exception as e:
        rec["err"] = f"{type(e).__name__}: {str(e)[:80]}"
    return rec


results = []
with ThreadPoolExecutor(max_workers=5) as ex:
    for i, o in enumerate(ex.map(work, docs), 1):
        results.append(o)
        print(f"  {i}/{len(docs)} {o['sha'][:8]} featured={len(o['featured'])} err={o['err']}", flush=True)

json.dump(results, open(f"{BASE}/recall-check-v2.json", "w"), indent=1)
feats = [f for r in results for f in r["featured"]]
b = Counter(f["bucket"] for f in feats); tot = len(feats)
errs = sum(1 for r in results if r["err"])
print(f"\ndocs with parse/render errors: {errs}/{len(results)}")
print(f"featured callout numbers seen by vision: {tot}")
for k in ("CAPTURED", "CAPTURED_MISLABELED", "PARSED_NOT_EXTRACTED", "NOT_PARSED"):
    print(f"  {k:22}: {b[k]:4} ({100*b[k]/max(1,tot):.1f}%)")
print(f"\n=> IMAGE-ONLY gap (NOT_PARSED): {100*b['NOT_PARSED']/max(1,tot):.1f}%"
      f"  |  captured-but-MISLABELED: {100*b['CAPTURED_MISLABELED']/max(1,tot):.1f}%"
      f"  |  truly captured: {100*b['CAPTURED']/max(1,tot):.1f}%")

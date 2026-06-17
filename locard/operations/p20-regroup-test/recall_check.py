"""RECALL check: do the featured numbers in infographics end up in our metrics?
For a sample of docs: have vision list the prominently FEATURED callout numbers on each page, then
bucket each one:
  CAPTURED              -> the number is in our extracted metrics for that doc
  PARSED_NOT_EXTRACTED  -> parser read the number (it's in the text) but we made no metric from it
  NOT_PARSED            -> parser never read it (the true 'baked into a picture' case)
This is the recall direction: visible-in-infographic vs in-our-metrics.
"""
import argparse, glob, json, os, re, subprocess, sys, tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression/bake-off")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/p20-regroup-test")
import vision_judge as vj
from lavandula.parse import config as pc
import boto3
BASE = "/home/ubuntu/research/locard/operations/p20-regroup-test"
PAGE_CAP = 8
N_DOCS = int(sys.argv[1]) if len(sys.argv) > 1 else 40

FEATURED_PROMPT = (
    "This is a page from a nonprofit annual/impact report. List ONLY the prominently FEATURED numbers "
    "— big stat callouts, highlighted figures, large numbers inside graphics/infographics (not body-text "
    "asides, not page numbers, not dates). For each, one line exactly as:  NUMBER | what it measures (2-6 words). "
    "If there are no featured callout numbers on this page, reply exactly NONE.")


def numkey(v):
    s = str(v or "").strip()
    try:
        f = float(s.replace(",", "").replace("$", "").replace("%", ""))
        if f == int(f):
            return str(int(f))
    except ValueError:
        pass
    return re.sub(r"[^\d]", "", s)


def doc_pages(sha16):
    f = f"{BASE}/out/A/{sha16}.json"
    if not os.path.exists(f):
        return 0, "", set()
    d = json.load(open(f))
    if "error" in d:
        return 0, "", set()
    npages = d.get("n_pages") or max((e["page"] for e in d.get("elements", [])), default=1)
    txt = " ".join(e.get("text", "") for e in d["elements"])
    parsed_nums = {numkey(t) for t in re.findall(r"\d[\d.,]*", txt)}
    parsed_nums.discard("")
    return npages, txt, parsed_nums


def render_all(sha, npages, tmp):
    pdf = f"{tmp}/{sha[:16]}.pdf"
    boto3.client("s3", region_name="us-east-1").download_file(pc.S3_BUCKET, f"{pc.S3_PREFIX}{sha}.pdf", pdf)
    base = f"{tmp}/{sha[:16]}"
    subprocess.run(["pdftoppm", "-png", "-r", "150", "-l", str(min(npages, PAGE_CAP)), pdf, base],
                   capture_output=True, timeout=300)
    os.remove(pdf)
    pngs = glob.glob(f"{base}-*.png")
    pngs.sort(key=lambda p: int(re.search(r"-(\d+)\.png$", p).group(1)))
    return pngs


def parse_featured(txt):
    out = []
    for ln in (txt or "").splitlines():
        if "|" not in ln:
            continue
        num, _, lab = ln.partition("|")
        nk = numkey(num)
        if nk and len(nk) >= 1:
            out.append((nk, num.strip(), lab.strip()[:40]))
    return out


def main():
    man = json.load(open(f"{BASE}/sample-manifest.json"))
    # deterministic spread across strata
    docs = [man[i] for i in range(0, len(man), max(1, len(man) // N_DOCS))][:N_DOCS]
    # our captured metrics per doc (current-prompt set)
    cur = json.load(open(f"{BASE}/new-metrics-current.json"))
    ours = {d["sha"]: {numkey(m.get("metric_value")) for m in d["metrics"]} for d in cur}
    key = vj.gemini_key()
    tmp = tempfile.mkdtemp(prefix="recall_", dir=f"{BASE}/scratch")
    print(f"recall check on {len(docs)} docs", flush=True)

    def work(d):
        sha = d["sha"]; npages, _, parsed_nums = doc_pages(sha[:16])
        rec = {"sha": sha, "stratum": d["stratum"], "featured": [], "err": None}
        if not npages:
            rec["err"] = "no_parse"; return rec
        try:
            pngs = render_all(sha, npages, tmp)
            seen = {}
            for i, png in enumerate(pngs, 1):
                cap, _, _ = vj.gemini("gemini-2.5-flash-lite", FEATURED_PROMPT, png, key, temperature=0.0)
                if os.path.exists(png):
                    os.remove(png)
                for nk, raw, lab in parse_featured(cap):
                    seen.setdefault(nk, (raw, lab, i))  # first page it was featured on
            our_vals = ours.get(sha, set())
            for nk, (raw, lab, pg) in seen.items():
                bucket = ("CAPTURED" if nk in our_vals
                          else "PARSED_NOT_EXTRACTED" if nk in parsed_nums
                          else "NOT_PARSED")
                rec["featured"].append({"num": raw, "label": lab, "page": pg, "bucket": bucket})
        except Exception as e:
            rec["err"] = type(e).__name__
        return rec

    results = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        for i, o in enumerate(ex.map(work, docs), 1):
            results.append(o)
            print(f"  {i}/{len(docs)} {o['sha'][:8]} featured={len(o['featured'])} err={o['err']}", flush=True)

    json.dump(results, open(f"{BASE}/recall-check.json", "w"), indent=1)
    feats = [f for r in results for f in r["featured"]]
    b = Counter(f["bucket"] for f in feats)
    tot = len(feats)
    print(f"\nfeatured callout numbers seen by vision: {tot}")
    for k in ("CAPTURED", "PARSED_NOT_EXTRACTED", "NOT_PARSED"):
        print(f"  {k:22}: {b[k]:4}  ({100*b[k]/max(1,tot):.1f}%)")
    print(f"\n=> in our metrics: {100*b['CAPTURED']/max(1,tot):.1f}%  |  "
          f"parser missed entirely (true picture gap): {100*b['NOT_PARSED']/max(1,tot):.1f}%")


if __name__ == "__main__":
    main()

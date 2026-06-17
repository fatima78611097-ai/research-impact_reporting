"""Vision-scan fallback: for metrics neither locator could place, render the whole doc and have
Flash-Lite hunt for that specific number across pages (VERIFIER role, one known metric at a time).
Outcome per metric:
  CONFIRMED            vision found the value on some page labeled consistently with the metric -> publishable
  REFUTED              vision found the value but labeled differently everywhere -> real error -> quarantine
  NOT_FOUND_BY_VISION  vision can't find the value anywhere -> quarantine (irreducible)
Conservative: only CONFIRMED is recoverable; everything else stays quarantined (fail-safe).
"""
import argparse, glob, json, os, re, sys, subprocess, tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression/bake-off")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/p20-regroup-test")
import regroup
import vision_judge as vj
from lavandula.parse import config as pc
import boto3
BASE = "/home/ubuntu/research/locard/operations/p20-regroup-test"
PAGE_CAP = 25

# BLIND: never show vision our label. It independently reports what the number counts; if the number
# isn't really on the page it must say NOT_FOUND (kills the cover-page echo). We then compare ITS
# caption to our label by distinctive-word overlap (kills the parrot + the lenient match==CORRECT).
BLIND_PROMPT = ("On this page from a nonprofit report, find the number {val}. What does that exact "
                "number count or measure? Reply with ONLY the short caption as printed (a few words). "
                "If the number {val} does not actually appear on this page, reply exactly NOT_FOUND.")


def blind_caption(img, val, key):
    txt, _, _ = vj.gemini("gemini-2.5-flash-lite", BLIND_PROMPT.format(val=val), img, key, temperature=0.0)
    return (txt or "").strip()


def doc_pages(sha16):
    f = f"{BASE}/out/A/{sha16}.json"
    if os.path.exists(f):
        d = json.load(open(f))
        if d.get("n_pages"):
            return d["n_pages"]
        return max((e["page"] for e in d.get("elements", [])), default=1)
    return 1


def render_all(sha, npages, tmp, uniq):
    pdf = f"{tmp}/{sha[:16]}_{uniq}.pdf"  # unique per metric: same-doc metrics don't collide
    boto3.client("s3", region_name="us-east-1").download_file(
        pc.S3_BUCKET, f"{pc.S3_PREFIX}{sha}.pdf", pdf)
    base = f"{tmp}/{sha[:16]}_{uniq}"
    subprocess.run(["pdftoppm", "-png", "-r", "150", "-l", str(min(npages, PAGE_CAP)), pdf, base],
                   capture_output=True, timeout=300)
    os.remove(pdf)
    pngs = glob.glob(f"{base}-*.png")
    pngs.sort(key=lambda p: int(re.search(r"-(\d+)\.png$", p).group(1)))
    return pngs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--judged", default=f"{BASE}/vision-judge-unloc-current.json")
    args = ap.parse_args()
    rows = [r for r in json.load(open(args.judged)) if r["score"] == "STILL_UNLOC"]
    key = vj.gemini_key()
    tmp = tempfile.mkdtemp(prefix="scan_", dir=f"{BASE}/scratch")
    print(f"vision-scanning {len(rows)} still-unverifiable metrics", flush=True)

    def scan(r):
        out = dict(r); sha = r["sha"]
        try:
            pngs = render_all(sha, doc_pages(sha[:16]), tmp, r["id"])
            found = []
            for i, png in enumerate(pngs, 1):
                cap = blind_caption(png, r["value"], key)
                if os.path.exists(png):
                    os.remove(png)
                if cap and not cap.upper().startswith("NOT_FOUND"):
                    found.append((i, cap))
            if not found:
                out["outcome"] = "NOT_FOUND_BY_VISION"           # value isn't on any page
            else:
                agree_on = [(p, c) for p, c in found if regroup.labels_agree(c, r["metric_text"])]
                out["outcome"] = "CONFIRMED" if agree_on else "REFUTED"
                out["agree_on"] = [{"page": p, "caption": c[:90]} for p, c in agree_on[:5]]
            out["found"] = [{"page": p, "counts": c[:60]} for p, c in found[:8]]
        except Exception as e:
            out["outcome"] = f"ERR:{type(e).__name__}"
        return out

    results = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        for i, o in enumerate(ex.map(scan, rows), 1):
            results.append(o)
            print(f"  {i}/{len(rows)} {o['sha'][:8]} val={o['value']:>9} -> {o['outcome']}", flush=True)

    json.dump(results, open(f"{BASE}/vision-scan-result.json", "w"), indent=1)
    sc = Counter(r["outcome"] for r in results)
    print(f"\noutcomes: {dict(sc)}")
    resolved = sc["CONFIRMED"] + sc["REFUTED"]
    print(f"resolved by vision-scan: {resolved}/{len(rows)} "
          f"(CONFIRMED {sc['CONFIRMED']} recoverable, REFUTED {sc['REFUTED']} real-error->quarantine)")
    print(f"irreducible (vision still can't find): {sc['NOT_FOUND_BY_VISION']}/{len(rows)}")


if __name__ == "__main__":
    main()

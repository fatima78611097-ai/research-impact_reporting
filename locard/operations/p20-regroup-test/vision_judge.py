"""Independent vision judge of the gated metric set (Flash-Lite, BLIND to the claimed label).

For each sampled metric: render its page, ask Flash-Lite "what does number X count on this page?"
(it never sees our label), then compare vision's caption to what we shipped.
  CORRECT     vision's caption shares distinctive words with the shipped label
  WRONG       vision's caption is a different thing  -> a real error in the shipped set
  NOT_ON_PAGE vision can't find the number           -> unverifiable here (reported separately)

Two samples:
  --mode ship : stratified random sample of the SHIP set -> headline precision + Wilson CI
  --mode flag : all FLAGs, scored vs BOTH deepseek label and regroup label -> who's right
Run with p20 venv. Writes vision-judge-<mode>.json.
"""
import argparse, json, os, re, subprocess, sys, tempfile, math, random
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression/bake-off")
import regroup
from run_bakeoff import gemini, _key as gemini_key
from lavandula.parse import config as pc
BASE = "/home/ubuntu/research/locard/operations/p20-regroup-test"
random.seed(42)  # reproducible sample (no Math.random pitfalls)

JUDGE = ('On this page from a nonprofit report, find the number {val}. '
         'A label claims it counts: "{label}". Reply ONLY JSON: '
         '{{"counts": "<what {val} actually counts on this page, a few words>", '
         '"match": "CORRECT" if the claimed label means essentially the same thing, '
         '"WRONG" if {val} counts something clearly different, '
         'or "NOT_ON_PAGE" if {val} is not on this page}}.')


def numkey(v):
    s = str(v or "").strip()
    try:
        f = float(s.replace(",", "").replace("$", "").replace("%", ""))
        if f == int(f):
            return str(int(f))
    except ValueError:
        pass
    return re.sub(r"[^\d]", "", s)


def find_page(sha16, vk):
    """Page where the value appears as a WHOLE number token (numkey match), and ONLY if that
    resolves to a single page. Substring matching ('24' in '244') and multi-page values are
    excluded as unlocatable -> judged NO_PAGE, never scored WRONG."""
    f = f"{BASE}/out/A/{sha16}.json"
    if not os.path.exists(f) or not vk:
        return None
    d = json.load(open(f))
    if "error" in d:
        return None
    pages = set()
    for e in d["elements"]:
        for tok in re.findall(r"\d[\d.,]*", e["text"]):
            if numkey(tok) == vk:
                pages.add(e["page"])
    return next(iter(pages)) if len(pages) == 1 else None


def render(sha, page, tmp):
    """Download PDF, render one page, DELETE the PDF immediately (disk-safe). Returns png path."""
    import boto3
    pdf = f"{tmp}/{sha[:16]}_{page}.pdf"
    try:
        boto3.client("s3", region_name="us-east-1").download_file(
            pc.S3_BUCKET, f"{pc.S3_PREFIX}{sha}.pdf", pdf)
    except Exception:
        return None
    base = f"{tmp}/{sha[:16]}_p{page}"
    subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-png", "-r", "150",
                    "-singlefile", pdf, base], capture_output=True, timeout=120)
    if os.path.exists(pdf):
        os.remove(pdf)
    return f"{base}.png" if os.path.exists(f"{base}.png") else None


def vision_judge_call(img, val, label, key):
    """Returns (counts, match) — vision's independent caption + its CORRECT/WRONG/NOT_ON_PAGE rule."""
    txt, _, _ = gemini("gemini-2.5-flash-lite", JUDGE.format(val=val, label=label or ""),
                       img, key, temperature=0.0)
    try:
        j = json.loads(re.search(r"\{.*\}", txt or "", re.S).group(0))
        m = str(j.get("match", "")).upper()
        m = m if m in ("CORRECT", "WRONG", "NOT_ON_PAGE") else "PARSE_FAIL"
        return str(j.get("counts", ""))[:120], m
    except Exception:
        return (txt or "")[:120], "PARSE_FAIL"


def wilson(k, n):
    if n == 0:
        return (0, 0, 0)
    p = k / n; z = 1.96
    d = 1 + z*z/n
    c = (p + z*z/(2*n)) / d
    h = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / d
    return (p, max(0, c-h), min(1, c+h))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["ship", "flag"], default="ship")
    ap.add_argument("--n", type=int, default=210)  # stratified ship sample size
    ap.add_argument("--rows", default=f"{BASE}/gate-rows.json")
    ap.add_argument("--tag", default="")  # output suffix, to not overwrite other runs
    args = ap.parse_args()
    rows = json.load(open(args.rows))
    key = gemini_key()
    tmp = tempfile.mkdtemp(prefix="vj_")

    if args.mode == "flag":
        sample = [r for r in rows if r["verdict"] == "FLAG"]
    else:
        # numeric ship set only (drop coincidental small ints + no-number prose from precision sample)
        ship = [r for r in rows if r["ship"] and r["verdict"] in ("AGREE", "ABSTAIN", "NOT_FOUND", "AMBIGUOUS")]
        per = args.n // 3
        sample = []
        for st in ("heavy", "moderate", "low"):
            pool = [r for r in ship if r["stratum"] == st]
            sample += random.sample(pool, min(per, len(pool)))
    print(f"mode={args.mode} sample={len(sample)}", flush=True)

    def judge(r):
        sha16 = r["sha"][:16]
        vk = numkey(r["value"])
        page = r["page"] or find_page(sha16, vk)
        out = dict(r); out["vision_caption"] = None; out["score"] = None
        if not page:
            out["score"] = "NO_PAGE"; return out
        try:
            img = render(r["sha"], page, tmp)
            if not img:
                out["score"] = "NO_RENDER"; return out
            counts, match = vision_judge_call(img, r["value"], r["metric_text"], key)
            if os.path.exists(img):
                os.remove(img)  # delete rendered page right after judging (disk-safe)
            out["vision_caption"] = counts
            if args.mode == "flag":
                if match == "NOT_ON_PAGE":
                    out["score"] = "NOT_ON_PAGE"
                else:  # use vision's INDEPENDENT caption vs both labels (not the leading match)
                    ds = regroup.labels_agree(counts, r["metric_text"])
                    rg = regroup.labels_agree(counts, r["regroup_label"] or "")
                    out["score"] = ("REGROUP_RIGHT" if rg and not ds else
                                    "METRIC_RIGHT" if ds and not rg else
                                    "BOTH" if ds and rg else "NEITHER")
            else:  # WRONG only if vision rules WRONG AND its own caption shares no distinctive words
                agree = regroup.labels_agree(counts, r["metric_text"])
                if match == "NOT_ON_PAGE":
                    out["score"] = "NOT_ON_PAGE"
                elif match == "CORRECT" or agree:
                    out["score"] = "CORRECT"
                elif match == "WRONG":
                    out["score"] = "WRONG"
                else:
                    out["score"] = "UNCLEAR"
        except Exception as e:
            out["score"] = f"ERR:{type(e).__name__}"
        return out

    results = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for i, out in enumerate(ex.map(judge, sample), 1):
            results.append(out)
            if i % 20 == 0:
                print(f"  judged {i}/{len(sample)}", flush=True)

    json.dump(results, open(f"{BASE}/vision-judge-{args.mode}{args.tag}.json", "w"), indent=1)
    sc = Counter(r["score"] for r in results)
    print(f"\nscores: {dict(sc)}")
    if args.mode == "ship":
        c, w = sc["CORRECT"], sc["WRONG"]
        p, lo, hi = wilson(c, c + w)
        print(f"\nSHIP-set precision (verifiable on page): {c}/{c+w} = {100*p:.1f}%  "
              f"95% CI [{100*lo:.1f}, {100*hi:.1f}]")
        print(f"  (excluded: {sc['NOT_ON_PAGE']} not-on-page, {sc['NO_PAGE']} no-page, "
              f"renders/errs {sc.get('NO_RENDER',0)})")
        # per stratum
        for st in ("heavy", "moderate", "low"):
            ss = [r for r in results if r["stratum"] == st]
            c2 = sum(1 for r in ss if r["score"] == "CORRECT")
            w2 = sum(1 for r in ss if r["score"] == "WRONG")
            if c2 + w2:
                print(f"  {st:9}: {c2}/{c2+w2} = {100*c2/(c2+w2):.1f}%")
    else:
        print(f"\nFLAG adjudication (who does the page agree with):")
        for k in ("REGROUP_RIGHT", "METRIC_RIGHT", "BOTH", "NEITHER", "NOT_ON_PAGE"):
            print(f"  {k:14} {sc[k]}")


if __name__ == "__main__":
    main()

"""Measure the UNLOCATABLE metrics — the ones the value-locator couldn't place on a unique page.
Locate each by its source_snippet (text overlap) instead, then judge with the same blind Flash-Lite
judge. Reports precision on the now-locatable share + how many remain unlocatable even by snippet.
"""
import argparse, json, os, re, sys, tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression/bake-off")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/p20-regroup-test")
import regroup
import vision_judge as vj
from lavandula.common.db import make_app_engine
from sqlalchemy import text
BASE = "/home/ubuntu/research/locard/operations/p20-regroup-test"


def snip_words(s):
    return {w[:5] for w in re.findall(r"[a-z]{4,}", (s or "").lower())} - regroup.GENERIC_STEMS


def find_page_snippet(sha16, snippet):
    """Page whose elements cover >=60% of the snippet's distinctive words (unique best)."""
    f = f"{BASE}/out/A/{sha16}.json"
    if not os.path.exists(f):
        return None
    d = json.load(open(f))
    if "error" in d:
        return None
    sw = snip_words(snippet)
    if len(sw) < 2:
        return None
    pagewords = {}
    for e in d["elements"]:
        ew = {w[:5] for w in re.findall(r"[a-z]{4,}", e["text"].lower())}
        pagewords.setdefault(e["page"], set()).update(ew)
    best, bestcov = None, 0.0
    for p, pw in pagewords.items():
        cov = len(sw & pw) / len(sw)
        if cov > bestcov:
            bestcov, best = cov, p
    return best if bestcov >= 0.6 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--judged", default=f"{BASE}/vision-judge-ship.json")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    res = json.load(open(args.judged))
    unloc = [r for r in res if r["score"] == "NO_PAGE"]
    # snippets: inline if present (current set), else pull from DB by real id (run-10)
    snips, need_db = {}, []
    for r in unloc:
        if r.get("snippet"):
            snips[r["id"]] = r["snippet"]
        else:
            need_db.append(r["id"])
    if need_db:
        eng = make_app_engine()
        with eng.connect() as c:
            snips.update(dict(c.execute(text(
                "SELECT id, source_snippet FROM lava_vocab.llm_metrics WHERE id = ANY(:i)"),
                {"i": need_db}).fetchall()))
    key = vj.gemini_key()
    tmp = tempfile.mkdtemp(prefix="vjs_", dir=f"{BASE}/scratch")
    print(f"unlocatable-by-value metrics to re-locate by snippet: {len(unloc)}", flush=True)

    def judge(r):
        out = dict(r); sha16 = r["sha"][:16]; snip = snips.get(r["id"], "")
        out["snippet"] = (snip or "")[:120]
        pg = find_page_snippet(sha16, snip)
        if not pg:
            out["score"] = "STILL_UNLOC"; return out
        img = vj.render(r["sha"], pg, tmp)
        if not img:
            out["score"] = "NO_RENDER"; return out
        counts, match = vj.vision_judge_call(img, r["value"], r["metric_text"], key)
        if os.path.exists(img):
            os.remove(img)
        out["vision_caption"] = counts; out["page2"] = pg
        agree = regroup.labels_agree(counts, r["metric_text"])
        out["score"] = ("NOT_ON_PAGE" if match == "NOT_ON_PAGE"
                        else "CORRECT" if (match == "CORRECT" or agree)
                        else "WRONG" if match == "WRONG" else "UNCLEAR")
        return out

    results = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for i, o in enumerate(ex.map(judge, unloc), 1):
            results.append(o)
            if i % 20 == 0:
                print(f"  {i}/{len(unloc)}", flush=True)
    json.dump(results, open(f"{BASE}/vision-judge-unloc{args.tag}.json", "w"), indent=1)
    sc = Counter(r["score"] for r in results)
    print(f"\nscores: {dict(sc)}")
    located = len(unloc) - sc["STILL_UNLOC"]
    print(f"located via snippet: {located}/{len(unloc)} ({100*located/max(1,len(unloc)):.0f}%); "
          f"still unlocatable: {sc['STILL_UNLOC']}")
    c, w = sc["CORRECT"], sc["WRONG"]
    if c + w:
        p, lo, hi = vj.wilson(c, c + w)
        print(f"precision on the previously-unlocatable (now judged): {c}/{c+w} = {100*p:.1f}%  "
              f"95% CI [{100*lo:.1f}, {100*hi:.1f}]")


if __name__ == "__main__":
    main()

"""Re-extract WITH the location link kept. Build the doc text from the parsed elements (each of which
has a page + box), recording which character range came from which box. Run extraction (current
verbatim-snippet prompt). Then snap each metric to its box by finding its snippet's position in the
SAME text we fed and looking up which element that position falls in. Output: metrics + exact box.
No searching for the number; the link is carried through.
"""
import json, os, re, sys, httpx
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, "/home/ubuntu/research")
from lavandula.nlp.llm_extract import call_deepseek, _METRICS_PROMPT
from lavandula.common.secrets import get_secret
BASE = "/home/ubuntu/research/locard/operations/p20-regroup-test"
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 600


def assemble(sha16):
    """Join element texts into one string; return (text, spans) where spans=[(start,end,page,bbox)]."""
    f = f"{BASE}/out/A/{sha16}.json"
    if not os.path.exists(f):
        return None, None
    d = json.load(open(f))
    if "error" in d:
        return None, None
    parts, spans, pos = [], [], 0
    for e in d["elements"]:
        t = e.get("text") or ""
        if not t.strip():
            continue
        start = pos
        parts.append(t); pos += len(t)
        spans.append((start, pos, e["page"], e["bbox"]))
        parts.append("\n"); pos += 1
    return "".join(parts)[:60000], spans


def locate(snippet, full, spans):
    """Find the snippet in the fed text, map its start to the element/box it falls in."""
    if not snippet:
        return None
    s = str(snippet).strip()
    i = full.find(s)
    if i < 0 and len(s) >= 24:
        i = full.find(s[:24])           # prefix fallback (model copied slightly long/short)
    if i < 0:
        norm = re.sub(r"\s+", " ", s)[:24]
        i = re.sub(r"\s+", " ", full).find(norm) if norm else -1
        if i < 0:
            return None
    for a, b, pg, bb in spans:
        if a <= i < b:
            return {"page": pg, "bbox": bb}
    return None


def main():
    man = json.load(open(f"{BASE}/sample-manifest.json"))[:LIMIT]
    dkey = get_secret("lavandula/deepseek/api_key")
    client = httpx.Client(headers={"Authorization": f"Bearer {dkey}"})
    print(f"grounded re-extraction of {len(man)} docs (link kept through extraction)", flush=True)

    def work(d):
        sha = d["sha"]; full, spans = assemble(sha[:16])
        rec = {"sha": sha, "stratum": d["stratum"], "metrics": [], "err": None}
        if not full or len(full) < 100:
            rec["err"] = "no_text"; return rec
        try:
            res, _, _ = call_deepseek(dkey, full, client, _METRICS_PROMPT)
            mets = res.get("metrics") if isinstance(res, dict) else res
            mets = mets if isinstance(mets, list) else []
        except Exception as e:
            rec["err"] = type(e).__name__; return rec
        for m in mets:
            loc = locate(m.get("source_snippet"), full, spans)
            rec["metrics"].append({"value": m.get("metric_value"), "text": m.get("metric_text"),
                                   "snippet": m.get("source_snippet"), "loc": loc})
        return rec

    results = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for i, r in enumerate(ex.map(work, man), 1):
            results.append(r)
            if i % 50 == 0:
                print(f"  {i}/{len(man)}", flush=True)
    json.dump(results, open(f"{BASE}/metrics-grounded.json", "w"))

    allm = [m for r in results for m in r["metrics"]]
    loc = sum(1 for m in allm if m["loc"])
    errs = [r for r in results if r["err"]]
    print(f"\ndocs={len(results)} ({len(errs)} errored)  metrics={len(allm)}")
    print(f"WITH EXACT BOX LOCATION: {loc}/{len(allm)} = {100*loc/max(1,len(allm)):.1f}%  "
          f"(no box = number not in parsed text, e.g. inside an image)")
    for st in ("heavy", "moderate", "low"):
        sm = [m for r in results if r["stratum"] == st for m in r["metrics"]]
        l = sum(1 for m in sm if m["loc"])
        print(f"  {st:9}: {l}/{len(sm)} = {100*l/max(1,len(sm)):.1f}% located")


if __name__ == "__main__":
    main()

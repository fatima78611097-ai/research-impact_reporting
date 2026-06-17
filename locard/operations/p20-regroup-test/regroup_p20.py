"""P20 out-of-sample regroup test — identify + propose-fix on real P20 metrics.

For each sampled doc: load config-A parsed elements (text+bbox+page), run the regroup pairing
(same module as dev: locard/operations/comp-metric-regression/regroup.py), and for every P20 metric
(lava_vocab.llm_metrics, run 10) locate its value on a page and compare regroup's caption vs the
metric's text. Verdicts per metric:
  AGREE        regroup pairs the value with words matching the metric text (supports the pairing)
  FLAG         high-conf pairing DISAGREES with the metric text  -> candidate mispair + proposed label
  ABSTAIN      no confident pairing (dense/ambiguous)            -> status quo
  AMBIGUOUS    value found on multiple pages / multiple numbers  -> excluded, counted
  NOT_FOUND    value not found as a bare number                  -> prose metric, regroup n/a
Outputs flags for vision adjudication. Read-only on the DB; resume-safe over parsed docs.
"""
import json
import glob
import os
import re
import sys
import collections

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import regroup
from lavandula.common.db import make_app_engine
from sqlalchemy import text

BASE = "/home/ubuntu/research/locard/operations/p20-regroup-test"


def words(s):
    return {w[:5] for w in re.findall(r"[a-z]{4,}", (s or "").lower())}


def numkey(v):
    """Digit-key of a value, normalizing float formatting: '60.0' -> '60' (NOT '600')."""
    s = str(v or "").strip()
    try:
        f = float(s.replace(",", "").replace("$", "").replace("%", ""))
        if f == int(f):
            return str(int(f))
    except ValueError:
        pass
    return re.sub(r"[^\d]", "", s)


def load_elements(sha16):
    f = f"{BASE}/out/A/{sha16}.json"
    if not os.path.exists(f):
        return None
    d = json.load(open(f))
    if "error" in d:
        return None
    by_page = collections.defaultdict(list)
    for e in d["elements"]:
        by_page[e["page"]].append((e["text"], e["bbox"], e.get("origin", "BOTTOMLEFT")))
    return by_page


def main():
    man = {d["sha"]: d for d in json.load(open(f"{BASE}/sample-manifest.json"))}
    eng = make_app_engine()
    with eng.connect() as conn:
        mets = conn.execute(text(
            "SELECT id, content_sha256, metric_text, metric_value, verification_tier "
            "FROM lava_vocab.llm_metrics WHERE run_id=10 AND content_sha256 = ANY(:shas)"),
            {"shas": list(man)}).fetchall()
    print(f"P20 metrics in sample: {len(mets)}")

    pair_cache = {}
    counts = collections.Counter()
    flags = []
    parsed_docs = set()
    for mid, sha, mtext, mval, tier in mets:
        sha16 = sha[:16]
        by_page = load_elements(sha16)
        if by_page is None:
            counts["doc_not_parsed_yet"] += 1
            continue
        parsed_docs.add(sha16)
        vk = numkey(mval)
        if not vk:
            counts[(tier, "NOT_FOUND")] += 1
            continue
        try:
            if abs(float(vk)) < 20:          # small ints ground by coincidence; geometry is for hero numbers
                counts[(tier, "SMALL_SKIP")] += 1
                continue
        except ValueError:
            pass
        # locate value: pages where it appears as a bare-number element
        hits = []
        for pg, els in by_page.items():
            if (sha16, pg) not in pair_cache:
                pair_cache[(sha16, pg)] = regroup.pair_from_elements(regroup.normalize(els))
            for n, d in pair_cache[(sha16, pg)].items():
                if numkey(n) == vk:
                    hits.append((pg, n, d))
        if not hits:
            counts[(tier, "NOT_FOUND")] += 1
            continue
        if len({pg for pg, _, _ in hits}) > 1 or len(hits) > 1:
            counts[(tier, "AMBIGUOUS")] += 1
            continue
        pg, n, d = hits[0]
        if d["label"] is None or d["confidence"] != "high":
            counts[(tier, "ABSTAIN")] += 1
            continue
        if words(d["label"]) & words(mtext):
            counts[(tier, "AGREE")] += 1
        else:
            counts[(tier, "FLAG")] += 1
            flags.append({"metric_id": mid, "sha": sha, "page": pg, "value": str(mval),
                          "tier": tier, "metric_text": (mtext or "")[:200],
                          "regroup_label": d["label"][:200]})

    print(f"docs parsed so far: {len(parsed_docs)}")
    print("\nverdicts (verification_tier, verdict):")
    for k, v in sorted(counts.items(), key=lambda x: str(x[0])):
        print(f"  {str(k):38} {v:6,}")
    json.dump(flags, open(f"{BASE}/regroup-p20-flags.json", "w"), indent=1)
    nf = len(flags)
    print(f"\nFLAGS for adjudication: {nf} -> regroup-p20-flags.json")
    for f in flags[:10]:
        print(f"  [{f['tier']:10}] {f['value'][:10]:>10} metric={f['metric_text'][:46]!r}")
        print(f"      regroup: {f['regroup_label'][:70]!r}")


if __name__ == "__main__":
    main()

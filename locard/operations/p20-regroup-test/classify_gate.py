"""Gate the P20 run-10 metrics on the 600 stratified docs using regroup (coords), no vision.
Emits per-metric verdict + ship decision -> gate-rows.json, and a stratified summary.

Ship rule (deterministic, no vision):
  AGREE      regroup's coordinate pairing matches the metric's label   -> SHIP (confirmed)
  ABSTAIN    regroup has no confident grid pairing (prose/ambiguous)    -> SHIP (reading-order stands)
  NOT_FOUND  value not isolated as a bare number (prose metric)         -> SHIP
  SMALL      value <20 (grounds by coincidence)                         -> SHIP
  AMBIGUOUS  value on multiple pages/spots                              -> SHIP (can't adjudicate by coord)
  FLAG       regroup pairing DISAGREES with metric label                -> HOLD for vision adjudication
  DOC_NOT_PARSED                                                        -> drop from test
"""
import json, os, re, sys, collections
sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import regroup
from lavandula.common.db import make_app_engine
from sqlalchemy import text
BASE = "/home/ubuntu/research/locard/operations/p20-regroup-test"


def words(s):
    return {w[:5] for w in re.findall(r"[a-z]{4,}", (s or "").lower())}


def numkey(v):
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
            "FROM lava_vocab.llm_metrics WHERE run_id=10 AND content_sha256 = ANY(:s)"),
            {"s": list(man)}).fetchall()
    print(f"metrics: {len(mets)} across {len(man)} docs", flush=True)

    SHIP = {"AGREE", "ABSTAIN", "NOT_FOUND", "SMALL", "AMBIGUOUS", "PROSE_NO_NUM"}
    pair_cache = {}
    rows = []
    for mid, sha, mtext, mval, tier in mets:
        sha16 = sha[:16]
        st = man[sha]["stratum"]
        rec = {"id": mid, "sha": sha, "stratum": st, "value": str(mval),
               "metric_text": (mtext or "")[:200], "page": None, "regroup_label": None,
               "verdict": None, "ship": False}
        by_page = load_elements(sha16)
        if by_page is None:
            rec["verdict"] = "DOC_NOT_PARSED"; rows.append(rec); continue
        vk = numkey(mval)
        if not vk:
            rec["verdict"] = "PROSE_NO_NUM"
        else:
            small = False
            try:
                small = abs(float(vk)) < 20
            except ValueError:
                pass
            if small:
                rec["verdict"] = "SMALL"
            else:
                hits = []
                for pg, els in by_page.items():
                    if (sha16, pg) not in pair_cache:
                        pair_cache[(sha16, pg)] = regroup.pair_from_elements(regroup.normalize(els))
                    for n, d in pair_cache[(sha16, pg)].items():
                        if numkey(n) == vk:
                            hits.append((pg, n, d))
                if not hits:
                    rec["verdict"] = "NOT_FOUND"
                elif len({pg for pg, _, _ in hits}) > 1 or len(hits) > 1:
                    rec["verdict"] = "AMBIGUOUS"
                else:
                    pg, n, d = hits[0]
                    rec["page"] = pg
                    if d["label"] is None or d["confidence"] != "high":
                        rec["verdict"] = "ABSTAIN"
                    else:
                        rec["regroup_label"] = d["label"][:200]
                        rec["verdict"] = "AGREE" if (words(d["label"]) & words(mtext)) else "FLAG"
        rec["ship"] = rec["verdict"] in SHIP
        rows.append(rec)

    json.dump(rows, open(f"{BASE}/gate-rows.json", "w"))
    # summaries
    vc = collections.Counter(r["verdict"] for r in rows)
    print("\nverdict distribution:")
    for k, v in vc.most_common():
        print(f"  {k:16} {v:6,}  {'SHIP' if k in SHIP else 'HOLD/DROP'}")
    shipped = [r for r in rows if r["ship"]]
    held = [r for r in rows if r["verdict"] == "FLAG"]
    testable = [r for r in rows if r["verdict"] != "DOC_NOT_PARSED"]
    print(f"\ntestable metrics: {len(testable):,}")
    print(f"  SHIP (deterministic, no vision): {len(shipped):,} ({100*len(shipped)/len(testable):.1f}%)")
    print(f"  HOLD for vision (FLAG):          {len(held):,} ({100*len(held)/len(testable):.1f}%)")
    # the clean, non-vision-built headline subset
    confirmed = [r for r in rows if r["verdict"] == "AGREE"]
    print(f"  of SHIP, regroup-CONFIRMED (AGREE): {len(confirmed):,}")
    print("\nby stratum (testable / ship / flag):")
    for st in ("heavy", "moderate", "low"):
        t = [r for r in testable if r["stratum"] == st]
        s = [r for r in t if r["ship"]]
        f = [r for r in t if r["verdict"] == "FLAG"]
        print(f"  {st:9} {len(t):6,} / {len(s):6,} / {len(f):5,}")


if __name__ == "__main__":
    main()

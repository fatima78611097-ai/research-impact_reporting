"""Gate metrics from a re-extraction JSON (new-metrics-current.json) with the same regroup logic
as classify_gate.py. Emits gate-rows-current.json (same shape) for the vision judge.
"""
import json, os, re, sys, collections
sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import regroup
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
    src = sys.argv[1] if len(sys.argv) > 1 else f"{BASE}/new-metrics-current.json"
    out = sys.argv[2] if len(sys.argv) > 2 else f"{BASE}/gate-rows-current.json"
    data = json.load(open(src))
    SHIP = {"AGREE", "ABSTAIN", "NOT_FOUND", "SMALL", "AMBIGUOUS", "PROSE_NO_NUM"}
    pair_cache = {}
    rows = []
    mid = 0
    for d in data:
        sha = d["sha"]; st = d["stratum"]; sha16 = sha[:16]
        by_page = load_elements(sha16)
        for m in d["metrics"]:
            mid += 1
            mval = m.get("metric_value"); mtext = m.get("metric_text")
            rec = {"id": mid, "sha": sha, "stratum": st, "value": str(mval),
                   "metric_text": (mtext or "")[:200], "snippet": str(m.get("source_snippet", ""))[:200],
                   "page": None, "regroup_label": None, "verdict": None, "ship": False}
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
                        for n, dd in pair_cache[(sha16, pg)].items():
                            if numkey(n) == vk:
                                hits.append((pg, n, dd))
                    if not hits:
                        rec["verdict"] = "NOT_FOUND"
                    elif len({pg for pg, _, _ in hits}) > 1 or len(hits) > 1:
                        rec["verdict"] = "AMBIGUOUS"
                    else:
                        pg, n, dd = hits[0]
                        rec["page"] = pg
                        if dd["label"] is None or dd["confidence"] != "high":
                            rec["verdict"] = "ABSTAIN"
                        else:
                            rec["regroup_label"] = dd["label"][:200]
                            rec["verdict"] = "AGREE" if (words(dd["label"]) & words(mtext)) else "FLAG"
            rec["ship"] = rec["verdict"] in SHIP
            rows.append(rec)

    json.dump(rows, open(out, "w"))
    vc = collections.Counter(r["verdict"] for r in rows)
    testable = [r for r in rows if r["verdict"] != "DOC_NOT_PARSED"]
    ship = [r for r in rows if r["ship"]]
    print(f"metrics: {len(rows)} | testable: {len(testable)} | ship: {len(ship)} "
          f"({100*len(ship)/max(1,len(testable)):.1f}%) | flag: {vc['FLAG']}")
    print("verdicts:", dict(vc.most_common()))


if __name__ == "__main__":
    main()

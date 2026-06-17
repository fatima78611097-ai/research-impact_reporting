"""Spot-verify the distinctive grounding-repair candidates: for each, dump the value, subject,
the CITED marker (what the gate looked at), and the OTHER marker(s) where the value+subject
co-occur (the re-ground target). Lets a human read each and judge real-mis-grounding vs
coincidence/derived. Also dumps the 2 parser-recovered to confirm WHY they recovered.
"""
import json
import re
import sys

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import gate
import render
from lavandula.common.db import make_app_engine

REGATE = "/home/ubuntu/research/locard/operations/comp-metric-regression/regate-value-quarantines.json"
RESULTS = {
    "batch1-clean": "/home/ubuntu/research/locard/operations/comp-metric-regression/scale100-results-tuned.json",
    "test2": "/home/ubuntu/research/locard/operations/comp-metric-regression/scale-test2-results.json",
}
REVIEW = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/review-data.json"


def mag(v):
    try:
        return abs(float(re.sub(r"[,$%]", "", str(v))))
    except (ValueError, TypeError):
        return None


def main():
    regate = json.load(open(REGATE))["rows"]
    review = {r["id"]: r for r in json.load(open(REVIEW))}
    results = {k: json.load(open(v)) for k, v in RESULTS.items()}
    full_by_set = {k: {sha[:8]: sha for sha in res} for k, res in results.items()}

    # distinctive candidates: reground-strong/distinct with value >= 20 (skip <20 coincidence band)
    cands = [r for r in regate if r["bucket"] in ("reground-strong", "reground-distinct")
             and (mag(r["value"]) or 0) >= 20]
    recovered = [r for r in regate if r["bucket"] == "recovered"]
    print(f"distinctive reground candidates: {len(cands)} | recovered: {len(recovered)}\n")

    eng = make_app_engine()
    cache = {}
    with eng.connect() as conn:
        def render_doc(full):
            if full not in cache:
                _, _, cache[full] = render.render_tagged(conn, full)
            return cache[full]

        def dump(r, header):
            setname, sha8, idx = review[r["id"]]["set"], review[r["id"]]["sha8"], int(r["id"].split(":")[1])
            full = full_by_set[setname][sha8]
            m = results[setname][full][idx]
            idmap = render_doc(full)
            v = gate.to_float(m.get("value"))
            tol = max(1.0, 0.001 * abs(v)) if v is not None else 0
            subj_w = set(re.findall(r"[a-z]{4,}", (m.get("label") or "").lower()))
            cited = idmap.get(m.get("value_ref")) or {}
            print(f"{header} {r['id']}  value={m.get('value')}  [{r['bucket']}]")
            print(f"   STATEMENT: {m.get('statement')}")
            print(f"   subject  : {m.get('label')}")
            print(f"   CITED marker {m.get('value_ref')} (p{cited.get('page')}): {(cited.get('text') or '')[:300]}")
            others = []
            for k, it in idmap.items():
                if k == m.get("value_ref"):
                    continue
                nums = gate.candidate_numbers(it.get("text") or "")
                if v is not None and any(abs(c - v) <= tol for c in nums):
                    tw = set(re.findall(r"[a-z]{4,}", (it.get("text") or "").lower()))
                    others.append((k, it.get("page"), bool(subj_w & tw), (it.get("text") or "")[:300]))
            for k, pg, coq, txt in others[:3]:
                print(f"   OTHER  {k} (p{pg}, subj_co={coq}): {txt}")
            print()

        print("================ PARSER-RECOVERED (confirm why) ================\n")
        for r in recovered:
            dump(r, ">>")
        print("================ DISTINCTIVE REGROUND CANDIDATES ================\n")
        for r in sorted(cands, key=lambda x: -(mag(x["value"]) or 0)):
            dump(r, "--")


if __name__ == "__main__":
    main()

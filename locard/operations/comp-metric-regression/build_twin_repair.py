"""Twin-prefix repair (ENH-007): recover value_not_at_marker quarantines caused by the model
swapping the marker-type prefix — cited `c91` but meant `t91` (same index, wrong letter).

For each value_not_at_marker quarantine: if value_ref doesn't ground the value, try the
same-index opposite-prefix twin; if the twin grounds it, re-run the gate with the repaired
refs. Deterministic. Produces a VERIFIED LIST (twin-repair-recovered.json) for the operator to
mark/publish — does NOT touch review.db, does NOT auto-flip any verdict.
"""
import json
import sys

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import gate
import render
from lavandula.common.db import make_app_engine

REVIEW = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/review-data.json"
RES = {"batch1-clean": "/home/ubuntu/research/locard/operations/comp-metric-regression/scale100-results-tuned.json",
       "test2": "/home/ubuntu/research/locard/operations/comp-metric-regression/scale-test2-results.json"}
OUT = "/home/ubuntu/research/locard/operations/comp-metric-regression/twin-repair-recovered.json"


def twin(ref):
    if not ref or len(ref) < 2 or ref[0] not in "tc":
        return None
    return ("t" if ref[0] == "c" else "c") + ref[1:]


def grounds_value(ref, v, tol, idmap):
    e = idmap.get(ref)
    return e is not None and any(abs(c - v) <= tol for c in gate.candidate_numbers(e.get("text") or ""))


def main():
    review = {r["id"]: r for r in json.load(open(REVIEW))}
    quar = [r for r in review.values() if r.get("gate_decision") == "quarantine"
            and r.get("gate_reason") == "value_not_at_marker"]
    results = {k: json.load(open(v)) for k, v in RES.items()}
    full_by_set = {k: {s[:8]: s for s in r} for k, r in results.items()}

    eng = make_app_engine()
    cache = {}
    recovered = []
    with eng.connect() as conn:
        def idm(f):
            if f not in cache:
                _, _, cache[f] = render.render_tagged(conn, f)
            return cache[f]

        for r in quar:
            setn, sha8, idx = r["set"], r["sha8"], int(r["id"].split(":")[1])
            full = full_by_set.get(setn, {}).get(sha8)
            if not full:
                continue
            m = results[setn][full][idx]
            idmap = idm(full)
            vr, sr = m.get("value_ref"), m.get("subject_ref")
            v = gate.to_float(m.get("value"))
            if v is None or not vr:
                continue
            tol = max(1.0, 0.001 * abs(v))
            # repair value_ref only if cited doesn't ground AND the twin does
            if grounds_value(vr, v, tol, idmap) or not twin(vr) or not grounds_value(twin(vr), v, tol, idmap):
                continue
            rvr = twin(vr)
            # repair a nonexistent subject_ref by its twin too (same swap confusion)
            rsr = twin(sr) if (sr and sr not in idmap and twin(sr) and twin(sr) in idmap) else sr
            dec, reason = gate.verdict({"metric_value": m.get("value"), "label": m.get("label")}, rvr, rsr, idmap)
            tw = idmap.get(rvr) or {}
            recovered.append({"id": r["id"], "value": m.get("value"), "statement": m.get("statement"),
                              "subject": m.get("label"),
                              "cited_value_ref": vr, "repaired_value_ref": rvr,
                              "cited_subject_ref": sr, "repaired_subject_ref": rsr,
                              "twin_page": tw.get("page"), "twin_text": (tw.get("text") or "")[:140],
                              "new_decision": dec, "new_reason": reason})

    pub = [r for r in recovered if r["new_decision"] == "publish"]
    json.dump({"check": "twin-prefix-repair", "recovered": recovered,
               "publish_count": len(pub)}, open(OUT, "w"), indent=1)
    print(f"twin-repair applied: {len(recovered)} metrics repaired; {len(pub)} now PUBLISH\n")
    for r in recovered:
        print(f"  {r['id']:13} {r['cited_value_ref']}->{r['repaired_value_ref']}  "
              f"[{r['new_decision']}/{r['new_reason']}]  val={r['value']}")
        print(f"      twin(p{r['twin_page']}): {r['twin_text'][:90]}")
    print(f"\nsaved {OUT}")


if __name__ == "__main__":
    main()

"""M1 — integrated research pipeline (decision chain).

Assembles the post-extraction steps into ONE pass over the corpus's extracted metrics:
    gate (hardened) -> is-a-metric gate (small-int classifier) -> twin-prefix repair -> tier (logged).
Extraction (render + select[b51dc96a] + grounding pass) already produced the metrics in the results
files; this is the deterministic decision layer that turns them into final publish/quarantine + tier.
Then it scores the resulting published set against the M0 vision fixture and shows the delta vs §1a.

`decide()` is the single per-metric chain (reused by a future live per-doc runner). Run directly =
replay the chain over the whole corpus + score.
"""
import json
import re
import sys

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import gate
import render
from lavandula.common.db import make_app_engine
from score_pipeline import scorecard, _pct

B = "/home/ubuntu/research/locard/operations/comp-metric-regression/"
REVIEW = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/review-data.json"
RES = {"batch1-clean": B + "scale100-results-tuned.json", "test2": B + "scale-test2-results.json"}
MV = B + "measurable-value-quarantine.json"
FIX = B + "fixtures/vision-ground-truth.json"


def twin(ref):
    if not ref or len(ref) < 2 or ref[0] not in "tc":
        return None
    return ("t" if ref[0] == "c" else "c") + ref[1:]


def _grounds(ref, v, tol, idmap):
    e = idmap.get(ref)
    return e is not None and any(abs(c - v) <= tol for c in gate.candidate_numbers(e.get("text") or ""))


def _neighbor(ref, delta):
    """Same-prefix marker at index +-delta: t1 -> t2/t0. None if malformed."""
    if not ref or len(ref) < 2 or ref[0] not in "tc" or not ref[1:].isdigit():
        return None
    i = int(ref[1:]) + delta
    return f"{ref[0]}{i}" if i >= 0 else None


# Item-4 rule B (rounded value ~ precise figure at marker) was built and REJECTED on fixture
# validation 2026-06-11: 1 of its 2 recoveries was a look-alike NUMBER FROM A DIFFERENT STAT
# (81265722:0 — "more than 1,300" matched 1,324, but vision shows 1,324 is another metric and the
# true 1,300 lives elsewhere). 50% precision -> the gate's strictness is correct here; rounding
# recovery would re-introduce exactly the near-duplicate-number confusion ISSUE-003 documents.


def _yearlike(v):
    return v is not None and 1900 <= v <= 2099 and v == int(v)


def decide(m, idmap, measured):
    """The integrated per-metric chain. Returns (decision, reason, tier)."""
    vr, sr = m.get("value_ref"), m.get("subject_ref")
    label, val = m.get("label"), m.get("value")
    statement = m.get("statement") or ""
    dec, reason = gate.verdict({"metric_value": val, "label": label}, vr, sr, idmap, measured=measured)
    if dec == "quarantine" and reason == "value_not_at_marker":
        v = gate.to_float(val)
        tol = max(1.0, 0.001 * abs(v)) if v is not None else None
        # 1) twin-prefix repair (ENH-007): wrong letter, right index
        if v is not None and vr and twin(vr) and _grounds(twin(vr), v, tol, idmap):
            rsr = twin(sr) if (sr and sr not in idmap and twin(sr) and twin(sr) in idmap) else sr
            dec2, _ = gate.verdict({"metric_value": val, "label": label}, twin(vr), rsr, idmap, measured=measured)
            if dec2 == "publish":
                return "publish", "twin_repair", m.get("tier")
        # 2) item-4a: off-by-one neighbor re-anchor — value AND subject must ground at the neighbor.
        #    Guards: small ints excluded (ground anywhere) and YEAR-RANGE values excluded
        #    (recovering a "1999" re-anchors a year-as-value non-metric, vision-confirmed).
        if v is not None and abs(v) >= 20 and not _yearlike(v) and vr:
            for d in (1, -1):
                nb = _neighbor(vr, d)
                if nb and nb in idmap and _grounds(nb, v, tol, idmap):
                    dec3, _ = gate.verdict({"metric_value": val, "label": label}, nb, nb, idmap, measured=measured)
                    if dec3 == "publish":
                        return "publish", "neighbor_repair", m.get("tier")
    return dec, reason, m.get("tier")


def main():
    review = {r["id"]: r for r in json.load(open(REVIEW))}
    mv_flags = set(json.load(open(MV))["quarantine"])
    results = {k: json.load(open(v)) for k, v in RES.items()}
    full_by_set = {k: {s[:8]: s for s in r} for k, r in results.items()}

    eng = make_app_engine()
    cache = {}
    published, reasons = set(), {}
    skipped = 0
    with eng.connect() as conn:
        def idm(f):
            if f not in cache:
                _, _, cache[f] = render.render_tagged(conn, f)
            return cache[f]
        for r in review.values():
            try:
                idx = int(r["id"].split(":")[1])
            except (ValueError, IndexError):
                skipped += 1
                continue
            full = full_by_set.get(r.get("set"), {}).get(r.get("sha8"))
            if not full:
                skipped += 1
                continue
            try:
                m = results[r["set"]][full][idx]
            except (KeyError, IndexError):
                skipped += 1
                continue
            idmap = idm(full)
            measured = (r["id"] not in mv_flags) if gate.is_small_int(m.get("value")) else None
            dec, reason, _ = decide(m, idmap, measured)
            reasons[r["id"]] = (dec, reason)
            if dec == "publish":
                published.add(r["id"])

    records = json.load(open(FIX))["records"]
    base = scorecard(records)                  # §1a: original publish set
    new = scorecard(records, published)        # M1: integrated decision chain
    twin_n = sum(1 for d, rs in reasons.values() if rs == "twin_repair")

    def row(name, key, denom_key=None):
        b, n = base[key], new[key]
        bd = base[denom_key] if denom_key else base["published"]
        nd = new[denom_key] if denom_key else new["published"]
        print(f"  {name:26} {b:5} ({_pct(b,bd)})  ->  {n:5} ({_pct(n,nd)})")

    print(f"skipped (no extraction record): {skipped}")
    print(f"twin-repair recoveries: {twin_n}\n")
    print(f"{'layer':28} {'§1a (original)':>16}      {'M1 (integrated)':>16}")
    print(f"  {'published':26} {base['published']:5}              {new['published']:5}")
    row("is-a-metric", "is_a_metric")
    row("grounding correct", "grounded")
    row("shippable", "shippable")
    row("recall loss", "recall_loss", "recall_denom")
    print(f"\nM1 net: published {base['published']}->{new['published']}; "
          f"is-a-metric {_pct(base['is_a_metric'],base['published'])}->{_pct(new['is_a_metric'],new['published'])}; "
          f"shippable {_pct(base['shippable'],base['published'])}->{_pct(new['shippable'],new['published'])}")
    json.dump({"published": sorted(published), "reasons": reasons},
              open(B + "pipeline-decisions.json", "w"))


if __name__ == "__main__":
    main()

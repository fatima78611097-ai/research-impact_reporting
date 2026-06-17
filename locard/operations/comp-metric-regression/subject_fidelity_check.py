"""Subject-fidelity check: the metric's STATED subject must word-match what's actually at its
grounded location — not merely be co-located with the value.

The gate's current subject_grounded (gate.py:99) falls back to bare co_located, so a label that
shares ZERO words with its subject_ref marker passes as long as it sits next to the value
(f1189246:5: label "Medicaid revenue" grounded against a "Dane County" cell). This tightens it:
the label must match the subject_ref text OR the VALUE's vicinity (its row / co-located markers).
If it matches neither, the named subject isn't supported -> subject-fidelity FAIL.

Tests on f1189246:5 (expect FLAG) + the 40-sample (expect ~0 new flags on the clean ones).
"""
import json
import re
import sys

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import gate
import render
from lavandula.common.db import make_app_engine

REVIEW = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/review-data.json"
HAND = "/home/ubuntu/research/locard/operations/comp-metric-regression/hand_tier_labels.json"
RES = {"batch1-clean": "/home/ubuntu/research/locard/operations/comp-metric-regression/scale100-results-tuned.json",
       "test2": "/home/ubuntu/research/locard/operations/comp-metric-regression/scale-test2-results.json"}


def vicinity_text(vref, idmap):
    """Text at/around the VALUE. For a table cell: its OWN ROW only (+ column header) — never
    bbox-proximity, which bleeds across closely-spaced rows. For prose: co-located markers."""
    v = idmap.get(vref) or {}
    if v.get("kind") == "cell":
        parts = [v.get("row_text", "") or ""]
        tab, col = v.get("table"), v.get("col")
        if tab is not None and col is not None:
            cells = [e for e in idmap.values() if e.get("table") == tab]
            hr = min((c.get("row") for c in cells if c.get("row") is not None), default=None)
            parts.append(next((c.get("text", "") for c in cells if c.get("row") == hr and c.get("col") == col), ""))
        return " ".join(p for p in parts if p)
    parts = [v.get("text", "") or ""]
    for k, e in idmap.items():
        if k != vref and gate.co_located(vref, k, idmap):
            parts.append(e.get("text", "") or "")
    return " ".join(parts)


def _stem(w):
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    for suf in ("ings", "ing", "ions", "ion", "ments", "ment", "ees", "ee", "es", "ed", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[:-len(suf)]
            break
    if len(w) > 3 and w.endswith("e"):
        w = w[:-1]
    return w


def stems(s):
    return {_stem(w) for w in re.findall(r"[a-z]{4,}", (s or "").lower())}


def fidelity_grounded(label, sref, vref, idmap):
    """Tightened subject grounding: the label must STEM-match the subject marker OR the value's
    vicinity. Stemming clears the plural/inflection false-positives (immigrant/immigrants)."""
    lst = stems(label)
    if not lst:
        return True  # no subject words to verify
    sr = idmap.get(sref) or {}
    stext = (sr.get("text", "") or "") + " " + (sr.get("row_text", "") or "")
    if lst & stems(stext):
        return True
    if lst & stems(vicinity_text(vref, idmap)):
        return True
    return False


def main():
    review = {r["id"]: r for r in json.load(open(REVIEW))}
    results = {k: json.load(open(p)) for k, p in RES.items()}
    full_by_set = {k: {s[:8]: s for s in r} for k, r in results.items()}
    pub = [r for r in review.values() if r.get("gate_decision") == "publish"]

    eng = make_app_engine()
    cache = {}
    flagged = []
    checked = 0
    with eng.connect() as conn:
        def idm(f):
            if f not in cache:
                _, _, cache[f] = render.render_tagged(conn, f)
            return cache[f]
        for r in pub:
            setn, sha8 = r["set"], r["sha8"]
            try:
                idx = int(r["id"].split(":")[1])
            except ValueError:
                continue
            full = full_by_set.get(setn, {}).get(sha8)
            if not full:
                continue
            try:
                m = results[setn][full][idx]
            except (KeyError, IndexError):
                continue
            idmap = idm(full)
            vr, sr, lbl = m.get("value_ref"), m.get("subject_ref"), m.get("label")
            checked += 1
            if gate.subject_grounded(lbl, sr, vr, idmap) and not fidelity_grounded(lbl, sr, vr, idmap):
                flagged.append((r["id"], lbl, (idmap.get(sr) or {}).get("text", ""), m.get("value")))

    print(f"published metrics checked: {checked}")
    print(f"subject-fidelity FLAGS (OLD grounded, NEW fails): {len(flagged)} "
          f"({100*len(flagged)/checked:.1f}% of published)\n")
    json.dump([{"id": i, "label": l, "subject_ref_text": s, "value": v} for i, l, s, v in flagged],
              open("/home/ubuntu/research/locard/operations/comp-metric-regression/subject-fidelity-flags.json", "w"), indent=1)
    for i, l, s, v in flagged:
        print(f"  {i:13} val={str(v)[:9]:>9}  label={str(l)[:34]!r:36} subj_ref={str(s)[:34]!r}")


if __name__ == "__main__":
    main()

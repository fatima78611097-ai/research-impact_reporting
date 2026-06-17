"""Build review-data.json: every metric in the clean-166 baseline (minus the 15 gold),
with gate result, grounded value/subject (page+text), where the value ACTUALLY appears,
bboxes for the boxed image, source link, and Claude's first-pass proposal (verdict +
cause-stage + failure-mode + note). Static data for the review viewer; the backend only
stores the operator's verdicts on top of this.
"""
import json
import re
import sys

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import gate
import render
from lavandula.common.db import make_app_engine
from sqlalchemy import text

OUT = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/review-data.json"
SETS = [
    ("batch1-clean", "locard/operations/comp-metric-regression/scale100-results-tuned.json",
     "locard/operations/comp-metric-regression/baseline-100.json"),
    ("test2", "locard/operations/comp-metric-regression/scale-test2-results.json",
     "locard/operations/comp-metric-regression/test2-100.json"),
]
DISCARD = {"financial_report", "not_relevant"}


def words(s):
    return set(re.findall(r"[a-z]{4,}", (s or "").lower()))


def value_locations(value, idmap):
    v = gate.to_float(value)
    if v is None:
        return []
    tol = max(1.0, 0.001 * abs(v))
    out = []
    for k, it in idmap.items():
        if any(abs(c - v) <= tol for c in gate.candidate_numbers(it.get("text") or "")):
            out.append({"id": k, "page": it.get("page"), "text": (it.get("text") or "")[:240]})
    return out


def claude_pass(r, idmap, vref, sref, vlocs):
    """Heuristic FIRST-PASS proposal (a suggestion, never the record)."""
    dec, reason = r["decision"], r.get("reason")
    subj_w = words(r.get("label"))
    if dec == "publish":
        # gate verified value+subject at the cited markers; flag ambiguity if value repeats
        npages = len({L["page"] for L in vlocs if L["page"]})
        if npages >= 3:
            return dict(verdict="unsure", stage="", mode="value-repeats",
                        note=f"value appears on {npages} pages — confirm the pairing is the right instance")
        return dict(verdict="gate-correct", stage="", mode="",
                    note="value+subject verified at cited markers (auto; shallow — confirm by eye)")
    # quarantine
    if reason == "no_numeric_value":
        return dict(verdict="gate-correct", stage="selection", mode="no-numeric",
                    note="qualitative statement, no number to verify")
    if reason == "value_not_at_marker":
        if not vlocs:
            return dict(verdict="gate-correct", stage="selection", mode="derived-value",
                        note="value appears nowhere in the doc — model-computed/derived")
        # value IS in the doc somewhere
        at_subject = bool(sref) and any(abs(c - gate.to_float(r["value"])) <= max(1.0, 0.001*abs(gate.to_float(r["value"])))
                                        for c in gate.candidate_numbers(sref.get("text") or "")) if gate.to_float(r["value"]) else False
        with_subj = [L for L in vlocs if subj_w & words(L["text"])]
        if at_subject:
            return dict(verdict="false-quarantine", stage="grounding", mode="slot-swap",
                        note=f"value sits at the cited SUBJECT marker (page {sref.get('page')}) — value/subject slots swapped")
        if with_subj:
            return dict(verdict="false-quarantine", stage="grounding", mode="wrong-marker",
                        note=f"value+subject co-occur @ page {with_subj[0]['page']}; grounding cited the wrong marker")
        return dict(verdict="unsure", stage="grounding", mode="wrong-marker",
                    note=f"value present @ page {vlocs[0]['page']} but subject pairing unclear")
    if reason == "subject_not_grounded":
        return dict(verdict="unsure", stage="grounding", mode="subject-not-found",
                    note="value ok; subject not found at cited marker")
    return dict(verdict="unsure", stage="", mode="", note="")


eng = make_app_engine()
# gold-15 to exclude
gold = json.load(open("locard/operations/comp-metric-regression/gold-sample.json"))
gold_keys = {(g["sha8"], " ".join(str(g["statement"]).split())) for g in gold}

recs_out = []
with eng.connect() as conn:
    allshas = [d["sha"] for _, _, dj in SETS for d in json.load(open(dj))["docs"]]
    mt = {r[0]: r[1] for r in conn.execute(text(
        "SELECT content_sha256, material_type FROM lava_corpus.corpus WHERE content_sha256=ANY(:s)"), {"s": allshas}).fetchall()}
    names = {r[0]: r[1] for r in conn.execute(text(
        "SELECT d.content_sha256, ns.name FROM lava_parse.documents d "
        "LEFT JOIN lava_corpus.nonprofits_seed ns ON ns.ein=d.source_org_ein WHERE d.content_sha256=ANY(:s)"),
        {"s": allshas}).fetchall()}
    for setname, rj, dj in SETS:
        res = json.load(open(rj))
        shas = [d["sha"] for d in json.load(open(dj))["docs"] if mt.get(d["sha"]) not in DISCARD]
        for sha in shas:
            if not res.get(sha):
                continue
            _, _, idmap = render.render_tagged(conn, sha)
            s8 = sha[:8]
            for i, r in enumerate(res[sha]):
                if (s8, " ".join(str(r["statement"]).split())) in gold_keys:
                    continue  # already gold-verified
                vref = idmap.get(r.get("value_ref")) or {}
                sref = idmap.get(r.get("subject_ref")) or {}
                vlocs = value_locations(r.get("value"), idmap)
                page = vref.get("page") or sref.get("page")
                recs_out.append({
                    "id": f"{s8}:{i}", "set": setname, "sha8": s8, "org": str(names.get(sha) or "?"), "idx": i,
                    "statement": r.get("statement"), "value": r.get("value"), "subject": r.get("label"),
                    "gate_decision": r.get("decision"), "gate_reason": r.get("reason") or "",
                    "v_page": vref.get("page"), "v_text": (vref.get("text") or "")[:240],
                    "s_page": sref.get("page"), "s_text": (sref.get("text") or "")[:240],
                    "same_marker": r.get("value_ref") == r.get("subject_ref"),
                    "found": [{"page": L["page"], "text": L["text"]} for L in vlocs[:3]],
                    "page": page, "v_bbox": vref.get("bbox"), "s_bbox": sref.get("bbox"),
                    "pdf": f"reviewbox/{s8}.pdf", "img": f"reviewbox/{s8}_{i}.png",
                    "claude": claude_pass(r, idmap, vref, sref, vlocs),
                })

json.dump(recs_out, open(OUT, "w"), indent=1)
pub = sum(1 for r in recs_out if r["gate_decision"] == "publish")
print(f"wrote {len(recs_out)} metric records -> {OUT}")
print(f"  published {pub}  quarantined {len(recs_out)-pub}")
import collections
print("  claude-pass verdicts:", dict(collections.Counter(r["claude"]["verdict"] for r in recs_out)))
print("  by set:", dict(collections.Counter(r["set"] for r in recs_out)))

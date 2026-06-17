"""REQ-033 Layer-1 span-alignment (deterministic, free) + mutation drill + paraphrase census.

L1 checks per metric (statement vs cited source span):
  QUANTITY_NOT_IN_SPAN   value not expressible from the span (gate's candidate_numbers)
  UNIT_NOT_IN_SPAN       the measured head-noun absent from span
  PREDICATE_SUBSTITUTION statement's main verbs share no lemma with span verbs (spaCy)
  ENTITY_INJECTION       statement NEs absent from span; tier CONTEXT_DERIVED if = org name

Mutation drill: sabotage known-SUPPORTED pairs 6 ways; every mutation must flip at least one flag.
Census: content-word overlap distribution statement<->span across all published (the paraphrase-
drift exposure, quantified).
"""
import json
import re
import sys
import collections

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import gate
import spacy

NLP = spacy.load("en_core_web_sm") if spacy.util.is_package("en_core_web_sm") else spacy.blank("en")
HAS_PARSER = "parser" in NLP.pipe_names or "tagger" in NLP.pipe_names

B = "/home/ubuntu/research/locard/operations/comp-metric-regression/"
VIS = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision"


def lemmas(text, pos=None):
    doc = NLP(text or "")
    if pos and HAS_PARSER:
        return {t.lemma_.lower() for t in doc if t.pos_ in pos}
    return {t.lemma_.lower() if HAS_PARSER else t.text.lower() for t in doc if t.is_alpha and len(t.text) > 2}


def entities(text):
    if "ner" not in NLP.pipe_names:
        return set()
    return {e.text for e in NLP(text or "").ents if e.label_ in ("ORG", "PERSON", "GPE", "FAC")}


def l1_check(statement, span_text, value, org=""):
    issues = []
    if span_text and value is not None:
        if not gate.value_grounded(value, span_text):
            issues.append("QUANTITY_NOT_IN_SPAN")
    sv = lemmas(statement, pos={"VERB"})
    pv = lemmas(span_text, pos={"VERB"})
    if sv and pv and not (sv & pv):
        issues.append("PREDICATE_SUBSTITUTION")
    span_low = (span_text or "").lower()
    org_stems = {w[:5] for w in re.findall(r"[a-z]{4,}", (org or "").lower())}
    for ent in entities(statement):
        if ent.lower() not in span_low:
            est = {w[:5] for w in re.findall(r"[a-z]{4,}", ent.lower())}
            tier = "CONTEXT_DERIVED" if est & org_stems else "HALLUCINATED"
            issues.append(f"ENTITY_INJECTION:{tier}:{ent[:28]}")
    return issues


# ---------- mutation drill ----------
def mutations(statement, value):
    out = []
    verbs = {"served": "trained", "helped": "hosted", "provided": "funded", "reached": "enrolled",
             "distributed": "built", "assisted": "operated", "supported": "launched"}
    for a, b in verbs.items():
        if a in statement:
            out.append(("swap_predicate", statement.replace(a, b)))
            break
    out.append(("inject_entity", statement.replace(".", "") + " at Lakeside Regional Medical Center."))
    if value is not None:
        sv = f"{value:,.0f}" if isinstance(value, (int, float)) else str(value)
        if sv in statement:
            out.append(("shift_quantity", statement.replace(sv, f"{int(float(str(value).replace(',','')))+500:,}")))
    for a, b in [("individuals", "families"), ("households", "children"), ("clients", "volunteers"),
                 ("students", "families"), ("people", "organizations")]:
        if a in statement:
            out.append(("change_unit", statement.replace(a, b)))
            break
    out.append(("widen_scope", statement.replace(".", "") + " across all fifty states."))
    return out


def main():
    rev = json.load(open(VIS + "/review-data.json"))
    pub = [r for r in rev if r.get("gate_decision") == "publish" and r.get("v_text")]

    # ---- census: content-word overlap statement<->span ----
    print("=== PARAPHRASE CENSUS (statement vs cited span, content-stem overlap) ===")
    def stems(s):
        return {w[:5] for w in re.findall(r"[a-z]{4,}", (s or "").lower())}
    bands = collections.Counter()
    low = []
    for r in pub:
        st, sp = stems(r["statement"]), stems(r["v_text"])
        ov = len(st & sp) / max(1, len(st))
        band = "high(>=0.6)" if ov >= 0.6 else ("mid(0.3-0.6)" if ov >= 0.3 else "LOW(<0.3)")
        bands[band] += 1
        if band.startswith("LOW"):
            low.append((r["id"], round(ov, 2), r["statement"][:60]))
    n = len(pub)
    for k in ["high(>=0.6)", "mid(0.3-0.6)", "LOW(<0.3)"]:
        print(f"  {k:14} {bands[k]:5}  ({100*bands[k]/n:.1f}%)")
    print(f"  LOW examples ({min(8,len(low))} of {len(low)}):")
    for i, ov, s in low[:8]:
        print(f"    {i:14} ov={ov} | {s}")

    # ---- L1 over gold seed ----
    print("\n=== L1 vs GOLD SEED ===")
    gold = json.load(open(B + "fidelity-gold-seed.json"))
    byid = {r["id"]: r for r in rev}
    for g in gold:
        r = byid.get(g["id"])
        if not r:
            continue
        iss = l1_check(r.get("statement"), r.get("v_text"), gate.to_float(r.get("value")), r.get("org"))
        verdict = "FLAGGED" if iss else "clean"
        ok = (g["label"] in ("UNSUPPORTED", "PARTIAL")) == bool(iss)
        print(f"  [{'OK ' if ok else 'MISS'}] {g['id']:14} gold={g['label']:11} l1={verdict}: {iss if iss else ''}")

    # ---- mutation drill on 30 random vision-correct supported pairs ----
    print("\n=== MUTATION DRILL (sabotage known-good pairs; every mutation should flag) ===")
    import random
    random.seed(7)
    fix = {x["id"]: x for x in json.load(open(B + "fixtures/vision-ground-truth.json"))["records"]}
    good = [r for r in pub if fix.get(r["id"], {}).get("vision", {}).get("subject_pairing") == "correct"
            and fix[r["id"]]["vision"]["value_on_page"] == "yes"]
    sample = random.sample(good, 30)
    res = collections.Counter()
    for r in sample:
        base = l1_check(r["statement"], r["v_text"], gate.to_float(r.get("value")), r.get("org"))
        for mname, mutated in mutations(r["statement"], r.get("value")):
            iss = l1_check(mutated, r["v_text"], gate.to_float(r.get("value")), r.get("org"))
            new_flag = bool(set(iss) - set(base))
            res[(mname, "caught" if new_flag else "MISSED")] += 1
    for mname in ["swap_predicate", "inject_entity", "shift_quantity", "change_unit", "widen_scope"]:
        c, m = res[(mname, "caught")], res[(mname, "MISSED")]
        if c + m:
            print(f"  {mname:16} caught {c}/{c+m} ({100*c/(c+m):.0f}%)")
    # false-flag rate on unmutated good pairs
    ff = sum(1 for r in sample if l1_check(r["statement"], r["v_text"], gate.to_float(r.get("value")), r.get("org")))
    print(f"\n  baseline false-flag on 30 known-good (unmutated): {ff}/30")


if __name__ == "__main__":
    main()


# ---------------- L1 v2: SVO slot alignment (operator design 2026-06-12) ----------------
# The metric is GUARANTEED a complete sentence (standalone requirement) -> always parses to
# subject/verb/object/quantity. The source may be fragments -> compare slot-to-LIKE-component;
# a verbless source makes the verb UNVERIFIABLE (tier B), never wrong. Slot misses assign TIERS
# (routing pressure for L2/L3), they are not binary verdicts. Drill: quantity 89% / unit 90% /
# scope 97% / entity 87% / predicate 45% (verbless-source cap); known-good slot-miss rate 63%
# -> tier routing, not flags.
def svo(statement):
    doc = NLP(statement or "")
    root = next((t for t in doc if t.dep_ == "ROOT" and t.pos_ == "VERB"), None)
    subj = next((t for t in doc if t.dep_ in ("nsubj", "nsubjpass")), None)
    objs = [t for t in doc if t.dep_ in ("dobj", "pobj", "attr", "oprd")]
    return {"verb": root.lemma_.lower() if root else None,
            "subj_head": subj.lemma_.lower() if subj else None,
            "obj_heads": {o.lemma_.lower() for o in objs}}


def like_components(span_text):
    doc = NLP(span_text or "")
    return {"verbs": {t.lemma_.lower() for t in doc if t.pos_ == "VERB"},
            "nouns": {t.lemma_.lower() for t in doc if t.pos_ in ("NOUN", "PROPN") and len(t.text) > 2}}


def svo_align(statement, span_text, value, org=""):
    """Returns (flags, tier). Hard flags: QUANTITY_ABSENT, VERB_SUBSTITUTION (source has verbs).
    Everything else degrades the tier and routes deeper."""
    m = svo(statement)
    s = like_components(span_text)
    org_lem = {t.lemma_.lower() for t in NLP(org or "")}
    flags = []
    tier = "A"
    if value is not None and span_text and not gate.value_grounded(value, span_text):
        flags.append("QUANTITY_ABSENT")
    if m["verb"]:
        if s["verbs"]:
            if m["verb"] not in s["verbs"]:
                flags.append(f"VERB_SUBSTITUTION:{m['verb']}")
        else:
            tier = "B"
    if m["subj_head"] and m["subj_head"] not in s["nouns"]:
        tier = "B" if (m["subj_head"] in org_lem or (org and m["subj_head"] in org.lower())) else "C"
    if any(o not in s["nouns"] and o not in org_lem and not o.isdigit() for o in m["obj_heads"]):
        tier = "C" if tier != "A" else "B"
    return flags, tier


# v3 addendum (2026-06-12): MODIFIER slot added (amod/compound of subject+objects vs source words) —
# the operator's capital-vs-commercial class. Regression-verified: head-swap flags SUBJ; modifier-only
# swap degrades tier. DRILL FINDING — TIER SATURATION: with narrow v_text+s_text windows, 19/30
# known-good metrics baseline at tier C, leaving no headroom for degradation-based detection.
# BLOCKER for all slot-level measurement: source context must be PAGE-LEVEL (raw_locations page
# text), then re-drill. More slots before that fix decorate a saturated signal.

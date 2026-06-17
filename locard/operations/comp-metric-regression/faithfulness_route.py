"""Faithfulness check, stage 1 (operator design, 2026-06-12 session).

Leverage the complete-sentence guarantee: parse the metric into subject/verb/object
slots and compare each slot to the LIKE components of the FULL CITED PAGE(S) (store
narrow, verify wide -- the tier-saturation fix). Any slot mismatch routes the metric
to the stage-2 LLM spirit judge (attribution_amp_check SYSTEM prompt). Stage 1 is
deterministic and free; nothing is auto-acted.

Outputs faithfulness-route.json: per-metric slot results + route/pass verdict.
"""
import json
import sys
import collections

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import gate
import render
import spacy
from lavandula.common.db import make_app_engine

NLP = spacy.load("en_core_web_sm")
B = "/home/ubuntu/research/locard/operations/comp-metric-regression/"
VIS = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision"
RES = {"batch1-clean": B + "scale100-results-tuned.json", "test2": B + "scale-test2-results.json"}


def metric_slots(statement):
    """SVO + modifier slots of the metric (guaranteed complete sentence)."""
    doc = NLP(statement or "")
    root = next((t for t in doc if t.dep_ == "ROOT" and t.pos_ in ("VERB", "AUX")), None)
    subj = next((t for t in doc if t.dep_ in ("nsubj", "nsubjpass")), None)
    objs = [t for t in doc if t.dep_ in ("dobj", "pobj", "attr", "oprd")]
    mods = [t for t in doc if t.dep_ in ("amod", "compound")
            and t.head in ([subj] if subj else []) + objs]
    return {"verb": root.lemma_.lower() if root else None,
            "subj": subj.lemma_.lower() if subj else None,
            "objs": {o.lemma_.lower() for o in objs},
            "mods": {m.lemma_.lower() for m in mods}}


_PAGE_CACHE = {}


def _depl(t):
    """naive singular: nights->night, services->service (spaCy keeps ALL-CAPS plurals)."""
    if len(t) > 3 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


def page_components(sha8, page, page_text):
    key = (sha8, page)
    if key not in _PAGE_CACHE:
        doc = NLP(page_text or "")
        toks = {t.text.lower() for t in doc if t.is_alpha and len(t.text) > 2}
        # ALL-CAPS headline labels ("NIGHTS OF HOUSING PROVIDED") defeat POS tagging: include
        # every surface token + naive singular in nouns/words, and match verbs by stem prefix.
        surface = toks | {_depl(t) for t in toks}
        _PAGE_CACHE[key] = {
            "verbs": {t.lemma_.lower() for t in doc if t.pos_ == "VERB"},
            "nouns": {t.lemma_.lower() for t in doc if t.pos_ in ("NOUN", "PROPN") and len(t.text) > 2} | surface,
            "words": {t.lemma_.lower() for t in doc if t.is_alpha and len(t.text) > 2} | surface,
            "toks": toks,
        }
    return _PAGE_CACHE[key]


def verb_on_page(verb, src):
    """verb lemma in parsed verbs, or any surface form of it on the page (caps-proof)."""
    if verb in src["verbs"] or verb in src["toks"]:
        return True
    stem = verb[:-1] if verb.endswith("e") else verb
    if len(stem) >= 4 and any(t.startswith(stem) for t in src["toks"]):
        return True
    # short verbs: exact inflection set only
    forms = {verb + "s", verb + "ed", verb + "d", verb + "ing",
             verb + verb[-1] + "ing", verb + verb[-1] + "ed"}
    return bool(forms & src["toks"])


def merge(comps):
    out = {"verbs": set(), "nouns": set(), "words": set(), "toks": set()}
    for c in comps:
        for k in out:
            out[k] |= c[k]
    return out


import re

_NUMERICISH = re.compile(r"^[\d,.$%\-]+$")
_SCALE_WORDS = {"million", "billion", "thousand", "hundred", "%", "percent"} | set(gate._ONES) | set(gate._TENS)
_COPULAS = {"be", "have"}  # no action to amplify; copula misses are noise, not spirit risk
# generic time/report-framing nouns ("in fiscal year 2025", "per week", "in the reporting
# period", "since inception") -- framing the model adds, not content that can drift
_TIME_NOUNS = {"year", "period", "week", "month", "day", "time", "inception", "date", "average"}
# generic self-reference subjects: "The organization served..." IS the org
_ORG_GENERIC = {"organization", "nonprofit", "agency"}


def _noise_token(t):
    return len(t) < 3 or _NUMERICISH.match(t) or t in _SCALE_WORDS or t in _TIME_NOUNS


def _org_match(tok, org):
    """tok refers to the org: appears in the org name, or is its acronym (CTN)."""
    low = (org or "").lower()
    if not low or not tok:
        return False
    if tok in low:
        return True
    words = re.findall(r"[a-z]+", low)
    acro_all = "".join(w[0] for w in words)
    acro_content = "".join(w[0] for w in words if w not in ("of", "the", "and", "for", "in", "a"))
    return len(tok) >= 2 and tok in (acro_all, acro_content)


def check(statement, value, org, src):
    """Slot-by-slot vs page components. Returns (misses, verdict)."""
    m = metric_slots(statement)
    org_lem = {t.lemma_.lower() for t in NLP(org or "")}
    misses = []
    if value is not None and not gate.value_grounded(value, src["raw"]):
        misses.append("quantity")
    if m["verb"] and m["verb"] not in _COPULAS and src["verbs"] and not verb_on_page(m["verb"], src):
        misses.append(f"verb:{m['verb']}")
    if m["subj"] and not _noise_token(m["subj"]) and m["subj"] not in _ORG_GENERIC \
            and m["subj"] not in src["nouns"] \
            and m["subj"] not in org_lem and not _org_match(m["subj"], org):
        misses.append(f"subj:{m['subj']}")
    for o in m["objs"]:
        if not _noise_token(o) and o not in src["nouns"] and o not in org_lem and not _org_match(o, org):
            misses.append(f"obj:{o}")
    for md in m["mods"]:
        if not _noise_token(md) and md.isalpha() and md not in src["words"] and md not in org_lem:
            misses.append(f"mod:{md}")
    return misses, ("route" if misses else "pass")


def main():
    rev = json.load(open(VIS + "/review-data.json"))
    results = {k: json.load(open(v)) for k, v in RES.items()}
    fbs = {k: {s[:8]: s for s in r} for k, r in results.items()}
    eng = make_app_engine()
    out, skipped = [], 0
    rendered = {}
    with eng.connect() as conn:
        for r in rev:
            full = fbs.get(r.get("set"), {}).get(r["sha8"])
            if not full:
                skipped += 1
                continue
            if full not in rendered:
                try:
                    _, _, rendered[full] = render.render_tagged(conn, full)
                except Exception:
                    rendered[full] = None
            idmap = rendered[full]
            if not idmap:
                skipped += 1
                continue
            pages = {p for p in (r.get("v_page"), r.get("s_page")) if p not in (None, "", "None")}
            pages = {int(p) for p in pages}
            if not pages:
                skipped += 1
                continue
            by_page = collections.defaultdict(list)
            for item in idmap.values():
                if item.get("page") in pages:
                    by_page[item["page"]].append(item.get("text", ""))
            raw = "\n".join("\n".join(v) for v in by_page.values())
            comps = merge([page_components(r["sha8"], p, "\n".join(by_page[p])) for p in by_page])
            comps["raw"] = raw
            misses, v = check(r.get("statement"), gate.to_float(r.get("value")), r.get("org"), comps)
            out.append({"id": r["id"], "gate_decision": r["gate_decision"], "misses": misses,
                        "verdict": v, "statement": r["statement"]})
    json.dump(out, open(B + "faithfulness-route.json", "w"), indent=1)

    pub = [o for o in out if o["gate_decision"] == "publish"]
    qua = [o for o in out if o["gate_decision"] != "publish"]
    print(f"checked {len(out)} (skipped {skipped})")
    for name, grp in [("PUBLISHED", pub), ("QUARANTINED", qua)]:
        routed = [o for o in grp if o["verdict"] == "route"]
        print(f"\n{name}: {len(grp)-len(routed)} pass / {len(routed)} route ({100*len(routed)/max(1,len(grp)):.1f}%)")
        slot = collections.Counter(m.split(":")[0] for o in routed for m in o["misses"])
        print("  miss slots:", dict(slot.most_common()))


if __name__ == "__main__":
    main()

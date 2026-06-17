"""SLOT-RENDER SPIKE (isolated; read-only on canonical data).

Tests the Giving Compass-aligned alternative: instead of the LLM-composed `statement`,
render the display sentence deterministically from the slots the LLM already extracted
(value + label + grounding markers). NO re-extraction, NO LLM call. Reads frozen outputs
+ review-data.json (canonical gate decisions) + review.db (operator hand-marks), all
read-only. Writes ONLY into this folder.

Outputs:
  side-by-side.md  — composed statement vs slot-rendered, for every published metric
  spike-summary.txt — recall (clean vs thin label) + how it lines up with hand-marks
"""
import json
import re
import sqlite3
import collections

CMR = "/home/ubuntu/research/locard/operations/comp-metric-regression"
VIS = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/review-data.json"
RES = {"batch1-clean": CMR + "/scale100-results-tuned.json", "test2": CMR + "/scale-test2-results.json"}
OUT = CMR + "/slot-render-spike"

# tight currency nouns (dropped ambiguous "support"/"value"/"growth")
CURRENCY_HINT = re.compile(r"\b(revenue|expense|expenditure|asset|fund|funding|grant|"
                           r"refund|cost|wage|income|budget|salary|donation|investment|"
                           r"deficit|surplus|proceeds|dollar|sales)\b", re.I)
PERCENT_HINT = re.compile(r"\b(percent|percentage|share|rate|proportion)\b", re.I)


def kind_of(label, statement):
    """percent | currency | count. Trust the statement only when it's UNAMBIGUOUS
    (exactly one of $/% present); otherwise the value's unit comes from the label.
    A statement can carry a stray % that isn't this value's unit (the $830,553-with-85% trap)."""
    st, lab = statement or "", label or ""
    has_d, has_p = "$" in st, "%" in st
    if has_d and not has_p:
        return "currency"
    if has_p and not has_d:
        return "percent"
    labC, labP = CURRENCY_HINT.search(lab), PERCENT_HINT.search(lab)
    if labP and not labC:
        return "percent"
    if labC and not labP:
        return "currency"
    return "count"


def lower_keep_acronyms(s):
    """lowercase words but keep ALL-CAPS acronyms (HCL, CKA, ANC, CDBG)."""
    return " ".join(w if (len(w) >= 2 and w.isupper()) else w.lower() for w in s.split())


def humanize(n):
    a = abs(n)
    if a >= 1e9:
        s = f"{n/1e9:.1f}B"
    elif a >= 1e6:
        s = f"{n/1e6:.1f}M"
    elif a >= 1e3:
        s = f"{n/1e3:.1f}K"
    else:
        return f"{int(n):,}" if float(n).is_integer() else f"{n:g}"
    return s.replace(".0", "")


def fmt_full(n):
    return f"{int(n):,}" if float(n).is_integer() else f"{n:g}"


_ARTICLE = re.compile(r"^(the|a|an)\s+", re.I)


def clean_label(label):
    return _ARTICLE.sub("", (label or "").strip())


def render(value, label, statement):
    """Return (full_statement, compact, thin?). Giving-Compass style."""
    lab = clean_label(label)
    try:
        value = float(re.sub(r"[^0-9.\-]", "", str(value))) if value is not None else None
    except ValueError:
        value = None
    if value is None or not lab:
        return None, None, True
    labx = lower_keep_acronyms(lab)
    kind = kind_of(lab, statement)
    if kind == "percent":
        v = fmt_full(value) if not float(value).is_integer() else f"{int(value)}"
        full = f"{v}% {labx}"
        compact = f"{v}%"
    elif kind == "currency":
        full = f"${fmt_full(value)} {labx}"
        compact = f"{humanize(value)} USD"
    else:
        full = f"{fmt_full(value)} {labx}"
        compact = f"{humanize(value)} {labx}"
    # thin = label is empty, numeric, or a single generic word that can't stand alone
    thin = (len(lab) < 3) or bool(re.fullmatch(r"[\d.,$%]+", lab)) or \
        (len(lab.split()) == 1 and lab.lower() in {"total", "number", "amount", "count", "value", "rate"})
    return full, compact, thin


def main():
    rev = json.load(open(VIS))
    results = {k: json.load(open(v)) for k, v in RES.items()}
    fbs = {k: {s[:8]: s for s in r} for k, r in results.items()}

    def slots_for(r):
        full = fbs.get(r.get("set"), {}).get(r["sha8"])
        if not full:
            return None
        try:
            return results[r["set"]][full][int(r["id"].split(":")[1])]
        except (KeyError, IndexError, ValueError):
            return None

    con = sqlite3.connect(CMR + "/review.db")
    marks = {i: v for i, v in con.execute(
        "SELECT id,operator_verdict FROM reviews WHERE operator_verdict IS NOT NULL")}
    con.close()

    pub = [r for r in rev if r.get("gate_decision") == "publish"]
    rows, thin_n, missing = [], 0, 0
    for r in pub:
        m = slots_for(r)
        if not m:
            missing += 1
            continue
        full, compact, thin = render(m.get("value"), m.get("label"), m.get("statement"))
        if full is None:
            thin_n += 1
            continue
        if thin:
            thin_n += 1
        rows.append({"id": r["id"], "label": m.get("label"), "value": m.get("value"),
                     "composed": m.get("statement"), "slot_full": full, "slot_compact": compact,
                     "thin": thin, "mark": marks.get(r["id"], "")})

    # side-by-side
    L = ["# Slot-render vs composed — published metrics (isolated spike)", "",
         "Composed = what the LLM wrote (current). Slot = rendered from value+label (proposed).",
         "Same grounded value in both. `thin` = label too weak to stand alone.", ""]
    L.append("| id | composed (current) | slot-rendered (proposed) | compact | thin |")
    L.append("|---|---|---|---|---|")
    for x in rows:
        L.append(f"| {x['id']} | {x['composed'][:70]} | {x['slot_full'][:60]} | {x['slot_compact'][:22]} | {'⚠' if x['thin'] else ''} |")
    open(OUT + "/side-by-side.md", "w").write("\n".join(L))

    # summary
    n = len(rows)
    clean = sum(1 for x in rows if not x["thin"])
    S = []
    S.append(f"published metrics with extractable slots: {n}  (skipped {missing} unmapped)")
    S.append(f"render CLEANLY (label stands alone): {clean}/{n} = {100*clean/n:.1f}%")
    S.append(f"thin label (needs fallback):         {thin_n}")
    # cross with operator marks: of the ones you marked false-publish, do slots also look bad?
    checked = [x for x in rows if x["mark"]]
    S.append(f"\nof {len(checked)} you hand-checked & published:")
    bym = collections.Counter(x["mark"] for x in checked)
    S.append(f"  verdicts: {dict(bym)}")
    fp = [x for x in checked if x["mark"] == "false-publish"]
    S.append(f"  false-publish among them: {len(fp)} (slot-render does NOT fix these — value/is-a-metric job)")
    for x in fp[:10]:
        S.append(f"    {x['id']:14} slot=“{x['slot_full'][:55]}”")
    open(OUT + "/spike-summary.txt", "w").write("\n".join(S))
    print("\n".join(S))


if __name__ == "__main__":
    main()

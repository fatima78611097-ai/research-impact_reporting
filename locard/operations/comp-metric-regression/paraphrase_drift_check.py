"""Two-stage harness, plug-in #3 = paraphrase-drift (does the STATEMENT change the SOURCE's meaning?).

Supersedes loose_or_check.py. That check asked "is the source a loose or-list?" — which is
faithful most of the time, so it punished the model for the report's own wording and wrongly
quarantined verbatim metrics (d5cb6d0c:7, :8). This asks the question that actually matters:
did the model DISTORT the source when it wrote the metric?

Stage 1 — cheap regex: an "or" connective on either side (statement or source) AND a substantive
          prose source to compare against. The or<->and flip is the proven defect vector; an "or"
          must appear somewhere for it to occur. (Expandable later to negation / quantifier drift.)
Stage 2 — DeepSeek compares STATEMENT vs SOURCE → FAITHFUL or DISTORTED. Calibrated 5/5:
          flags d5cb6d0c:6 (or->and + dropped clause), keeps d5cb6d0c:7/:8 (verbatim or).
"""
import json
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, "/home/ubuntu/research")
from lavandula.common.secrets import get_secret

DATA = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/review-data.json"
OUT = "/home/ubuntu/research/locard/operations/comp-metric-regression/paraphrase-drift-quarantine.json"
KEY = get_secret("lavandula/deepseek/api_key")


def substantive(text):
    """A real prose source to compare against — not a bare number / chart fragment."""
    text = text or ""
    return len(re.findall(r"[A-Za-z]+", text)) >= 5


# ---------- the check definition (swap these per check) ----------
CHECK = {
    "name": "paraphrase-drift",
    # Stage 1: high-recall — an 'or' connective on either side, and a prose source to compare.
    "stage1": lambda r: bool(re.search(r"\bor\b", (r["statement"] or "") + "  " + (r.get("v_text") or ""), re.I))
                        and substantive(r.get("v_text")),
    "system": (
        "A metric STATEMENT was written from a SOURCE sentence. Judge ONLY the metric's own claim "
        "(the number and what it counts). Did the statement change the LOGICAL RELATIONSHIP of that "
        "claim versus the source?\n"
        "DISTORTED = the source counts a quantity that satisfies ANY of several different things "
        "joined by 'or' (a disjunction: A, B, OR C), and the statement rewrites it as a conjunction "
        "(A AND B) or drops/adds members so the number now counts something different. This raises or "
        "lowers the bar for what the number means.\n"
        "  Example DISTORTED: SOURCE '88% have control over their life, are optimistic, OR have self-worth' "
        "-> STATEMENT '88% have control over their life AND are optimistic' (any-of-three became both-of-two).\n"
        "FAITHFUL = the metric's claim is unchanged. This INCLUDES: rounding ($346,656.95 -> $346,657); "
        "rewording or shortening; a faithful 'A or B' kept as 'A or B'; and — most importantly — an 'or' "
        "that appears ELSEWHERE in the source but NOT in the metric's own claim ('deficit or loss', "
        "'at or below', 'minimum wage or more', 'support or resources', 'challenging or changing'). "
        "If the statement's own claim matches the source, it is FAITHFUL even if some other phrase in the "
        "source contains 'or'.\n"
        "Reply with one word: FAITHFUL or DISTORTED."
    ),
    "user": lambda r: f"STATEMENT: {r['statement']}\nSOURCE: {r.get('v_text') or ''}",
}


def deepseek(system, user):
    body = json.dumps({"model": "deepseek-chat", "temperature": 0.0, "max_tokens": 8,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": user}]}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request("https://api.deepseek.com/v1/chat/completions", data=body,
                                         headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())["choices"][0]["message"]["content"].strip().upper()
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                return "ERR"
    return "ERR"


def main():
    d = json.load(open(DATA))
    cands = [r for r in d if CHECK["stage1"](r)]
    print(f"STAGE 1 (or-connective + prose source) → {len(cands)} of {len(d)} ({100*len(cands)/len(d):.1f}%)")
    distorted = []
    for r in cands:
        v = deepseek(CHECK["system"], CHECK["user"](r))
        flag = v.startswith("DISTORT")
        if flag:
            distorted.append(r)
        print(f"  {'DISTORTED' if flag else 'faithful '}  {r['id']:13}  {r['statement'][:80]}")
    print()
    print(f"STAGE 2 (DeepSeek) → {len(distorted)} of {len(cands)} candidates are DISTORTED "
          f"({100*len(distorted)/len(d):.2f}% of all metrics)")
    json.dump({"check": CHECK["name"], "candidates": [r["id"] for r in cands],
               "quarantine": [r["id"] for r in distorted]}, open(OUT, "w"), indent=1)
    print(f"saved {OUT}")


if __name__ == "__main__":
    main()

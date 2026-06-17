"""Two-stage harness, plug-in #2 = measurable-value (does the metric measure a real quantity?).

Stage 1 — regex: small integer values are the suspects (multipliers, tallies of named items,
structural/org counts, identifiers all live here; real big counts/$/% almost never do).
Stage 2 — DeepSeek: MEASURE vs NOT. Same engine as loose_or_check.py.
"""
import json
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, "/home/ubuntu/research")
from lavandula.common.secrets import get_secret

DATA = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/review-data.json"
OUT = "/home/ubuntu/research/locard/operations/comp-metric-regression/measurable-value-quarantine.json"
KEY = get_secret("lavandula/deepseek/api_key")


def small_int(r):
    try:
        v = float(re.sub(r"[,$%]", "", str(r["value"])))
    except (ValueError, TypeError):
        return False
    return v == int(v) and abs(v) <= 20


CHECK = {
    "name": "measurable-value",
    "stage1": small_int,
    "system": (
        "Judge whether a nonprofit-report metric number is a REAL COUNTED/MEASURED QUANTITY or NOT.\n"
        "MEASURE = the number counts or measures real things: people/clients served, graduates, "
        "FACILITIES/vehicles/LOCATIONS/counties operated or served (16 centers, 10 vans, 3 counties), "
        "providers, dollars, percent, hours, units produced. Small counts still count, even \"1 client served\".\n"
        "NOT = the number is not a quantity of anything: a multiplier (\"doubled\"=2); a bare \"1\" that only "
        "means a single EVENT happened or a single thing EXISTS (\"the legislature passed a bill\"=1, "
        "\"HB 700 allows...\"=1, \"the church completed revitalization\"=1, \"we are the ONLY shelter\"=1, "
        "\"first of its kind\"=1); a tally of items merely listed; or an identifier/year/rank.\n"
        "Reply with one word: MEASURE or NOT."
    ),
    "user": lambda r: r["statement"],
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
    print(f"STAGE 1 (small-int suspects) → {len(cands)} of {len(d)} ({100*len(cands)/len(d):.1f}%)")
    not_measure = []
    for r in cands:
        v = deepseek(CHECK["system"], CHECK["user"](r))
        if v.startswith("NOT"):
            not_measure.append(r)
        print(f"  {'NOT-MEASURE' if v.startswith('NOT') else 'measure    '}  {r['id']:13} val={str(r['value']):>4}  {r['statement'][:78]}")
    print()
    print(f"STAGE 2 (DeepSeek) → {len(not_measure)} of {len(cands)} candidates have NO measurable value "
          f"({100*len(not_measure)/len(d):.1f}% of all metrics)")
    json.dump({"check": CHECK["name"], "candidates": [r["id"] for r in cands],
               "quarantine": [r["id"] for r in not_measure]}, open(OUT, "w"), indent=1)
    print(f"saved {OUT}")


if __name__ == "__main__":
    main()

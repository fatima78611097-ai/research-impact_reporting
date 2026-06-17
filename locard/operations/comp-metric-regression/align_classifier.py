"""M1b — align the is-a-metric (measurable-value) classifier to the LOCKED definition.

Stage 1 = small integers (<=20), same suspects as before. Stage 2 = DeepSeek METRIC/NOT with the
locked rubric: KEEP outcome/reach/capacity/financial (incl. capacity ratios like 4:1 member-to-staff);
REJECT the 8 tier-0 types + ACTIVITY (launched/hosted/opened N). Writes the aligned NOT-list; the diff
vs the old list = the capacity ratios restored + activity counts now rejected.
"""
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/home/ubuntu/research")
from lavandula.common.secrets import get_secret

KEY = get_secret("lavandula/deepseek/api_key")
B = "/home/ubuntu/research/locard/operations/comp-metric-regression/"
DATA = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/review-data.json"
OLD = B + "measurable-value-quarantine.json"
OUT = B + "measurable-value-quarantine-aligned.json"

SYSTEM = (
    "Judge whether the small-integer value in a nonprofit-report metric is a REAL MEASURED QUANTITY (METRIC) or NOT.\n"
    "METRIC = the number measures one of these value classes:\n"
    "  - reach/output: people or units served/delivered (served 7 communities; 15 clients; 12 meals)\n"
    "  - capacity: resources the org has or runs — staff, volunteers, vehicles, centers, counties, partners, beds, "
    "members — INCLUDING structural ratios (4:1 member-to-staff; 14 volunteers per staff)\n"
    "  - outcome: a measured change for people (3 residents transitioned to permanent housing)\n"
    "  - financial: dollars.\n"
    "  Small counts still count (even '1 client served', '1 mobile van').\n"
    "NOT = the number measures none of the above:\n"
    "  - an EVENT that merely happened / a thing that EXISTS encoded as 1 ('passed a bill'=1, 'the only shelter'=1, 'first of its kind'=1)\n"
    "  - ACTIVITY: programs/events/sites the org launched, hosted, opened, or held ('launched 3 programs'; 'hosted 5 events'; 'opened 2 sites')\n"
    "  - a multiplier ('doubled'=2), a ranking/ordinal (#1, 3rd), an award/honor count ('1 of 7 honorees'),\n"
    "  - a date/year, a duration/tenure ('9-month program', 'operating 5 years'), a label/rating/level ('4-star', 'Level 3'), a forecast.\n"
    "Reply with one word: METRIC or NOT."
)


def small_int(r):
    try:
        v = float(re.sub(r"[,$%]", "", str(r["value"])))
    except (ValueError, TypeError):
        return False
    return v == int(v) and abs(v) <= 20


def deepseek(user):
    body = json.dumps({"model": "deepseek-chat", "temperature": 0.0, "max_tokens": 8,
                       "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}).encode()
    for _ in range(3):
        try:
            req = urllib.request.Request("https://api.deepseek.com/v1/chat/completions", data=body,
                                         headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(req, timeout=60).read())["choices"][0]["message"]["content"].strip().upper()
        except Exception:
            pass
    return "ERR"


d = json.load(open(DATA))
cands = [r for r in d if small_int(r)]
print(f"stage 1 small-int suspects: {len(cands)}")


def classify(r):
    return r["id"], deepseek(r["statement"])


with ThreadPoolExecutor(max_workers=20) as ex:
    res = dict(ex.map(classify, cands))

err = [i for i, v in res.items() if v == "ERR"]
notlist = [i for i, v in res.items() if v.startswith("NOT")]
old = set(json.load(open(OLD))["quarantine"])
new = set(notlist)
print(f"errors: {len(err)}")
print(f"aligned NOT-a-metric: {len(new)}  (old: {len(old)})")
print(f"  RESTORED to metric (were NOT, now METRIC): {len(old - new)} -> {sorted(old - new)[:12]}")
print(f"  NEWLY rejected (were METRIC, now NOT):      {len(new - old)} -> {sorted(new - old)[:12]}")

json.dump({"check": "measurable-value-aligned", "candidates": [r["id"] for r in cands],
           "quarantine": sorted(new)}, open(OUT, "w"), indent=1)
print(f"saved {OUT}")

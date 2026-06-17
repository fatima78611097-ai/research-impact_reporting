"""Coded metric/not-a-metric classifier vs my manual run. Runs DeepSeek (definition-based) on the
65 published flagged metrics and diffs against my hand calls (held=METRIC, else=NOT)."""
import json
import sys
import urllib.request

sys.path.insert(0, "/home/ubuntu/research")
from lavandula.common.secrets import get_secret

KEY = get_secret("lavandula/deepseek/api_key")
VIS = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision"
MV = "/home/ubuntu/research/locard/operations/comp-metric-regression/measurable-value-quarantine.json"
HELD = {"5b8eae73:4", "d58e3d5b:11", "12a4557a:4", "3e3ff326:10",
        "e43d7482:6", "f4819890:8", "81265722:9", "0793289a:2"}

SYSTEM = (
    "Decide if a nonprofit-report claim is a METRIC or NOT.\n"
    "METRIC = a number that measures a real counted/measured quantity the org did, reached, produced, "
    "or changed: people served, dollars, percent, meals, hours, genuine reach counts.\n"
    "NOT = the number does not measure a quantity: an event that happened once (=1), an award/honor, a "
    "ranking or Top-N placement, a year or date, a duration, a star/level rating, a multiplier (doubled), "
    "a forecast, a tally of items merely listed by name, or a bare structural ratio with no measured outcome.\n"
    "Reply with one word: METRIC or NOT."
)


def deepseek(user):
    body = json.dumps({"model": "deepseek-chat", "temperature": 0.0, "max_tokens": 4,
                       "messages": [{"role": "system", "content": SYSTEM},
                                    {"role": "user", "content": user}]}).encode()
    req = urllib.request.Request("https://api.deepseek.com/v1/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())["choices"][0]["message"]["content"].strip().upper()


flagged = json.load(open(MV))["quarantine"]
review = {r["id"]: r for r in json.load(open(VIS + "/review-data.json"))}
pub = [i for i in flagged if review.get(i, {}).get("gate_decision") == "publish"]

agree = 0
disagree = []
for i in pub:
    r = review[i]
    manual = "METRIC" if i in HELD else "NOT"
    code = "METRIC" if deepseek(r.get("statement") or "").startswith("METRIC") else "NOT"
    if code == manual:
        agree += 1
    else:
        disagree.append((i, manual, code, (r.get("statement") or "")[:80]))

print(f"checked {len(pub)} published flagged; agreement with my manual run: {agree}/{len(pub)} ({100*agree/len(pub):.0f}%)\n")
print(f"disagreements ({len(disagree)}):")
for i, manual, code, s in disagree:
    print(f"  {i:13} manual={manual:6} code={code:6} | {s}")

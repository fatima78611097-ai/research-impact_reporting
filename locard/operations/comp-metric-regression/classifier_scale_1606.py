"""Run the metric/NOT semantic classifier over the WHOLE 1606 baseline (no small-int pre-filter)
to feel its at-scale behavior before wiring it into the gate: overall NOT rate, published-only
rate, overlap with the existing 121 small-int flags, and the NEW (value>20) flags to eyeball."""
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/home/ubuntu/research")
from lavandula.common.secrets import get_secret

KEY = get_secret("lavandula/deepseek/api_key")
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


VIS = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision"
MV = "/home/ubuntu/research/locard/operations/comp-metric-regression/measurable-value-quarantine.json"

rev = json.load(open(VIS + "/review-data.json"))
small_flags = set(json.load(open(MV))["quarantine"])


def classify(r):
    try:
        code = "METRIC" if deepseek(r.get("statement") or "").startswith("METRIC") else "NOT"
    except Exception as e:
        code = "ERR:" + type(e).__name__
    return r["id"], code


with ThreadPoolExecutor(max_workers=20) as ex:
    res = dict(ex.map(classify, rev))

by_id = {r["id"]: r for r in rev}
not_ids = [i for i, c in res.items() if c == "NOT"]
err = [i for i, c in res.items() if c.startswith("ERR")]
pub_not = [i for i in not_ids if by_id[i].get("gate_decision") == "publish"]
pub_total = sum(1 for r in rev if r.get("gate_decision") == "publish")
new_big = [i for i in not_ids if i not in small_flags
           and isinstance(by_id[i].get("value"), (int, float)) and abs(by_id[i]["value"]) > 20]

print(f"errors: {len(err)}")
print(f"NOT (all 1606):        {len(not_ids)}  ({100*len(not_ids)/len(rev):.1f}%)")
print(f"NOT (published 1433):  {len(pub_not)}  ({100*len(pub_not)/pub_total:.1f}% of published)")
print(f"overlap w/ 121 small-int flags: {len(set(not_ids) & small_flags)} of {len(small_flags)}")
print(f"NEW flags beyond small-int net (value>20): {len(new_big)}\n")
print("--- NEW value>20 flags (sample to judge real-catch vs false-reject) ---")
for i in sorted(new_big, key=lambda x: -abs(by_id[x].get("value") or 0))[:40]:
    r = by_id[i]
    print(f"  {i:13} val={str(r.get('value'))[:10]:>10} | {(r.get('statement') or '')[:84]}")

json.dump({"not_ids": not_ids, "pub_not": pub_not, "new_big": new_big, "results": res},
          open("/home/ubuntu/research/locard/operations/comp-metric-regression/classifier-scale-1606.json", "w"), indent=1)

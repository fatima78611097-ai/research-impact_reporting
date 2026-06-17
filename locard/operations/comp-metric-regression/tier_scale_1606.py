"""Run the TIER+VALIDITY prompt (code_tier_check.py's, which knows 'financial' is a valid tier) over
the whole 1606 — the real scale test of the is-it-a-metric + tier approach. Reports valid/non-metric
rate (all + published), tier distribution, overlap w/ the 121 small-int flags, and the financial-
protection check: of the 179 financials the BARE run wrongly rejected, how many this prompt rescues."""
import collections
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/home/ubuntu/research")
from lavandula.common.secrets import get_secret

KEY = get_secret("lavandula/deepseek/api_key")
VIS = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision"
MV = "/home/ubuntu/research/locard/operations/comp-metric-regression/measurable-value-quarantine.json"
BARE = "/home/ubuntu/research/locard/operations/comp-metric-regression/classifier-scale-1606.json"

SYSTEM = (
    "Classify ONE nonprofit-report metric. Return ONLY JSON {\"validity\":..,\"tier\":..,\"standalone\":..}.\n"
    "validity: valid_metric if it states a measured QUANTITY of something real; non_metric if it is a "
    "non-measurement (event-as-1, ranking, award/honor, date/year, duration/tenure '60 years', label/rating, "
    "multiplier 'doubled', forecast, or a qualitative claim with a spurious number).\n"
    "tier: outcome_impact (change/result for people) | reach_output (scale of service delivered / counts served / "
    "units produced) | capacity_input (resources, staff, vehicles, partners, locations, membership) | "
    "financial (revenue, expenses, funds raised, assets) | activity_count (things done/launched: events, programs "
    "launched) | non_metric.\n"
    "standalone: Y if a complete self-contained sentence understandable from the org mission + report year; "
    "P if partial/ambiguous (missing or unclear value/subject); N if a bare fragment."
)


def deepseek(user):
    body = json.dumps({"model": "deepseek-chat", "temperature": 0.0, "max_tokens": 120,
                       "messages": [{"role": "system", "content": SYSTEM},
                                    {"role": "user", "content": user}]}).encode()
    req = urllib.request.Request("https://api.deepseek.com/v1/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    out = json.loads(urllib.request.urlopen(req, timeout=60).read())["choices"][0]["message"]["content"].strip()
    if out.startswith("```"):
        out = out.split("```")[1].lstrip("json").strip()
    return json.loads(out)


rev = json.load(open(VIS + "/review-data.json"))
by_id = {r["id"]: r for r in rev}
small_flags = set(json.load(open(MV))["quarantine"])
bare = json.load(open(BARE))
bare_new_big = set(bare["new_big"])  # 179 financials the bare run rejected


def classify(r):
    try:
        c = deepseek(r.get("statement") or "")
        return r["id"], c.get("validity"), c.get("tier"), c.get("standalone")
    except Exception:
        return r["id"], "ERR", "ERR", "ERR"


with ThreadPoolExecutor(max_workers=20) as ex:
    out = list(ex.map(classify, rev))

res = {i: {"validity": v, "tier": t, "standalone": s} for i, v, t, s in out}
err = [i for i in res if res[i]["validity"] == "ERR"]
non = [i for i in res if res[i]["validity"] == "non_metric"]
pub_total = sum(1 for r in rev if r.get("gate_decision") == "publish")
pub_non = [i for i in non if by_id[i].get("gate_decision") == "publish"]
tiers = collections.Counter(res[i]["tier"] for i in res if res[i]["validity"] != "ERR")
rescued = [i for i in bare_new_big if res.get(i, {}).get("validity") == "valid_metric"]

print(f"errors: {len(err)}")
print(f"NON-METRIC (all 1606):       {len(non)}  ({100*len(non)/len(rev):.1f}%)")
print(f"NON-METRIC (published 1433): {len(pub_non)}  ({100*len(pub_non)/pub_total:.1f}% of published)")
print(f"overlap w/ 121 small-int flags: {len(set(non) & small_flags)} of {len(small_flags)}")
print(f"\nFINANCIAL-PROTECTION CHECK: of the {len(bare_new_big)} financials the BARE run rejected,")
print(f"  this tier prompt rescues to valid_metric: {len(rescued)}  ({100*len(rescued)/max(1,len(bare_new_big)):.0f}%)")
print(f"\ntier distribution (valid metrics):")
for t, c in tiers.most_common():
    print(f"  {str(t):16} {c:5}  ({100*c/sum(tiers.values()):.1f}%)")
print(f"\n--- sample NON-METRIC flags with value>20 (judge real-catch vs false-reject) ---")
big_non = [i for i in non if isinstance(by_id[i].get("value"), (int, float)) and abs(by_id[i]["value"]) > 20]
for i in sorted(big_non, key=lambda x: -abs(by_id[x].get("value") or 0))[:25]:
    r = by_id[i]
    print(f"  {i:13} val={str(r.get('value'))[:10]:>10} | {(r.get('statement') or '')[:80]}")

json.dump(res, open("/home/ubuntu/research/locard/operations/comp-metric-regression/tier-scale-1606.json", "w"), indent=1)

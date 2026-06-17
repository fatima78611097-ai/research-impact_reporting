"""Code tiering vs hand tiering — calibration diff.

Tiers the same 40 metrics by code (DeepSeek, text-only — apples-to-apples with the hand pass),
then diffs against the locked hand labels (hand_tier_labels.json). Reports agreement on
validity and tier, and lists every disagreement (the useful part).
"""
import json
import sys
import urllib.request

sys.path.insert(0, "/home/ubuntu/research")
from lavandula.common.secrets import get_secret

KEY = get_secret("lavandula/deepseek/api_key")
HAND = "/home/ubuntu/research/locard/operations/comp-metric-regression/hand_tier_labels.json"
DATA = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/review-data.json"

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


def main():
    hand = json.load(open(HAND))
    stmts = {r["id"]: (r.get("statement") or "") for r in json.load(open(DATA))}
    ids = [k for k in hand if not k.startswith("_")]
    v_agree = t_agree = 0
    disagree = []
    for i in ids:
        h = hand[i]
        try:
            c = deepseek(stmts[i])
        except Exception as e:
            c = {"validity": "ERR", "tier": "ERR", "standalone": "ERR"}
        va = h["v"] == c.get("validity")
        ta = h["tier"] == c.get("tier")
        v_agree += va
        t_agree += ta
        if not (va and ta):
            disagree.append((i, h, c, stmts[i][:80]))
    n = len(ids)
    print(f"validity agreement: {v_agree}/{n} ({100*v_agree/n:.0f}%)")
    print(f"tier agreement    : {t_agree}/{n} ({100*t_agree/n:.0f}%)")
    print(f"\ndisagreements ({len(disagree)}):")
    for i, h, c, s in disagree:
        flags = []
        if h["v"] != c.get("validity"):
            flags.append(f"VALIDITY hand={h['v']} code={c.get('validity')}")
        if h["tier"] != c.get("tier"):
            flags.append(f"tier hand={h['tier']} code={c.get('tier')}")
        print(f"  {i:13} {' | '.join(flags)}")
        print(f"      {s}")


if __name__ == "__main__":
    main()

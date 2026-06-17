"""Item 1 — extend is-a-metric to the remaining reject classes (value>20 tail the small-int rule
can't reach): tenure/duration, year-as-value, forecast/projection, population factoid.

Two-stage (same harness pattern): stage 1 = narrow regex over the CURRENT published set -> small
candidate pool; stage 2 = DeepSeek confirms NOT-a-metric for those four classes only (conservative:
when in doubt, METRIC). Validation gate vs the vision fixture: if the flips would drop more than
MAX_FALSE_REJECTS vision-confirmed real metrics, STOP and report instead of applying.
On pass: writes flips into review-data.json (reason 'not_a_metric_rule') and prints before/after.
"""
import json
import re
import shutil
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/home/ubuntu/research")
from lavandula.common.secrets import get_secret

KEY = get_secret("lavandula/deepseek/api_key")
VIS = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision"
B = "/home/ubuntu/research/locard/operations/comp-metric-regression/"
MAX_FALSE_REJECTS = 2

TENURE = re.compile(r"\b(\d+|\w+ty|\w+th) years? (of|in|serving|of service)|celebrat\w+ \d+ years"
                    r"|\b\d+(st|nd|rd|th) (anniversary|year)|(\b|-)(\d+)-year (history|legacy|tradition)"
                    r"|for (over|more than|nearly) \d+ years|has been .{0,40}(since|for) (19|20)\d{2}"
                    r"|\bsince (19|20)\d{2}\b|turned \d+ years old", re.I)
def YEARVAL(r):
    """Value is in the year range AND the statement prints it as a BARE year token (no thousands
    comma). '2,017 children' = a count (comma) -> not a candidate; 'in March 2017' = a year."""
    v = r.get("value")
    if not _isyear(v):
        return False
    bare = str(int(float(v)))
    # block digits/commas around it (a count like "2,017") and decimals ("2017.5"),
    # but ALLOW a sentence-final period ("in March 2017.")
    return bool(re.search(rf"(?<![\d,.]){bare}(?![\d,])(?!\.\d)", r.get("statement") or ""))
FORECAST = re.compile(r"\bwill \w+|\bby 20\d{2}\b|\bgoal of\b|\baims? to\b|\bplans? to\b|\bprojected\b"
                      r"|\bexpected to\b|\bcould \w+|\btoward (its|a|the) .{0,30}(goal|target)", re.I)
DURATION_EP = re.compile(r"\b(stayed?|spent|remained|lived|housed|sheltered|works?|working)\b.{0,40}\b\d+([\s-]+)(day|week|month|night|hour)s?\b"
                         r"|\b\d+[\s-]+(day|week|month|night|hour)s?\s+(at|in)\s+(the\s+)?(shelter|program|facility|home)"
                         r"|\bnamed\s+[A-Z][a-z]+\b.{0,80}\b\d+", re.I)
FACTOID = re.compile(r"\b(1|one) in (\d+|\w+)\b|of (all )?(americans|u\.?s\.? adults|adults|women|men|children"
                     r"|people|youth) (are|have|experience|live|face)|\bnationally\b|in the (u\.?s\.?|united states)\b", re.I)


def _isyear(v):
    try:
        return 1900 <= float(v) <= 2099 and float(v) == int(float(v))
    except (TypeError, ValueError):
        return False


def stage1(r):
    s = r.get("statement") or ""
    hits = []
    if TENURE.search(s):
        hits.append("tenure")
    if YEARVAL(r):
        hits.append("year-as-value")
    if FORECAST.search(s):
        hits.append("forecast")
    if FACTOID.search(s):
        hits.append("factoid")
    if DURATION_EP.search(s):
        hits.append("episode-duration")
    return hits


SYSTEM = (
    "A nonprofit-report claim was pre-flagged as possibly NOT a metric. You are judging ONE specific "
    "VALUE within the claim - decide what THAT VALUE measures, not what else the sentence mentions.\n"
    "NOT = the VALUE itself is one of exactly these four types:\n"
    "  1) TENURE/DURATION: the value is years of operation/service/history ('45 years of service', "
    "'118-year history') OR the length of ONE beneficiary's personal episode ('Anthony stayed 60 days "
    "at the shelter'). But AGGREGATE service volume IS a metric ('100 hours of volunteer service "
    "delivered', '5,010 night hostings'). If the sentence mentions an anniversary but the VALUE is a "
    "count of people/calls/dollars, that's a METRIC.\n"
    "  2) YEAR-AS-VALUE: the value is a calendar year ('launched in March 2017' -> value 2017).\n"
    "  3) FORECAST: the value is a future goal/projection NOT yet achieved ('will rise 14% by 2045', "
    "'$15 million goal'). 'Surpassed/met its goal of X' means X WAS achieved -> METRIC.\n"
    "  4) POPULATION FACTOID: the value measures the general population/society, not this org's own "
    "work ('1 in 3 women experience...', '85% of brain growth happens...'). The org's own service "
    "numbers in the same sentence are still METRICs.\n"
    "METRIC = the value is the org's own measured quantity (people served, dollars raised/spent, percent "
    "improved, units delivered), even if a year, 'since', or an anniversary appears as context "
    "('served 500 families in 2022' -> 500 is a METRIC).\n"
    "When in doubt, reply METRIC.\nReply one word: METRIC or NOT."
)


def deepseek(user):
    body = json.dumps({"model": "deepseek-chat", "temperature": 0.0, "max_tokens": 6,
                       "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}).encode()
    for _ in range(3):
        try:
            req = urllib.request.Request("https://api.deepseek.com/v1/chat/completions", data=body,
                                         headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(req, timeout=60).read())["choices"][0]["message"]["content"].strip().upper()
        except Exception:
            pass
    return "ERR"


rev = json.load(open(VIS + "/review-data.json"))
pub = [r for r in rev if r.get("gate_decision") == "publish"]
cands = [(r, stage1(r)) for r in pub]
cands = [(r, h) for r, h in cands if h]
print(f"published: {len(pub)}; stage-1 candidates: {len(cands)}")

def ask(rh):
    r, hits = rh
    user = f"VALUE = {r.get('value')}\nCLAIM: {r.get('statement') or ''}"
    return r["id"], deepseek(user), hits


with ThreadPoolExecutor(max_workers=20) as ex:
    verdicts = list(ex.map(ask, cands))
flag = {i for i, v, _ in verdicts if v.startswith("NOT")}
print(f"stage-2 confirmed NOT-a-metric: {len(flag)}")

# Operator adjudication 2026-06-10 of the 8 vision-real disagreements ("apply all 34"):
# - KEEP (genuine stage-2 judge errors; achieved goal / real count beside an anniversary):
OPERATOR_KEEP = {"a00adaa5:0", "b60a49eb:8"}
# - REJECT confirmed per the guidance doc (forecast/aspiration, population stats, campaign target),
#   where vision graded leniently:
OPERATOR_REJECT = {"040633b7:4", "370cd021:9", "6b3b6732:7", "efd61fea:16", "db75f054:1", "e136ede4:0"}
flag -= OPERATOR_KEEP

# ---- validation gate vs the vision fixture ----
# Factoid exception (operator + guidance-doc ruling 2026-06-10): population factoids ARE rejects per
# the doc's core definition ("measures something THE NONPROFIT did"); vision graded them leniently.
# A vision-metric flagged ONLY as factoid is a doc-override, not a false-reject.
fix = {r["id"]: r for r in json.load(open(B + "fixtures/vision-ground-truth.json"))["records"]}
hits_by_id = {i: h for i, v, h in verdicts}
VALUE = {"outcome_impact", "reach_output", "capacity_input", "financial"}
real = lambda r: r["vision"]["is_metric"] == "metric" and r["vision"]["tier"] in VALUE
vision_real = [i for i in flag if i in fix and real(fix[i])]
overrides = [i for i in vision_real if hits_by_id.get(i) == ["factoid"] or i in OPERATOR_REJECT]
false_rejects = [i for i in vision_real if i not in overrides]
good_catches = [i for i in flag if i in fix and not real(fix[i])]
print(f"\nVALIDATION vs fixture: {len(good_catches)} correct catches, "
      f"{len(overrides)} doc-override factoids, {len(false_rejects)} false-rejects (real metrics)")
for i in overrides:
    print(f"   doc-override {i:13} | {(fix[i]['statement'] or '')[:70]}")
for i in false_rejects:
    print(f"   FALSE-REJECT {i:13} | {(fix[i]['statement'] or '')[:70]}")

if len(false_rejects) > MAX_FALSE_REJECTS:
    print(f"\nGATE FAILED (> {MAX_FALSE_REJECTS} false-rejects) — NOT applied. Tune and re-run.")
    sys.exit(1)

# ---- apply to the canonical set ----
shutil.copy(VIS + "/review-data.json", VIS + "/review-data.before-item1.json")
n = 0
for r in rev:
    if r["id"] in flag and r.get("gate_decision") == "publish":
        r["gate_decision"] = "quarantine"
        r["gate_reason"] = "not_a_metric_rule"
        n += 1
json.dump(rev, open(VIS + "/review-data.json", "w"))
json.dump({"check": "item1-nonmetric-rules", "flagged": sorted(flag),
           "by": {i: h for i, v, h in verdicts if i in flag}}, open(B + "item1-flags.json", "w"), indent=1)
newpub = sum(1 for r in rev if r["gate_decision"] == "publish")
print(f"\nAPPLIED: {n} publish -> quarantine. Set now {newpub} publish / {len(rev)-newpub} quarantine.")
print("snapshot: review-data.before-item1.json")

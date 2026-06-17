"""Validate coordinate re-grouping against the vision fixture. Two questions:
 1. SAFETY (no-op on clean): on vision-CORRECT metrics, does re-grouping agree with the model's
    (correct) subject? Disagreement = a regression it would introduce.
 2. FIX (on mispairs): on vision-WRONG metrics, does re-grouping produce a DIFFERENT subject than the
    model's wrong one (i.e., catch/correct it)?
Matching is by value (find the metric's number on its page) + word-overlap between re-grouping's label
and the model's subject. Abstentions are counted separately (safe: left as-is).
"""
import json
import re
import sys
import collections

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import regroup
from lavandula.common.db import make_app_engine
from sqlalchemy import text, bindparam

B = "/home/ubuntu/research/locard/operations/comp-metric-regression/"
RES = {"batch1-clean": B + "scale100-results-tuned.json", "test2": B + "scale-test2-results.json"}
results = {k: json.load(open(v)) for k, v in RES.items()}
sha8_to_full = {s[:8]: s for r in results.values() for s in r}
fix = {r["id"]: r for r in json.load(open(B + "fixtures/vision-ground-truth.json"))["records"]}
review = {r["id"]: r for r in json.load(open("/home/ubuntu/research/locard/spikes/0064/eval_set/vision/review-data.json"))}
try:
    ADJUDICATED = {v["id"]: v["verdict"] for v in json.load(open(B + "regroup-adjudication-result.json"))["result"]}
except FileNotFoundError:
    ADJUDICATED = {}


def words(s):
    # 5-char prefix stems so paraphrase pairs match (family/families, grocery/groceries, served/serving)
    return {w[:5] for w in re.findall(r"[a-z]{4,}", (s or "").lower())}


def numkey(v):
    return re.sub(r"[^\d]", "", str(v))


# metrics with a value on a page, published, that vision graded (correct or wrong pairing), value present
pool = [i for i, r in fix.items()
        if r["vision"]["value_on_page"] == "yes" and r["vision"]["subject_pairing"] in ("correct", "wrong")
        and review.get(i, {}).get("gate_decision") in ("publish", "quarantine")]
docs = sorted({sha8_to_full[review[i]["sha8"]] for i in pool if review[i]["sha8"] in sha8_to_full})

eng = make_app_engine()
pg_cache = {}
with eng.connect() as conn:
    def get_pairs(full, page):
        if (full, page) not in pg_cache:
            by = regroup.raw_locations(conn, full)
            pg_cache[(full, page)] = regroup.pair_from_elements(regroup.normalize(by.get(page, [])))
        return pg_cache[(full, page)]

    res = collections.Counter()
    misses = []
    for i in pool:
        r = review[i]
        full = sha8_to_full.get(r["sha8"])
        page = r.get("v_page") or r.get("page")
        if not full or not page:
            continue
        pairs = get_pairs(full, int(page))
        vk = numkey(r.get("value"))
        match = next(({"n": n, **d} for n, d in pairs.items() if numkey(n) == vk and vk), None)
        vision_wrong = fix[i]["vision"]["subject_pairing"] == "wrong"
        subj = r.get("subject") or ""
        if not match:
            res["no_number_match"] += 1
            continue
        if match["label"] is None:
            res[("wrong" if vision_wrong else "correct", "abstain")] += 1
            continue
        agree = bool(words(match["label"]) & words(subj))
        # page-truth adjudication (regroup-adjudication-result.json) overrides word-matching:
        # BOTH_SAME = same caption, different fragments -> counts as agree; REGROUP = regroup was
        # right and the model wrong -> agree-with-truth (not a regression / an actual catch).
        if i in ADJUDICATED and ADJUDICATED[i] in ("BOTH_SAME", "REGROUP"):
            agree = True
        bucket = "wrong" if vision_wrong else "correct"
        conf = match["confidence"]
        res[(bucket, "agree" if agree else "differ")] += 1
        res[(bucket, "agree" if agree else "differ", conf)] += 1
        if not vision_wrong and not agree:
            misses.append((i, conf, r.get("value"), subj[:28], match["label"][:38]))

print(f"pool: {len(pool)} metrics across {len(docs)} docs\n")
cc = res[("correct", "agree")] + res[("correct", "differ")] + res[("correct", "abstain")]
wc = res[("wrong", "agree")] + res[("wrong", "differ")] + res[("wrong", "abstain")]
print("=== SAFETY (vision-CORRECT metrics — re-grouping must NOT contradict) ===")
print(f"  agree w/ model: {res[('correct','agree')]}   differ (would REGRESS): {res[('correct','differ')]}   abstain (safe): {res[('correct','abstain')]}   total {cc}")
print("\n=== FIX (vision-WRONG metrics — re-grouping should DIFFER / correct) ===")
print(f"  differ from wrong model (caught): {res[('wrong','differ')]}   agree w/ wrong (missed): {res[('wrong','agree')]}   abstain: {res[('wrong','abstain')]}   total {wc}")
print(f"\nno number match on page: {res['no_number_match']}")
print("\n=== HIGH-CONFIDENCE ONLY (the surgical operating point) ===")
hc_safe_ok = res[("correct", "agree", "high")]; hc_safe_bad = res[("correct", "differ", "high")]
hc_fix = res[("wrong", "differ", "high")]; hc_fix_miss = res[("wrong", "agree", "high")]
print(f"  clean metrics, high-conf: agree {hc_safe_ok}  REGRESS {hc_safe_bad}  -> regression rate {100*hc_safe_bad/max(1,hc_safe_ok+hc_safe_bad):.0f}%")
print(f"  mispairs,      high-conf: caught {hc_fix}  missed {hc_fix_miss}")
print(f"\n--- would-regress cases (conf shown) ---")
for i, conf, v, subj, lab in misses[:18]:
    print(f"   [{conf:7}] {i:13} val={str(v)[:8]:>8} model={subj!r:30} regroup={lab!r}")

"""Stage-2 qualifier (per locked §2a): cheap DeepSeek pass over impact_story CANDIDATES only,
applying the fine rules stage-1 deliberately omits — specific-beneficiary vs population/aggregate
(the audit's dominant failure, 18/24 errors), relief-episode guard, lore exception, quote-only rule.

Modes:
  --validate   run on the 60 audited candidates, score against the fixture truth (default)
  --full       run on all impact_story candidates in run-1, write stage-2 verdicts
"""
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/home/ubuntu/research")
from lavandula.common.secrets import get_secret

B = "/home/ubuntu/research/locard/operations/story-definition/"
KEY = get_secret("lavandula/deepseek/api_key")
FULL = "--full" in sys.argv

SYSTEM = (
    "You are verifying a CANDIDATE impact story extracted from a nonprofit report. Decide its true "
    "class with these tests, in order:\n"
    "1. SPECIFIC BENEFICIARY: the central beneficiary must be a specific, identifiable person, "
    "family, group, community, or place shown in its own situation — NOT a population category, "
    "count, or cohort. 'Maria', 'one family in our housing program', 'the watershed' PASS. "
    "'refugee families', '630 members', 'three farming operations', 'students in our program' FAIL.\n"
    "2. CHANGE: the passage answers 'what is different now?' for THAT beneficiary — an observable "
    "state change, avoided harm, or sustained improvement, presented as a true past/current event. "
    "Immediate relief counts only as a specific episode ('Maria received a hot meal after three days "
    "without food'), never routine delivery.\n"
    "3. Routing when it fails: primarily aggregate delivery/counts or group-level outcomes -> "
    "service_episode. Subject is a donor/funder/volunteer -> donor_story. Sentiment/praise without a "
    "concrete change -> testimonial. Program description, org-capability narrative, founder/origin "
    "lore without a report-year outcome, future/hypothetical -> reject.\n"
    "4. A quote-only passage qualifies only if the quote or its immediate context supplies "
    "beneficiary + intervention + change.\n"
    'Reply ONLY JSON: {"verdict": "impact_story|service_episode|testimonial|donor_story|reject", '
    '"reason": "<one short clause>"}'
)


def deepseek(user):
    body = json.dumps({"model": "deepseek-chat", "temperature": 0.0, "max_tokens": 120,
                       "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request("https://api.deepseek.com/v1/chat/completions", data=body,
                                         headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
            out = json.loads(urllib.request.urlopen(req, timeout=90).read())["choices"][0]["message"]["content"].strip()
            if out.startswith("```"):
                out = out.split("```")[1].lstrip("json").strip()
            return json.loads(out)
        except Exception as e:
            if attempt == 2:
                return {"verdict": "ERR", "reason": str(e)[:80]}
            time.sleep(4)


def qualify(item):
    user = (f"CANDIDATE (stage-1 said impact_story, confidence {item.get('confidence')}):\n"
            f"title: {item.get('title')}\nprimary_beneficiary: {item.get('primary_beneficiary')}\n"
            f"what_changed: {item.get('what_changed')}\n\nVERBATIM PASSAGE:\n{(item.get('span_text') or '')[:6000]}")
    return deepseek(user)


if not FULL:
    # validate against the audited fixture
    sample = {(x["sha8"], x["title"]): x for x in json.load(open(B + "story-audit-sample.json"))}
    truth = json.load(open(B + "story-audit-result.json"))["result"]["detail"]
    cands = []
    for t in truth:
        if t["sys_class"] != "impact_story":
            continue
        key = next((k for k in sample if k[0] == t["sha8"] and sample[k]["classification"] == "impact_story"), None)
        # match by index order instead: sample items are unique by (sha8,title)
    # robust join: walk sample items, find their truth rows by idx order
    items = json.load(open(B + "story-audit-sample.json"))
    pairs = []
    for t in truth:
        it = items[t["idx"]]
        if it["classification"] == "impact_story":
            pairs.append((it, t))
    print(f"validating stage-2 on {len(pairs)} audited impact candidates")
    with ThreadPoolExecutor(max_workers=12) as ex:
        verdicts = list(ex.map(lambda p: (p[1], qualify(p[0])), pairs))
    import collections
    res = collections.Counter()
    for t, v in verdicts:
        s2 = v.get("verdict")
        true = t["correct_class"]
        keep = s2 == "impact_story"
        really = true == "impact_story"
        res[("keep" if keep else "cut", "true_story" if really else "not_story")] += 1
    kept_good = res[("keep", "true_story")]; kept_bad = res[("keep", "not_story")]
    cut_good = res[("cut", "true_story")]; cut_bad = res[("cut", "not_story")]
    print(f"\nSTAGE-2 RESULT vs fixture truth:")
    print(f"  kept &真 story:   {kept_good}   (good keeps)")
    print(f"  kept & NOT:      {kept_bad}   (precision leaks)")
    print(f"  cut & NOT:       {cut_bad}   (good cuts)")
    print(f"  cut & true story:{cut_good}   (recall cost)")
    tot_kept = kept_good + kept_bad
    print(f"\n  precision after stage-2: {kept_good}/{tot_kept} = {100*kept_good/max(1,tot_kept):.0f}%  (was 60% raw / 76% high-conf)")
    print(f"  true stories retained:   {kept_good}/{kept_good+cut_good} = {100*kept_good/max(1,kept_good+cut_good):.0f}%")
    print(f"\n--- recall losses (true stories stage-2 cut) ---")
    for t, v in verdicts:
        if t["correct_class"] == "impact_story" and v.get("verdict") != "impact_story":
            print(f"  [{v.get('verdict'):15}] {t['sha8']} | {v.get('reason','')[:80]}")
else:
    run1 = json.load(open(B + "story-extraction-run1.json"))
    cands = [(sha, r) for sha, rl in run1.items() if isinstance(rl, list)
             for r in rl if r.get("classification") == "impact_story"]
    # span_text not stored in run1 records; rebuild from render
    sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
    import render
    from lavandula.common.db import make_app_engine
    eng = make_app_engine()
    cache = {}
    enriched = []
    with eng.connect() as conn:
        for sha, r in cands:
            if sha not in cache:
                try:
                    _, _, cache[sha] = render.render_tagged(conn, sha)
                except Exception:
                    cache[sha] = None
            idmap = cache[sha]
            if not idmap:
                continue
            try:
                i0, i1 = int(r["span_start"][1:]), int(r["span_end"][1:])
                r["span_text"] = " ".join((idmap.get(f"t{i}") or {}).get("text", "") for i in range(i0, i1 + 1))
            except Exception:
                r["span_text"] = ""
            enriched.append((sha, r))
    with ThreadPoolExecutor(max_workers=12) as ex:
        out = list(ex.map(lambda p: (p[0], p[1], qualify(p[1])), enriched))
    results = [{"sha": s, "title": r.get("title"), "stage1_confidence": r.get("confidence"),
                "stage2_verdict": v.get("verdict"), "stage2_reason": v.get("reason")} for s, r, v in out]
    json.dump(results, open(B + "stage2-verdicts.json", "w"), indent=1)
    import collections
    print("stage-2 over all candidates:", dict(collections.Counter(x["stage2_verdict"] for x in results)))
    print(f"saved {B}stage2-verdicts.json")

"""Vision pairing pass — STEP 3 of 3: confirm value<->label pairing by LOOKING at the page.

The gate (regen_2 / slot_render) pairs each metric's number with a label by marker
geometry. Geometry can't tell whether a physically-close label is the SEMANTICALLY
right one. This pass asks a vision model, per published *split* metric (value and
label from different markers), looking at the boxed source page:

    does the BLUE-boxed label actually describe what the RED-boxed number measures?

2-of-3 majority vote (gemini-2.5-flash). On a majority DISAGREE the metric is
quarantined (decided_by="vision") — matches the precision-over-recall bar: a wrong
pairing is a costly false-publish; dropping a real one is recoverable. The vision
verdict (+ the model's suggested correct label) is written onto every judged record.

Scope: the 81 published split metrics (value_ref != subject_ref). Inline metrics
(same marker = same cell) have no pairing to get wrong and are skipped.

    python3 regen_3_vision_pairing.py            # judge + apply (writes new-review-data.json)
    python3 regen_3_vision_pairing.py --dry-run  # judge only, write the log, DON'T touch the viewer data

Reuses the REST/SSM Gemini call from bake-off/run_bakeoff.py.
"""
import argparse
import base64
import datetime
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

VDIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(VDIR, "new-review-data.json")
LOG = os.path.join(VDIR, "vision_pairing_results.json")
MODEL = "gemini-2.5-flash"
PASSES = 3
VOTE_NEEDED = 2          # >=2 of 3 "match" to stay published
TEMP = 0.4               # spread for a meaningful majority vote


def _key():
    k = os.environ.get("GEMINI_API_KEY")
    if k:
        return k
    sys.path.insert(0, "/home/ubuntu/research")
    from lavandula.common.secrets import get_secret
    return get_secret("gemini-api-key")   # SSM /cloud2.lavandulagroup.com/gemini-api-key


PROMPT = """You are auditing one extracted statistic from a nonprofit's report page.

On the page image:
- the RED box outlines a NUMBER our system extracted as a metric value.
- the BLUE box outlines the LABEL our system paired with that number (what it claims the number measures).

Look at the page layout. Decide ONLY this: does the BLUE-boxed label correctly describe what the RED-boxed number measures? A label is correct if a reader, seeing the number in its place on the page, would naturally read THAT label as its meaning. It is WRONG if the number's real label is a different nearby piece of text (e.g. the label belongs to an adjacent column, row, or callout).

Reply with ONLY a JSON object, no markdown fences, no prose:
{"match": true|false, "correct_label": "<the label you believe is right, or the blue one if it's correct>", "reason": "<one clause, max 8 words>"}"""


def gemini(prompt, img_path, key, temperature):
    with open(img_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}, {"inline_data": {"mime_type": "image/png", "data": b64}}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 1500},
    }).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={key}"
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            r = json.loads(urllib.request.urlopen(req, timeout=120).read())
            cand = (r.get("candidates") or [{}])[0]
            return "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        except Exception as e:
            if attempt == 3:
                return f"ERR:{e}"
            time.sleep(2 * (attempt + 1))


def parse(text):
    """-> (match: bool|None, correct_label: str, reason: str). None match = unparseable."""
    import re
    t = text.strip().replace("```json", "").replace("```", "").strip()
    try:
        j = json.loads(re.search(r"\{.*\}", t, re.S).group(0))
        return bool(j.get("match")), str(j.get("correct_label", "")), str(j.get("reason", ""))
    except Exception:
        # truncated/unfenced fallback: read the match flag directly if present
        mm = re.search(r'"match"\s*:\s*(true|false)', t, re.I)
        if mm:
            lab = re.search(r'"correct_label"\s*:\s*"([^"]*)"', t)
            return mm.group(1).lower() == "true", (lab.group(1) if lab else ""), t[:120]
        return None, "", t[:120]


def judge_one(rec, key):
    img = os.path.join(VDIR, rec["img"])
    votes = []
    for _ in range(PASSES):
        m, lab, why = parse(gemini(PROMPT, img, key, TEMP))
        votes.append({"match": m, "correct_label": lab, "reason": why})
    yes = sum(1 for v in votes if v["match"] is True)
    no = sum(1 for v in votes if v["match"] is False)
    parsed = yes + no
    # default-safe: if the model couldn't be parsed enough to form a vote, do NOT quarantine
    match = True if parsed < VOTE_NEEDED else (yes >= VOTE_NEEDED)
    # pick a suggested correct label from a dissenting vote
    sugg = next((v["correct_label"] for v in votes if v["match"] is False and v["correct_label"]), "")
    return {"match": match, "yes": yes, "no": no, "parsed": parsed,
            "suggested_label": sugg, "votes": votes}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="judge + log only; don't modify new-review-data.json")
    args = ap.parse_args()
    key = _key()

    data = json.load(open(DATA))
    split = [r for r in data if r.get("gate_decision") == "publish" and not r.get("same_marker")]
    print(f"judging {len(split)} published split metrics with {MODEL} ({PASSES} passes, {VOTE_NEEDED}-of-{PASSES} vote)\n")

    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(lambda r: (r, judge_one(r, key)), split))

    flipped, undetermined = [], []
    for rec, v in results:
        rec["vision_pairing"] = {"match": v["match"], "yes": v["yes"], "no": v["no"],
                                 "parsed": v["parsed"], "suggested_label": v["suggested_label"],
                                 "model": MODEL, "passes": PASSES}
        if v["parsed"] < VOTE_NEEDED:
            undetermined.append(rec)
        elif not v["match"]:
            flipped.append(rec)
            rec["gate_decision"] = "quarantine"
            rec["decided_by"] = "vision"
            rec["decided_detail"] = f"vision: label mispaired (correct: {v['suggested_label'] or '?'})"
        tag = "QUARANTINE" if (v["parsed"] >= VOTE_NEEDED and not v["match"]) else \
              ("undetermined" if v["parsed"] < VOTE_NEEDED else "ok")
        print(f"  {rec['sha8']} m{rec['id'].split(':')[-1]:>3}  {v['yes']}/{v['parsed']} match  [{tag}]"
              + (f"  -> {v['suggested_label'][:40]}" if tag == 'QUARANTINE' else ""))

    json.dump([{"id": r["id"], **v} for r, v in results], open(LOG, "w"), indent=1, default=str)
    pub = sum(1 for r in data if r["gate_decision"] == "publish")
    print(f"\njudged {len(split)}  ->  {len(flipped)} quarantined by vision, {len(undetermined)} undetermined (kept)")
    print(f"new totals: {pub} publish / {len(data) - pub} quarantine")

    if args.dry_run:
        print("\n--dry-run: new-review-data.json NOT modified. Verdicts in vision_pairing_results.json")
        return
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = DATA.replace(".json", f".before-vision-{stamp}.json")
    json.dump(json.load(open(DATA)), open(bak, "w"))   # backup the pre-write file
    json.dump(data, open(DATA, "w"), default=str)
    print(f"\nwrote new-review-data.json (backup: {os.path.basename(bak)})")


if __name__ == "__main__":
    main()

"""Deterministic span check for story extraction run-1 (the story gate, stage 0).

Per story record: cited span markers EXIST in the doc's idmap, are text markers, start<=end, the
span is a sane size, and the span text is narrative (word-bearing, not numbers/garble). Also checks
the structured fields' fidelity: primary_beneficiary words appear in the span text (anti-fabrication,
same word-overlap idea as the metric gate's subject check).
"""
import json
import re
import sys
import collections

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import render
from lavandula.common.db import make_app_engine

B = "/home/ubuntu/research/locard/operations/story-definition/"
d = json.load(open(B + "story-extraction-run1.json"))


def stems(s):
    return {w[:5] for w in re.findall(r"[a-z]{4,}", (s or "").lower())}


def midx(ref):
    return int(ref[1:]) if ref and ref[0] == "t" and ref[1:].isdigit() else None


eng = make_app_engine()
res = collections.Counter()
problems = []
checked = 0
with eng.connect() as conn:
    for sha, recs in d.items():
        if not isinstance(recs, list) or not recs:
            continue
        try:
            _, _, idmap = render.render_tagged(conn, sha)
        except Exception:
            continue
        for r in recs:
            if r.get("classification") != "impact_story":
                continue
            checked += 1
            s0, s1 = r.get("span_start"), r.get("span_end")
            i0, i1 = midx(s0), midx(s1)
            if i0 is None or i1 is None:
                res["bad_ref_format"] += 1
                problems.append((sha[:8], r.get("title"), f"refs {s0}..{s1}"))
                continue
            if s0 not in idmap or s1 not in idmap:
                res["ref_not_in_doc"] += 1
                problems.append((sha[:8], r.get("title"), f"missing {s0 if s0 not in idmap else s1}"))
                continue
            if i1 < i0 or (i1 - i0) > 12:
                res["span_shape"] += 1
                problems.append((sha[:8], r.get("title"), f"span {s0}..{s1}"))
                continue
            text = " ".join((idmap.get(f"t{i}") or {}).get("text", "") for i in range(i0, i1 + 1))
            if len(re.findall(r"[A-Za-z]{3,}", text)) < 12:
                res["span_not_narrative"] += 1
                problems.append((sha[:8], r.get("title"), f"thin span text: {text[:60]!r}"))
                continue
            ben = stems(r.get("primary_beneficiary"))
            if ben and not (ben & stems(text)) and r.get("primary_beneficiary", "").lower() not in text.lower():
                res["beneficiary_not_in_span"] += 1
                problems.append((sha[:8], r.get("title"), f"beneficiary {r.get('primary_beneficiary')!r} not in span"))
                continue
            res["ok"] += 1

print(f"impact stories span-checked: {checked}")
for k, v in res.most_common():
    print(f"  {k:24} {v:4}  ({100*v/checked:.1f}%)")
print(f"\n--- problems ({len(problems)}) ---")
for sha8, title, why in problems[:25]:
    print(f"  {sha8} {str(title)[:40]:40} | {why}")
json.dump([{"sha8": a, "title": b, "why": c} for a, b, c in problems],
          open(B + "span-check-problems.json", "w"), indent=1)

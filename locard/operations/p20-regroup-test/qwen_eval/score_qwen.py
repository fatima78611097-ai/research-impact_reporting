"""Score qwen-raw.json against eval-set truth. No pass bar — reports raw numbers + failures
for operator review: accuracy by source & mode, plus every miss with truth vs model output.
Runs anywhere (no GPU).
"""
import json
import os
import re
import sys
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
items = json.load(open(f"{HERE}/eval-set.json"))
raw = json.load(open(f"{HERE}/qwen-raw.json"))


def stems(s):
    return {w[:5] for w in re.findall(r"[a-z]{4,}", (s or "").lower())}


def numkey(v):
    s = str(v or "").strip()
    try:
        f = float(s.replace(",", "").replace("$", "").replace("%", "").replace("+", ""))
        if f == int(f):
            return str(int(f))
    except ValueError:
        pass
    return re.sub(r"[^\d]", "", s)


def parse_full(txt):
    if not txt or txt.startswith("ERR"):
        return None
    m = re.search(r"\[.*\]", txt, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def caption_match(truth, got):
    st, sg = stems(truth), stems(got)
    if not st or not sg:
        return False
    inter = len(st & sg)
    return inter >= 2 or inter >= len(st) * 0.5 or inter >= len(sg) * 0.5 and inter >= 1


res = collections.Counter()
misses = []
for it in items:
    src = it["source"]
    # FULL mode: find the value in the page's extracted list
    fr = parse_full(raw.get("full", {}).get(it["img"]))
    if fr is not None:
        hit = next((x for x in fr if isinstance(x, dict) and numkey(x.get("value")) == numkey(it["value"])), None)
        if hit is None:
            res[(src, "full", "value_missed")] += 1
        elif caption_match(it["truth"], str(hit.get("caption", ""))):
            res[(src, "full", "correct")] += 1
        else:
            res[(src, "full", "wrong_caption")] += 1
            misses.append(("full", src, it["value"], it["truth"][:60], str(hit.get("caption", ""))[:60]))
    else:
        res[(src, "full", "parse_fail")] += 1
    # TARGET mode
    tg = raw.get("target", {}).get(f"{it['img']}|{it['value']}")
    if tg is not None:
        if caption_match(it["truth"], tg):
            res[(src, "target", "correct")] += 1
        else:
            res[(src, "target", "wrong")] += 1
            misses.append(("target", src, it["value"], it["truth"][:60], tg[:60]))

print("=== RESULTS (no pass bar — raw numbers for review) ===")
for src in ["p20-flag", "p20-recovery", "dev-adjudicated"]:
    for mode in ["full", "target"]:
        sub = {k: v for k, v in res.items() if k[0] == src and k[1] == mode}
        n = sum(sub.values())
        if not n:
            continue
        ok = sub.get((src, mode, "correct"), 0)
        print(f"  {src:16} {mode:6}: {ok}/{n} ({100*ok/n:.0f}%)  detail={ {k[2]: v for k, v in sub.items()} }")
print(f"\n=== MISSES ({len(misses)}) ===")
for mode, src, val, truth, got in misses[:40]:
    print(f"  [{mode}/{src}] {val}: truth={truth!r}  got={got!r}")

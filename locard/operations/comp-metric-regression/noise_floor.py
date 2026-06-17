"""Comp-metric prompt regression — STEP 1: the noise floor.

Runs the VIRGIN comp-metric prompt N times on a stratified subset of the
baseline-100 (prov-populated sections text) at PRODUCTION settings
(deepseek-chat, temperature 0.0, max_tokens 3000) and measures run-to-run
variation — count of metrics + selection overlap. That variation is the noise
floor the location modification must stay within to count as behaviour-neutral.

Read-only except the DeepSeek calls. Runs on cloud2 (DeepSeek API + RDS read).
    python3 locard/operations/comp-metric-regression/noise_floor.py
"""
import json
import re
import statistics as st
import sys
import urllib.request

sys.path.insert(0, "/home/ubuntu/research")
from lavandula.common.db import make_app_engine
from lavandula.common.secrets import get_secret
from sqlalchemy import text

VIRGIN = open("locard/operations/prompt-backups/2026-06-06/comp-metric-prompt.KNOWN-GOOD.txt").read()
KEY = get_secret("lavandula/deepseek/api_key")
N_RUNS = 3
MAXCHARS = 60_000


def chat(system, user):
    body = json.dumps({
        "model": "deepseek-chat", "temperature": 0.0, "max_tokens": 3000,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        t = json.loads(r.read())["choices"][0]["message"]["content"].strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        t = t[4:].strip() if t.lower().startswith("json") else t.strip()
    try:
        out = json.loads(t)
        return out if isinstance(out, list) else []
    except Exception:
        return []


def key_metric(m):
    val = str(m.get("metric_value"))
    words = frozenset(re.findall(r"[a-z]{3,}", (m.get("label") or "").lower()))
    return val, words


def overlap(a, b):
    """Jaccard on metrics matched by value + label-word intersection."""
    sa, sb = [key_metric(m) for m in a], [key_metric(m) for m in b]
    matched, used = 0, set()
    for x in sa:
        for j, y in enumerate(sb):
            if j in used:
                continue
            if x[0] == y[0] and (bool(x[1] & y[1]) or not x[1] or not y[1]):
                matched += 1
                used.add(j)
                break
    union = len(sa) + len(sb) - matched
    return matched / union if union else 1.0


eng = make_app_engine()
shas = [d["sha"] for d in json.load(open("/tmp/baseline-100.json"))["docs"]]
with eng.connect() as c:
    rows = c.execute(text(
        "SELECT content_sha256, page_count, table_count, figure_count "
        "FROM lava_parse.documents WHERE content_sha256 = ANY(:s)"), {"s": shas}).fetchall()
    prof = {r[0]: (r[1] or 0, r[2] or 0, r[3] or 0) for r in rows}
    cand = [s for s in shas if s in prof and 2 <= prof[s][0] <= 30]
    tabheavy = sorted([s for s in cand if prof[s][1] >= 3], key=lambda s: -prof[s][1])[:5]
    figheavy = sorted([s for s in cand if prof[s][2] >= 5 and prof[s][1] <= 1], key=lambda s: -prof[s][2])[:5]
    lowboth = [s for s in cand if prof[s][1] == 0 and prof[s][2] <= 2][:5]
    picked = list(dict.fromkeys(tabheavy + figheavy + lowboth))[:15]
    doctext = {}
    for s in picked:
        secs = c.execute(text(
            "SELECT body_text FROM lava_parse.sections WHERE content_sha256=:s ORDER BY section_index"),
            {"s": s}).fetchall()
        doctext[s] = "\n".join(x[0] for x in secs if x[0])[:MAXCHARS]

print(f"regression set: {len(picked)} docs", flush=True)
for s in picked:
    print(f"  {s[:8]} pages={prof[s][0]} tables={prof[s][1]} figs={prof[s][2]} chars={len(doctext[s])}", flush=True)

results = {}
for s in picked:
    runs = [chat(VIRGIN, doctext[s]) for _ in range(N_RUNS)]
    counts = [len(r) for r in runs]
    ovs = [overlap(runs[i], runs[j]) for i in range(N_RUNS) for j in range(i + 1, N_RUNS)]
    results[s] = {"counts": counts, "pairwise_overlap": ovs, "runs": runs}
    print(f"  {s[:8]}: counts={counts} overlap={[round(o, 2) for o in ovs]}", flush=True)

allcounts = [c for r in results.values() for c in r["counts"]]
allov = [o for r in results.values() for o in r["pairwise_overlap"]]
print("\n==== NOISE FLOOR (same virgin prompt, run-to-run) ====")
print(f"metric count per run: median {st.median(allcounts)}, range {min(allcounts)}-{max(allcounts)}")
print(f"selection overlap between same-prompt runs: median {st.median(allov):.2f}, "
      f"min {min(allov):.2f}, mean {st.mean(allov):.2f}")
print(f"zero-metric runs (suspicious): {sum(1 for cnt in allcounts if cnt == 0)}")
json.dump({"picked": picked, "results": results}, open("/tmp/comp-metric-noisefloor.json", "w"), indent=1)
print("saved /tmp/comp-metric-noisefloor.json")

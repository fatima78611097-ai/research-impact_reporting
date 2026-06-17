"""Side-by-side: virgin vs treatment comp-metrics, for the eyeball.

Pure analysis of the saved runs (no model/DB calls). For each doc, takes the
STABLE selection of each arm (a metric value appearing in >=2 of its 3 runs),
aligns by value, and splits into kept-by-both / dropped-by-treatment /
added-by-treatment. Writes a markdown report; prints the most-divergent docs.
"""
import json
import re
from collections import Counter

NOISE = json.load(open("/tmp/comp-metric-noisefloor.json"))
TREAT = json.load(open("/tmp/comp-metric-treatment.json"))
PICKED = NOISE["picked"]
OUT = "locard/operations/comp-metric-regression/side-by-side.md"


def valnum(m):
    try:
        return float(str(m.get("metric_value")).replace(",", ""))
    except Exception:
        return None


def stable(runs):
    """value -> representative metric, for values present in >=2 of the runs."""
    present = Counter()
    rep = {}
    for r in runs:
        seen = set()
        for m in r:
            v = valnum(m)
            if v is None or v in seen:
                continue
            seen.add(v)
            present[v] += 1
            rep.setdefault(v, m)
    return {v: rep[v] for v, n in present.items() if n >= 2}


def short(m):
    s = (m.get("statement") or "").strip()
    ref = ""
    if "value_ref" in m:
        ref = f"   ⟶ value_ref={m.get('value_ref')} subject_ref={m.get('subject_ref')}"
    return s + ref


lines = ["# Virgin vs Treatment comp-metrics — side-by-side", "",
         "_Stable selection = metric value present in ≥2 of 3 runs (filters run-to-run noise)._",
         "_kept = in both arms · dropped = virgin kept it, treatment didn't · added = treatment-only._", ""]
rows = []
for sha in PICKED:
    v = stable(NOISE["results"][sha]["runs"])
    t = stable(TREAT[sha]["runs"])
    kept = sorted(set(v) & set(t))
    dropped = sorted(set(v) - set(t))
    added = sorted(set(t) - set(v))
    rows.append((sha, len(kept), len(dropped), len(added)))
    lines.append(f"## {sha[:8]}  — kept {len(kept)} · dropped {len(dropped)} · added {len(added)}")
    lines.append("\n**Kept by both:**")
    lines += [f"- {short(t[x])}" for x in kept] or ["- (none)"]
    lines.append("\n**Dropped by treatment (virgin had, treatment didn't):**")
    lines += [f"- {short(v[x])}" for x in dropped] or ["- (none)"]
    lines.append("\n**Added by treatment (treatment-only):**")
    lines += [f"- {short(t[x])}" for x in added] or ["- (none)"]
    lines.append("")

open(OUT, "w").write("\n".join(lines))

print(f"wrote {OUT}\n")
print(f"{'doc':10}{'kept':>6}{'dropped':>9}{'added':>7}")
for sha, k, d, a in rows:
    print(f"{sha[:8]:10}{k:>6}{d:>9}{a:>7}")

# print the 2 most-divergent docs inline
rows.sort(key=lambda r: -(r[2] + r[3]))
for sha, k, d, a in rows[:2]:
    v = stable(NOISE["results"][sha]["runs"])
    t = stable(TREAT[sha]["runs"])
    print(f"\n===== {sha[:8]} (kept {k}, dropped {d}, added {a}) =====")
    print("-- DROPPED by treatment --")
    for x in sorted(set(v) - set(t)):
        print(f"   {(v[x].get('statement') or '')[:90]}")
    print("-- ADDED by treatment --")
    for x in sorted(set(t) - set(v)):
        print(f"   {(t[x].get('statement') or '')[:90]}")

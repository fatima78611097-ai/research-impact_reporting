"""Re-apply the gate WITH the column-header guard to the 100 published metrics.
Measures #9 prevalence (how many published metrics the guard now catches) + samples.
No API calls."""
import collections
import json
import sys

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import gate
import render
from lavandula.common.db import make_app_engine

tuned = json.load(open("locard/operations/comp-metric-regression/scale100-results-tuned.json"))
eng = make_app_engine()
prev_pub = still_pub = 0
flags = collections.Counter()
samples = []
with eng.connect() as c:
    for sha, recs in tuned.items():
        pubs = [r for r in recs if r["decision"] == "publish"]
        if not pubs:
            continue
        _, _, idmap = render.render_tagged(c, sha)
        for r in pubs:
            prev_pub += 1
            m = {"metric_value": r["value"], "label": r["label"], "statement": r["statement"]}
            dec, reason = gate.verdict(m, r["value_ref"], r["subject_ref"], idmap)
            if dec == "publish":
                still_pub += 1
            else:
                flags[reason] += 1
                if reason.startswith("column_") and len(samples) < 12:
                    samples.append((reason, r))

caught = sum(flags.values())
print("=== column-header guard applied to the previously-published set ===")
print(f"previously published: {prev_pub}")
print(f"still publish:        {still_pub} ({still_pub/prev_pub*100:.0f}%)")
print(f"NEWLY caught (the #9 class): {caught} = {caught/prev_pub*100:.1f}% of published")
print("  reasons:", dict(flags))
print("\n-- sample catches --")
for reason, r in samples:
    print(f"  [{reason}] value={r['value']}  label={r['label']!r}")
    print(f"     {str(r['statement'])[:85]}")

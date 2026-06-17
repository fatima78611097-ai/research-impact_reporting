"""Re-apply the (tuned) gate to the saved scale-100 metrics+refs. No API calls —
re-renders idmaps from RDS and re-runs gate.verdict. Reports the new split, what
got recovered (quarantine->publish), and samples both for confirmation.
"""
import json
import sys
import collections

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import gate
import render
from lavandula.common.db import make_app_engine

SCALE = json.load(open("locard/operations/comp-metric-regression/scale100-results.json"))

eng = make_app_engine()
recovered, still_q, newpub, newq = [], [], 0, 0
out = {}
with eng.connect() as conn:
    for sha, recs in SCALE.items():
        _, _, idmap = render.render_tagged(conn, sha)
        newrecs = []
        for r in recs:
            dec, reason = gate.verdict(
                {"metric_value": r["value"], "label": r["label"]},
                r["value_ref"], r["subject_ref"], idmap)
            if dec == "publish":
                newpub += 1
                if r["decision"] == "quarantine":
                    recovered.append(r)
            else:
                newq += 1
                still_q.append({**r, "new_reason": reason})
            newrecs.append({**r, "decision": dec, "reason": reason})
        out[sha] = newrecs

tot = newpub + newq
print("==== TUNED GATE (co-location requirement removed) ====")
print(f"metrics={tot}  PUBLISH {newpub} ({newpub/tot*100:.0f}%)  QUARANTINE {newq} ({newq/tot*100:.0f}%)")
print(f"recovered from quarantine -> publish: {len(recovered)}")
print("remaining quarantine reasons:", dict(collections.Counter(r["new_reason"] for r in still_q)))
print("\n-- sample RECOVERED (were quarantined, now publish) --")
for r in recovered[:8]:
    print(f"   value={r['value']!r} label={str(r['label'])[:30]!r}  {str(r['statement'])[:70]}")
print("\n-- sample STILL QUARANTINED --")
for r in still_q[:8]:
    print(f"   [{r['new_reason']}] value={r['value']!r} label={str(r['label'])[:28]!r}  {str(r['statement'])[:60]}")
json.dump(out, open("locard/operations/comp-metric-regression/scale100-results-tuned.json", "w"), indent=1)
print("\nsaved scale100-results-tuned.json")

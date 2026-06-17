"""For column_ambiguous flags, dump the value_ref cell's actual table row so we can
see whether the value genuinely appears in >1 column (real #9) or it's over-flagging."""
import json
import sys

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import gate
import render
from lavandula.common.db import make_app_engine

tuned = json.load(open("locard/operations/comp-metric-regression/scale100-results-tuned.json"))
eng = make_app_engine()
shown = 0
with eng.connect() as c:
    for sha, recs in tuned.items():
        if shown >= 6:
            break
        pubs = [r for r in recs if r["decision"] == "publish"]
        if not pubs:
            continue
        _, _, idmap = render.render_tagged(c, sha)
        for r in pubs:
            if shown >= 6:
                break
            m = {"metric_value": r["value"], "label": r["label"], "statement": r["statement"]}
            dec, reason = gate.verdict(m, r["value_ref"], r["subject_ref"], idmap)
            if reason != "column_ambiguous":
                continue
            a = idmap.get(r["value_ref"], {})
            tcells = [v for v in idmap.values() if v.get("kind") == "cell"
                      and v.get("table") == a.get("table") and v.get("page") == a.get("page")]
            row_cells = sorted([v for v in tcells if v.get("row") == a.get("row")],
                               key=lambda v: (v.get("col") is None, v.get("col")))
            aval = gate.to_float(a.get("text"))
            shown += 1
            print(f"\n[{shown}] {sha[:8]}  value={r['value']}  '{str(r['statement'])[:70]}'")
            print(f"     value_ref: col={a.get('col')} header={gate._header_for(tcells, a.get('col'))!r} text={a.get('text')!r}")
            print(f"     ROW (table {a.get('table')}, row {a.get('row')}):")
            for v in row_cells:
                n = gate.to_float(v.get("text"))
                hit = "  <== EXACT match to value" if (n is not None and aval is not None and abs(n - aval) < 1e-6) else ""
                print(f"       col {v.get('col')} hdr={str(gate._header_for(tcells, v.get('col')))[:18]!r}: {str(v.get('text'))[:28]!r}{hit}")

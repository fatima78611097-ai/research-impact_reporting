"""DIAGNOSTIC ONLY — geometry pairing check for metric mispairing.

This is the EXACT check that was RUN ONCE on new-review-data.json (the 271 published
metrics) as a one-time measurement. It is NOT a gate: it applies no fix, quarantines
nothing, and persists nothing — it only classifies and prints. It is NOT wired into
the publish gate (slot_render.py). Documented in GATING.md.

Result on that run (current selective set): 190 inline (no pairing risk),
79 split co-located (coherent), 2 split far-apart (suspect, both 128de607),
0 no-geometry-info. The 2 suspects were left as `publish` — nothing acted on them.

Thresholds used: dy < 60 pts => same row; (dx < 160 and dy < 160) => same card.
Box midpoints from the stored v_bbox / s_bbox (points, 1/72 inch).

To turn this into an APPLIED gate you would quarantine the 'suspect' bucket and/or
route it (plus dense-grid cases this proximity test can't see) to a vision judge.
"""
import json


def _mid(b):
    return ((b["l"] + b["r"]) / 2, (b["t"] + b["b"]) / 2)


def geometry_pairing_check(records):
    """Classify each PUBLISHED metric by whether its value box and subject box are
    physically co-located. Returns counts + the suspect list. Applies nothing."""
    inline = coherent = suspect = noinfo = 0
    suspects = []
    for r in records:
        if r.get("gate_decision") != "publish":
            continue
        if r.get("same_marker"):                 # value & label share a marker -> no pairing risk
            inline += 1
            continue
        vb, sb = r.get("v_bbox"), r.get("s_bbox")
        if not vb or not sb or r.get("v_page") is None:
            noinfo += 1
            continue
        if r["v_page"] != r["s_page"]:           # label on a different page -> suspect
            suspect += 1
            suspects.append((r, "different page"))
            continue
        (vx, vy), (sx, sy) = _mid(vb), _mid(sb)
        dy, dx = abs(vy - sy), abs(vx - sx)
        if dy < 60 or (dx < 160 and dy < 160):   # same row, or same card -> coherent
            coherent += 1
        else:
            suspect += 1
            suspects.append((r, f"far apart dx={int(dx)} dy={int(dy)}"))
    return {"inline": inline, "coherent": coherent, "suspect": suspect,
            "noinfo": noinfo, "suspects": suspects}


if __name__ == "__main__":
    recs = json.load(open("new-review-data.json"))
    out = geometry_pairing_check(recs)
    print(f"inline={out['inline']} coherent={out['coherent']} "
          f"suspect={out['suspect']} noinfo={out['noinfo']}")
    for r, why in out["suspects"]:
        print(f"  SUSPECT [{why}] {r['statement'][:60]}")

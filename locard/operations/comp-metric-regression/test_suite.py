"""M4 — the locked regression suite. One command; green = the research pipeline still produces the
validated numbers. This is the acceptance contract for the production port: a port must pass this
suite (same fixture, same bars) before cutover.

Checks (no network calls; DB used only for the regroup geometry check, skippable with --no-db):
  1. fixture-frozen      the vision ground-truth fixture is intact (n=1605, hash-stable count)
  2. m0-baselines        scorecard() reproduces the locked §1a numbers on the ORIGINAL gate set
  3. canonical-set       the applied canonical set holds its post-fix numbers (publish/quarantine,
                         shippable/grounded/is-a-metric bars)
  4. gate-hardening      small-int exact grounding (no substring/tolerance leaks) + verdict() hooks
  5. validity-rules      item-1 flips held (sample), operator-keeps still published
  6. twin-repair         prefix-swap recovery on the known case
  7. regroup-geometry    Capstone 7/7 high-conf (DB) + caption filters behave
"""
import json
import re
import sys

sys.path.insert(0, "/home/ubuntu/research")
sys.path.insert(0, "/home/ubuntu/research/locard/operations/comp-metric-regression")
import gate
import regroup
from score_pipeline import scorecard, BASELINE

B = "/home/ubuntu/research/locard/operations/comp-metric-regression/"
VIS = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision"
NO_DB = "--no-db" in sys.argv

# locked bars for the CURRENT canonical set (post M1-promote + item-1 [incl. year-as-value regex fix]
# + regroup flag-mode + item-4a neighbor recoveries + episode-duration rule, 2026-06-11)
CANONICAL = {"publish": 1263, "quarantine": 343,
             "shippable_min": 0.930, "grounded_min": 0.945, "is_a_metric_min": 0.974}

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


# 1. fixture frozen
fx = json.load(open(B + "fixtures/vision-ground-truth.json"))
check("fixture-frozen", fx["n"] == 1605 and len(fx["records"]) == 1605 and fx["frozen"] == "2026-06-10",
      f"n={fx['n']}")

# 2. M0 baselines reproduce on the ORIGINAL gate decisions
s0 = scorecard(fx["records"])
drift = {k: (s0[k], BASELINE[k]) for k in BASELINE if s0[k] != BASELINE[k]}
check("m0-baselines", not drift, str(drift))

# 3. canonical set holds its post-fix bars
rev = json.load(open(VIS + "/review-data.json"))
pub_ids = {r["id"] for r in rev if r["gate_decision"] == "publish"}
counts = (len(pub_ids), len(rev) - len(pub_ids))
sc = scorecard(fx["records"], pub_ids)
N = sc["published"]
bars = (counts == (CANONICAL["publish"], CANONICAL["quarantine"]),
        sc["shippable"] / N >= CANONICAL["shippable_min"],
        sc["grounded"] / N >= CANONICAL["grounded_min"],
        sc["is_a_metric"] / N >= CANONICAL["is_a_metric_min"])
check("canonical-set", all(bars),
      f"counts={counts} ship={sc['shippable']/N:.3f} grnd={sc['grounded']/N:.3f} metric={sc['is_a_metric']/N:.3f}")

# 4. gate hardening behaviors
g = (gate.value_grounded(1, "purchased a food truck for youth") is False         # no invented 1
     and gate.value_grounded(1, "served 1 client this year") is True             # real 1 grounds
     and gate.value_grounded(2, "the second highest rate in 2019") is False      # rank/year no leak
     and gate.value_grounded(9, "operates 9 mobile vans") is True                # real small count
     and gate.value_grounded(2300000, "a $2.3-million fiber project") is True      # hyphen scale fix
     and gate.value_grounded(14884, "House and 1 4, 884 individuals worked") is True   # shattered-number join
     and gate.value_grounded(111, "assisted  11 individuals and conducted 121 tours") is False)  # no cross-word join
v = gate.verdict({"metric_value": 3, "label": "programs launched"}, None, None, {}, measured=False)
g2 = v == ("quarantine", "no_numeric_value") or v[0] == "quarantine"             # small-int + not-measured never publishes
check("gate-hardening", g and g2)

# 5. item-1 flips held + operator keeps
byid = {r["id"]: r for r in rev}
flips_held = all(byid[i]["gate_decision"] == "quarantine"
                 for i in ["015893d4:5", "e6696bd9:7", "60e7220f:5", "5cfbbca2:4"] if i in byid)
keeps = all(byid[i]["gate_decision"] == "publish" for i in ["a00adaa5:0", "b60a49eb:8"] if i in byid)
check("validity-rules", flips_held and keeps)

# 6. twin-repair on the known case + item-4a neighbor recoveries applied (and rule B stays dead)
tw = json.load(open(B + "twin-repair-recovered.json"))
known = {r["id"]: r for r in tw["recovered"]}
dec = json.load(open(B + "pipeline-decisions.json"))["reasons"]
nb = {i for i, (d, rs) in dec.items() if rs == "neighbor_repair"}
no_rounded = not any(rs == "rounded_value" for _, rs in dec.values())
check("twin-repair", known.get("d58e3d5b:0", {}).get("new_decision") == "publish"
      and known.get("c09c96c2:0", {}).get("new_decision") == "quarantine")
check("item4-recovery", nb == {"015893d4:0", "e136ede4:4"} and no_rounded
      and all(byid[i]["gate_decision"] == "publish" for i in nb if i in byid),
      f"neighbor={sorted(nb)}")

# 7. doc-gate (REQ-PROD-018): stage-0 corpus inclusion behaviors
from lowvalue_gate import doc_included
dg = (doc_included(0, 2596)[0] is False                      # dca5a824 class: plain-text
      and doc_included(1, 5586, pdf_creator="Microsoft Word 2013")[0] is False   # 9255ab7e: logo doesn't save a typed doc
      and doc_included(12, 800)[0] is True                   # designed doc passes
      and doc_included(0, 900)[0] is True                    # sparse but not dense-typed
      and doc_included(5, 1200, metric_mv_fraction=0.92, n_metrics=13)[0] is False   # narrative/event
      and doc_included(5, 1200, demo_fraction=0.56, n_metrics=9)[0] is False     # flavor D demographic sheet
      and doc_included(5, 1200, metric_mv_fraction=0.3, n_metrics=10)[0] is True)
check("doc-gate", dg)

# 8. regroup geometry: caption filters + Capstone 7/7 (DB)
agree = (not regroup.labels_agree("individuals attended Divorce Support Groups", "Co-Parenting Seminar attendees")
         and regroup.labels_agree("Beneficiaries of heating assistance", "Heating assistance beneficiaries"))
filt = (agree and regroup._bad_label("271 of which were children") and regroup._bad_label("1:1 IN DEPTH SUPPORT")
        and regroup._bad_label("Limits were changed so families may receive more")
        and not regroup._bad_label("Beneficiaries of heating assistance"))
if NO_DB:
    check("regroup-geometry", filt, "(filters only; DB skipped)")
else:
    from lavandula.common.db import make_app_engine
    from sqlalchemy import text as sqtext
    eng = make_app_engine()
    with eng.connect() as conn:
        full = conn.execute(sqtext("SELECT content_sha256 FROM lava_parse.sections WHERE content_sha256 LIKE :p LIMIT 1"),
                            {"p": "5b98b66f%"}).scalar()
        p = regroup.pairings(conn, full, 2)
    GT = {"967": "counseling", "353": "Head Start", "218": "Entrepreneurs", "369": "financial",
          "2,971": "heating", "32": "Graduates", "$1,903,873": "tax refunds"}
    hits = sum(1 for n, want in GT.items()
               if p.get(n, {}).get("confidence") == "high" and want.lower() in (p[n]["label"] or "").lower())
    check("regroup-geometry", filt and hits == 7, f"capstone {hits}/7")

fails = [n for n, ok, _ in results if not ok]
print(f"\n{'GREEN — research pipeline holds its validated numbers.' if not fails else 'RED — ' + ', '.join(fails)}")

# PENDING-PROD obligations: printed on EVERY run so they cannot be missed. Sourced from
# production-requirements.md; remove a line only when its REQ is DONE/WAIVED there.
import re as _re
try:
    reqs = open(B + "production-requirements.md").read()
    entries = _re.findall(r"\*\*(REQ-PROD-\d+)([^*]*)\*\*", reqs)
    pending = [rid for rid, rest in entries if "DONE" not in rest and "WAIVED" not in rest]
    done = [rid for rid, rest in entries if "DONE" in rest or "WAIVED" in rest]
    print(f"\nPENDING-PROD ({len(pending)} open, {len(done)} done — see production-requirements.md):")
    print("  " + ", ".join(pending))
except FileNotFoundError:
    print("\nWARNING: production-requirements.md missing — the hardened TODO list is gone; restore it.")
sys.exit(1 if fails else 0)

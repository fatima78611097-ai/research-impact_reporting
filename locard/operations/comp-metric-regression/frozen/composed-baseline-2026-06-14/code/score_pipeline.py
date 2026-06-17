"""M0 — scoring harness + regression test against the frozen vision fixture.

Encodes the LOCKED definition (is-a-metric = vision says 'metric' AND tier is one of the four value
classes; activity + the 8 tier-0 types = reject). `scorecard(records, published_ids)` scores any
pipeline's published set against vision truth -> the §1a layers. Reused by M1-M4: pass the new
pipeline's published ids. Run directly = regression test: asserts the locked §1a baselines.
"""
import collections
import json
import sys

FIX = "/home/ubuntu/research/locard/operations/comp-metric-regression/fixtures/vision-ground-truth.json"
VALUE_CLASSES = {"outcome_impact", "reach_output", "capacity_input", "financial"}

# locked §1a baselines (2026-06-10 vision-verified, denominator = 1433 published)
BASELINE = {"published": 1433, "is_a_metric": 1310, "grounded": 1304,
            "shippable": 1236, "recall_loss": 31, "recall_denom": 172,
            "tier_agree": 1191, "tier_pool": 1350}


def is_a_metric(vis):
    return vis["is_metric"] == "metric" and vis["tier"] in VALUE_CLASSES


def grounded(vis):
    return vis["value_on_page"] == "yes" and vis["subject_pairing"] == "correct"


def scorecard(records, published_ids=None):
    """published_ids: set the pipeline publishes (default = the fixture's gate_decision=='publish')."""
    if published_ids is None:
        published_ids = {r["id"] for r in records if r["gate_decision"] == "publish"}
    pub = [r for r in records if r["id"] in published_ids]
    notpub = [r for r in records if r["id"] not in published_ids]
    N = len(pub)
    metric = [r for r in pub if is_a_metric(r["vision"])]
    gok = [r for r in pub if grounded(r["vision"])]
    fab = [r for r in pub if r["vision"]["value_on_page"] == "no"]
    mis = [r for r in pub if r["vision"]["value_on_page"] != "no" and r["vision"]["subject_pairing"] == "wrong"]
    ship = [r for r in pub if grounded(r["vision"]) and is_a_metric(r["vision"])]
    recall = [r for r in notpub if grounded(r["vision"]) and is_a_metric(r["vision"])]
    sa = collections.Counter(r["vision"].get("standalone") for r in ship)
    tier_pool = [r for r in records if r["code_validity"] == "valid_metric" and r["vision"]["is_metric"] == "metric"]
    tier_agree = [r for r in tier_pool if r["vision"]["tier"] == r["code_tier"]]
    return {
        "published": N, "is_a_metric": len(metric), "grounded": len(gok),
        "fabricated": len(fab), "mispaired": len(mis), "shippable": len(ship),
        "recall_loss": len(recall), "recall_denom": len(notpub),
        "standalone": dict(sa), "tier_agree": len(tier_agree), "tier_pool": len(tier_pool),
    }


def _pct(n, d):
    return f"{100*n/d:.1f}%" if d else "n/a"


def main():
    records = json.load(open(FIX))["records"]
    s = scorecard(records)
    N = s["published"]
    print(f"=== SCORECARD (published denominator = {N}) ===")
    print(f"  Mode 2 — is-a-metric      : {s['is_a_metric']:4} ({_pct(s['is_a_metric'], N)})")
    print(f"  Mode 1 — grounding correct: {s['grounded']:4} ({_pct(s['grounded'], N)})  [fab {s['fabricated']}, mispair {s['mispaired']}]")
    print(f"  Mode 1 — recall loss      : {s['recall_loss']}/{s['recall_denom']} ({_pct(s['recall_loss'], s['recall_denom'])})")
    print(f"  SHIPPABLE                 : {s['shippable']:4} ({_pct(s['shippable'], N)})")
    print(f"  standalone among shippable: {s['standalone']}")
    print(f"  tier agreement (HELD)     : {s['tier_agree']}/{s['tier_pool']} ({_pct(s['tier_agree'], s['tier_pool'])})")

    drift = {k: (s[k], BASELINE[k]) for k in BASELINE if s[k] != BASELINE[k]}
    if drift:
        print("\nM0 FAIL — drift from locked §1a baselines:")
        for k, (got, exp) in drift.items():
            print(f"   {k}: got {got}, expected {exp}")
        sys.exit(1)
    print("\nM0 PASS — harness reproduces the locked §1a scorecard exactly.")


if __name__ == "__main__":
    main()

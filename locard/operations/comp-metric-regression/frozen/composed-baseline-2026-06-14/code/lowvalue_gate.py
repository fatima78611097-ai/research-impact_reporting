"""REQ-PROD-018 — the low-value report (corpus-inclusion) gate, BUILT. Doc-level curation:

  Flavor A  plain-text report:    figure_count == 0 AND chars/page >= 1500 (typed, undesigned)
  Flavor C  narrative/event report: > 60% of the doc's extracted metrics flagged by the
            measurable-value check OR small-int-dominant with not-a-metric majority
            (legislative/advocacy/church-network docs: value=1 per event)

Flavor B (single-program/administrative) is instance-collection only — no detector yet by design.
Runs on the dev corpus, REPORTS the docs it would exclude + the metric impact. Application to the
canonical set is an OPERATOR decision (curation, not extraction defect) — this script does not flip
anything unless --apply is passed.
"""
import json
import sys
import collections

sys.path.insert(0, "/home/ubuntu/research")
from lavandula.common.db import make_app_engine
from sqlalchemy import text

B = "/home/ubuntu/research/locard/operations/comp-metric-regression/"
VIS = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision"
APPLY = "--apply" in sys.argv

DEMO_PAT = __import__("re").compile(
    r"\b(household|households)\b.*\b(single|two|parent|size|income|own|rent)\b"
    r"|\bwere (black|white|hispanic|asian|unemployed|homeless|male|female|veteran)s?\b"
    r"|\bincome (band|level|bracket)|\bage[sd]? \d+", __import__("re").I)


def doc_included(figs, chars_per_page, metric_mv_fraction=None, n_metrics=0,
                 demo_fraction=None, pdf_creator=""):
    """Stage-0 corpus-inclusion decision (REQ-PROD-018). Returns (included, reason).
    Flavor A pre-extraction; flavors C/D need per-metric signals (post-extraction).
    pdf_creator is recorded as corroborating EVIDENCE (Word/Office/Publisher), never the trigger
    (13 fine Office-made reports exist in dev; junk ships in InDesign/Canva/empty-metadata too)."""
    ev = f"; creator={pdf_creator[:40]}" if pdf_creator else ""
    if figs <= 1 and chars_per_page >= 1500:   # <=1: a logo defeated figs==0 (9255ab7e)
        return False, f"A:plain-text/typed (figs={figs}, {chars_per_page:.0f} chars/pg{ev})"
    if metric_mv_fraction is not None and n_metrics >= 4 and metric_mv_fraction > 0.6:
        return False, f"C:narrative/event ({metric_mv_fraction:.0%} non-measurable{ev})"
    if demo_fraction is not None and n_metrics >= 4 and demo_fraction > 0.5:
        return False, f"D:demographic-sheet ({demo_fraction:.0%} demographic descriptors{ev})"
    return True, "ok"


rev = json.load(open(VIS + "/review-data.json"))
docs = sorted({r["sha8"] for r in rev})
mv_flags = set(json.load(open(B + "measurable-value-quarantine.json"))["quarantine"])

eng = make_app_engine()
meta = {}
with eng.connect() as conn:
    rows = conn.execute(text(
        "SELECT left(content_sha256,8), COALESCE(figure_count,0), COALESCE(table_count,0), "
        "COALESCE(total_text_chars,0), COALESCE(page_count,0) FROM lava_parse.documents "
        "WHERE left(content_sha256,8) = ANY(:s)"), {"s": docs}).fetchall()
for sha8, figs, tabs, chars, pages in rows:
    meta[sha8] = {"figs": figs, "tabs": tabs, "cpp": chars / max(1, pages), "pages": pages}

by_doc = collections.defaultdict(list)
for r in rev:
    by_doc[r["sha8"]].append(r)

flagged = []
for sha8, mets in sorted(by_doc.items()):
    m = meta.get(sha8, {})
    reasons = []
    # Flavor A: undesigned typed document
    if m and m["figs"] == 0 and m["cpp"] >= 1500:
        reasons.append(f"A:plain-text (figs=0, {m['cpp']:.0f} chars/pg)")
    # Flavor C: the doc's metrics are dominantly non-measurements
    ids = [x["id"] for x in mets]
    mv_hit = sum(1 for i in ids if i in mv_flags)
    if len(ids) >= 4 and mv_hit / len(ids) > 0.6:
        reasons.append(f"C:narrative/event ({mv_hit}/{len(ids)} non-measurable)")
    if reasons:
        pub = sum(1 for x in mets if x["gate_decision"] == "publish")
        flagged.append({"sha8": sha8, "org": mets[0].get("org"), "reasons": reasons,
                        "metrics": len(ids), "published": pub})

print(f"dev corpus: {len(by_doc)} docs -> low-value gate flags {len(flagged)}:")
tot_pub = 0
for f in flagged:
    tot_pub += f["published"]
    print(f"  {f['sha8']}  {str(f['reasons']):58} {f['published']:3} published | {f['org'][:38]}")
print(f"\nimpact if applied: {tot_pub} published metrics would move to curation-excluded "
      f"({100*tot_pub/sum(1 for r in rev if r['gate_decision']=='publish'):.1f}% of published)")
json.dump(flagged, open(B + "lowvalue-gate-flags.json", "w"), indent=1)

if APPLY:
    import shutil
    shutil.copy(VIS + "/review-data.json", VIS + "/review-data.before-lowvalue.json")
    shas = {f["sha8"] for f in flagged}
    n = 0
    for r in rev:
        if r["sha8"] in shas and r["gate_decision"] == "publish":
            r["gate_decision"] = "quarantine"
            r["gate_reason"] = "lowvalue_doc"
            r["decided_by"] = "low-value report gate"
            r["decided_detail"] = "; ".join(next(f["reasons"] for f in flagged if f["sha8"] == r["sha8"]))
            n += 1
    json.dump(rev, open(VIS + "/review-data.json", "w"))
    print(f"APPLIED: {n} metrics -> curation-excluded")
else:
    print("(report only — pass --apply after operator decision)")

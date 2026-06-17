# Experiment 0002: Coordinate-Grounded Metric Extraction — 10-doc proof

**Status**: In Progress

**Date**: 2026-06-16

## Goal
Phase −1(a) of the metric-product execution plan. Prove the full precision pipeline
end-to-end on real data with NO new build and NO re-parse, on documents that already have
both extracted metrics and real Docling page coordinates (49 such docs exist).

Chain (existing research code, `comp-metric-regression/run_doc.py`):
low-value doc gate → tagged render (uses coordinates) → DeepSeek extract → DeepSeek ground
(assigns value/subject markers) → measurable-value check (small ints) → gate.verdict +
twin-repair → publish / quarantine + reason.

**Question:** On ~10 coord-bearing docs, does the chain publish only metrics that are
actually on the page (right number + right label), and quarantine the rest for sound
reasons? This is the go/no-go proof that the approach works before we spec the build.

## Method
- 10 docs sampled from the 49 that have metrics + coordinates (mix of metric counts).
- Run `run_doc.run_doc()` per doc; save records (statement, value, subject, decision, reason).
- Hand-check: render the source pages for a sample of PUBLISHED metrics and verify the
  number + label against the page image.

## Validation (single doc, efd61fea90a6 — Greater Dubuque Development)
15 metrics → 9 publish / 6 quarantine. Quarantines were correct: Forbes/MSN "ranked #1/#3/
Top 20" rejected as not-a-metric; two ungrounded values flagged value_not_at_marker.
Published were real measurements (256 visits, 332 net new jobs, $27.75M investment, $17.57
wage, 93,653 population). Precision-first behavior confirmed.

## Results
**Status: PASS.** 10/10 docs ran end-to-end (~8–16s each). 106 metrics → **94 publish /
12 quarantine** (8 value_not_at_marker, 4 not-a-metric). Quarantines were all correct
(Forbes/Moody's "#1/#3/Top-20" rankings; ungrounded values).

**Hand-check against parsed source text (3 docs, ~27 published metrics): 0 errors.**
- efd61fea (Greater Dubuque): 7/7 cleanly-checkable published correct (256 owners met, 474
  assistance occasions, 332 net new jobs, $27.75M capital investment, 110 employer
  investors, 1,800 net new jobs, $17.57 avg wage). "75" inconclusive (search collided with
  a PMS color code), not an error.
- e6696bd9 (CRT): 10/10 correct (86,000 served, 680 housed, 23,000 energy families, 176
  homes weatherized, 74 MAT patients, 125,000 volunteer hours, 40 towns, 55 years,
  $52,695,347 revenue, $52,591,378 expenses).
- f090f7af (YMCA Houston): 10/10 correct (320,000 served, 13,000 legal consults, 884
  unaccompanied children, 245 trafficking survivors, 2,587,770 lbs food, 3,233 SAW grads,
  12,000 youth sports, 5,182 early care, 394,910 volunteer hours, 60,000+ teen members).
  "60,000 teen members" hand-confirmed real (source: "60,000+ teen members found a place to
  belong") — not the mispair an initial value-only search suggested.

**Conclusion:** the coord-grounded chain (render→extract→ground→is-a-metric→gate) works
end-to-end on real data with no new build, and on prose-heavy reports it publishes accurate
metrics (right number + right label) while correctly quarantining non-metrics. This is the
go/no-go proof that the approach is sound.

**Honest caveat:** all 3 hand-checked docs are prose-heavy annual reports — the easy case
for text grounding. The hard error class (mispairing) lives on designed/infographic pages,
which are under-represented in the 49 coord-bearing docs and which the text path can't fully
solve. That gap is what Phase −1(c) recall-sizing and the Phase 4 vision work address.

## Next Steps
- Phase −1(b): hand-grade a sample of today's ~56K "verified" (right-number AND right-label)
  for the real baseline; measure prose-vs-infographic duplication.
- Phase −1(c): run the recall check (upgraded to label-match) to size the image-only gap.
- If results hold, spec the production build (extractor cites markers; Spec/SPIDER) — first
  point where Gemini/Codex consults are triggered.

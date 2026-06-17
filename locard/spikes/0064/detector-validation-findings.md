# 0064 — Advisor KPI-detector: empirical validation on known pages

**Date:** 2026-06-01. Implemented the advisor's detector faithfully
(`kpi_detector.py`) and ran it over every page of the three known docs
(`run_detector.py`, docling_parse cells + bboxes, CPU). Verdict: **sound design,
fires on the real target, but not yet calibrated for the key safety property
(only fire where the fix actually works).**

## What worked (merit confirmed)
- **Canonical target fires:** `001eb5f8` p13 (EMAIL CAMPAIGN clean grid) → APPLY,
  score 9. The "heading-above-metric" concern did NOT block it (a pair was found).
- **Obvious non-KPI correctly skipped:** cover (p1), section dividers / lone
  titles (p5, p8, p15) → normal_pipeline. Prose-ish pages → medium/normal.
- The three-lane routing and layout-shape signals behave broadly as intended.

## Three calibration gaps the run exposed
1. **Fires on the un-fixable dense mosaic.** `61d4b4bb` p1 → APPLY (score 8) — but
   this is exactly the page where coordinate regrouping SCRAMBLES (sim doc 2).
   Tell-tale: the clusterer found only **1 cluster** (couldn't separate the
   cards), yet the additive score still reached 8 via the other signals. **The
   score≥8 gate is not conservative enough.** The advisor's stricter *whitelist*
   trigger correctly returned False here (it requires 2+ clusters) — so the
   whitelist is the safer gate.
2. **Table-grid rejector is miscalibrated** (partly my implementation). It fired
   `table_grid_detected` on KPI *card* pages (p13, p16, many 7ce2c7fd pages),
   conflating a dense card grid with a financial table. Consequence: it can't
   reliably protect real financial tables AND it polluted the whitelist (blocked
   p13). Distinguishing "card grid" from "row/column data table" needs a real
   discriminator (repeated row labels, full-width value alignment), not a
   cell-count heuristic.
3. **Garbled documents masquerade as KPI everywhere.** `7ce2c7fd` is glyph-soup
   garble → every line is a tiny fragment → `sbr≈1.0, pbr≈0.0` on EVERY page →
   nearly all 16 pages scored APPLY. The detector can't tell "short fragments
   because KPI cards" from "short fragments because font-garble." Small population
   (~0.2% of docs) but systematic; needs a garble guard upstream.

## The unifying insight (ties detection to the fix)
"**Clustering cleanly separated the cards (2+ clusters with heading/metric
pairs)**" is simultaneously (a) the strongest *detection* signal AND (b) the
precondition for the regrouping fix to *succeed*. The dense mosaic failed both at
once (1 cluster). So the right gate is the advisor's **whitelist (require clean
multi-cluster separation), not the additive score≥8** — apply the fix ONLY where
the cards cleanly separate, which is exactly where the fix works. This makes
detection and "will-the-fix-work" the same test.

## v2 results (iterated: whitelist-gated APPLY + finer/tight clustering + occupancy table-rejector + garble guard) — VISUALLY VERIFIED

The three round-1 gaps are FIXED:
- **Dense mosaic** `61d4b4bb` p1 → **LOG, not APPLY** (0 clean card columns; clustering can't separate the packed cards). Visual: a genuine but chaotic YMCA KPI sheet — correctly logged, not applied.
- **Garble** `7ce2c7fd` (all 16 pages) → **normal(garbled)** (gfrac 0.89–1.0). The garble guard works.
- **Canonical target** `001eb5f8` p13 → **APPLY** (score 12, whitelist=T). Visually the clean 3-column EMAIL CAMPAIGN grid. Correct.

BUT visual verification of the APPLY lane exposed a DIFFERENT, serious precision problem — **3 of the 4 APPLY pages on `001eb5f8` are FALSE POSITIVES** (the expensive error):
- **p7, p14 → APPLY but are PROSE pages** (multi-paragraph narrative + one KPI callout box). Misclassified.
- **p20 → APPLY but is the STATEMENT OF FINANCIAL POSITION** (revenue/expense tables + pie charts). The table-rejector missed it.
- Only **p13** among the APPLY pages is a true clean KPI grid. APPLY-lane precision on this doc ≈ 25%.

### Root causes (both fixable, neither is mere threshold tuning)
1. **Block granularity is the big one.** The parser emits text *lines*, not paragraph *blocks*. A prose paragraph fragments into many short lines (none ≥15 words or containing a mid-line period), so `paragraph_block_ratio` is low and `short_block_ratio` high → **prose pages with a callout masquerade as KPI**. Fix: reconstruct paragraphs (merge consecutive same-column/flow lines into a block) BEFORE computing density signals; then prose → high paragraph ratio → `prose_like` → rejected.
2. **Financial/table rejection still too weak.** Two side-by-side label/value tables + charts (p20) slipped through the occupancy heuristic. Needs currency-aligned value-column detection / chart-region awareness / "Total Revenue|Expenses" cues.

### Verdict
v2 is a real improvement (the mosaic/garble/target cases are now right, and the whitelist gate correctly ties APPLY to "cards cleanly separate = fix will work"). But it is **NOT yet deployable**: it still fires on prose and financial pages, which are exactly the pages the regrouping fix would damage. The dominant remaining error is prose-with-callout false positives driven by line-vs-paragraph granularity. This suggests either a focused v3 (paragraph reconstruction + financial guard) OR that heuristic detection has a precision ceiling here and a **labeled eval set + learned classifier** (advisor step 3) is the more durable path.

## Recommended refinements (before any rollout)
- Gate on the **whitelist** (require ≥2 clean clusters + heading/metric pairs), not
  score≥8. Treat score as the medium/log lane only.
- Replace the table-grid heuristic with a real card-vs-table discriminator.
- Add a **garble guard** (the glyph-soup space-fraction signal from
  `garble_count.py`) so garbled pages route away from regrouping.
- Then build a human-labeled eval set (advisor step 3) and measure precision —
  false positives (firing where the fix breaks the page) are the expensive error.

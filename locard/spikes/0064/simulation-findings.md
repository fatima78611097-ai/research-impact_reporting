# 0064 — Full-fix simulation (grouping + KPI prompt), real pipeline

**Date:** 2026-06-01. **Setup:** CPU cell extraction (docling_parse, with bbox) →
coordinate clustering → serialize → REAL production extractor (`call_deepseek`,
`deepseek-chat`, temp 0, the live `_METRICS_PROMPT`) → REAL gate
(`grounding.check`). Only the cell ORDER and a one-paragraph prompt addition vary.
Scripts: `sim_fullfix.py`, `sim_promptfix.py`, `grouping_lib.py`.

## Conditions
- **A** = row-major (current linearization) + existing prompt
- **B** = column-major (grouping fix) + existing prompt
- **C** = column-major + minimal KPI prompt addition ("make KPI-card metrics
  self-contained by including the parent category label; snippet rule UNCHANGED —
  verbatim, contiguous"). The `metric_text` carries the parent; `source_snippet`
  stays atomic & verbatim, so the gate stays strict.

## Result 1 — clean card grid (001eb5f8 p13, EMAIL CAMPAIGN): FULL FIX WORKS
- A/B (existing prompt): atomic, context-poor — `63,955 DELIVERED`.
- **C (full fix): context-complete AND verified:**
  ```
  63,955 DELIVERED — Email Campaign      snip:'63,955 DELIVERED'   GROUNDED:R1
  23,255 OPENS — Email Campaign          snip:'23,255 OPENS'       GROUNDED:R1
  2,542 CLICKS — Email Campaign          snip:'2,542 CLICKS'       GROUNDED:R1
  2M SOCIAL MEDIA — Media                                          GROUNDED:R1
  1.6M 2021 — Peer-to-Peer Texting                                 GROUNDED:R1
  450,000+ PAGE VIEWS — Tax Credit Calculator                      GROUNDED:R1
  ```
  Correct parent on EVERY card; every snippet still grounds R1 (gate unchanged).
  Column-major made each label contiguous with its stats, so the LLM could attach
  the right parent while the verbatim snippet stayed atomic. This is the win.

## Result 2 — dense/irregular KPI mosaic (61d4b4bb, 88% quarantined in prod): CLUSTERING FAILS
Page 1 = 126 cells tightly packed (`5,423MEMBERSHIPS` glued; 4 card labels on the
same y-band at different x; numbers/labels interleaved). Naive column-major
scrambled it:
- **B "100% grounded" is ILLUSORY** — when the serialization concatenates
  scrambled cells (`'ATTENDEES ATTENDEES EMPLOYEES PRAYER 220'`), the LLM quotes
  the scramble and it trivially grounds against its own (scrambled) text.
  Grounding-against-own-serialization REWARDS bad clustering.
- **C** produced WRONG/mixed context: `2,200 attendees — Breakfast with Santa`
  (snip `'BREAKFAST 2,200 SOLD OUT EVENT INCLUSIVE TRAINING ATTENDEES'` →
  quarantine), `220 attendees — Inclusive Training` (snip is a scrambled blob).

## Conclusions (honest, two-sided)
1. **The mechanism is real and can be excellent** — on well-structured card grids,
   grouping + a trivial prompt addition yields context-complete, correctly-attributed,
   gate-passing metrics. The prompt change is easy; the gate stays strict.
2. **The hard, uncertain part is the 2D clustering, and it's layout-dependent** —
   from trivial (clean grids) to genuinely hard (dense mosaics). Naive x-gap
   column detection is not enough; a production fix needs robust card detection,
   and even that will not be uniformly reliable. Expect PARTIAL recovery,
   prioritized by clustering tractability; the hardest pages may need a VLM or stay
   quarantined.
3. **Success cannot be fully auto-scored.** "Snippet grounds against the regrouped
   text" is gameable by a bad serialization. Correctness needs visual/human
   judgment on a sample — re-confirming the human-in-viewer (Spec 0057 QA viewer)
   as the trustworthy instrument, not another automated proxy.

## Bearing on the investment decision
- Timing is right (pilot: ~5% parsed) to build grouping INTO the pipeline before the
  bulk parse, applied SURGICALLY to KPI pages (we have a queryable signature:
  `grounding_diag` contiguity-break × `lava_parse.documents` figure density).
- The work is a **layout-clustering** problem first (hard, the real cost), prompt
  second (trivial), gate unchanged. Garble (~0.2%) is a separate cheap bank-it.

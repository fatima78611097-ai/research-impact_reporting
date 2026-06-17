# Production Pipeline Requirements (HARDENED — enforced, not advisory)

**Enforcement:** these REQ-IDs are printed as `PENDING-PROD` by `test_suite.py` on EVERY run, and the
production-convergence spec MUST adopt them as acceptance criteria. A production port is not done
until each REQ is either implemented (suite check goes green) or explicitly waived by the operator
in this file. Do not delete entries; mark them DONE/WAIVED with date + commit.

## A. Confidence / filtering (from the 2026-06-11 disclosure decisions)

- **REQ-PROD-001 — per-metric `layout_class` persisted at pipeline time.** Values from the pipeline's
  own path: `prose | table | grid_geometry_confirmed | designed_verifier_confirmed | dense_mosaic`.
  Written when the metric is produced; never derived later. Enables the public include/exclude filter.
- **REQ-PROD-002 — `confidence_class` + taxonomy version field** (`confidence_class_v1`). Published
  filters keep meaning over time; class refinements bump the version, never mutate old rows.
- **REQ-PROD-003 — public interface defaults to EXCLUDING the high-error class** (dense_mosaic);
  inclusion is an explicit user opt-in carrying the disclosed error band.
- **REQ-PROD-004 — per-class accuracy disclosure fields**: each published class carries its audited
  precision band + audit date (the sampled vision-audit number, refreshed per release).

## B. Vector / RAG readiness (capture at extraction time — backfill is expensive)

- **REQ-PROD-010 — stable `metric_id`** (survives re-runs; dedup key = content_sha256 + value +
  normalized subject) so embeddings/citations don't dangle across pipeline versions.
- **REQ-PROD-011 — RAG record completeness per metric**: standalone `metric_statement`, `value`,
  `unit`, `org_ein`, `report_year/period`, `content_sha256`, `page`, grounding marker/bbox,
  `verification_path` (which gates/rules/verifier it passed), `layout_class`, `tier` (held: logged
  not published), source URL. All fields the interview/RAG product needs to cite and filter.
- **REQ-PROD-012 — provenance bbox retained on the metric record** (not only in parse tables): the
  page-highlight UX (ENH-002) and the verifier both need it without a re-parse join.
- **REQ-PROD-013 — pgvector enabled on the production DB** (operator DDL: `CREATE EXTENSION vector`)
  + `metric_embeddings` table (metric_id, model, dim, embedding, created_at) + ANN index. Sizing
  check vs instance class BEFORE the national run (db.t4g.small is fine to ~100k, not millions).
- **REQ-PROD-014 — embedding pass as a pipeline stage** (model choice recorded per row), run on
  publish, re-run policy versioned with the embedding model.
- **REQ-PROD-015 — macro-plan attribution gates as inclusion filter** for anything entering RAG:
  valid sha + archived PDF + EIN joins + NTEE/material-type present (per
  `locard/operations/macro-plan-corpus-integrity-rag-readiness.md`).
- **REQ-PROD-019 — NTEE cohort onboarding via research (operator's operating model, 2026-06-12)**:
  every NEW NTEE cohort adopted into the product runs through the research pipeline FIRST — sample
  docs → full pipeline (incl. stage-0 doc gate) → page-truth audit of a sample → cohort scorecard →
  rules tuned/locked if new error classes appear → cohort admitted to production only when bars are
  met. Research is the permanent testbed; production never ingests an unaudited cohort.
- **REQ-PROD-018 (BUILT on research 2026-06-12; production wiring pending) — corpus-inclusion (low-value report) gate**: the
  plain-text detector (flavor A: figure_count==0 + high text density — dca5a824, 4afba9fd) +
  narrative/event-report detector (flavor C: >~80% of extracted "metrics" fail the measurable-value
  check) + single-program/administrative flavor B (collect instances first). Named in
  low-value-reports.md since the review rounds but NEVER built; operator re-flagged 2026-06-12.
- **REQ-PROD-017 — spread/landscape page handling**: store per-PAGE dimensions at parse; flag
  2-up/landscape pages; overlay rendering and printed-page labels must map Docling coordinate space
  to the actual PDF page space (efd61fea: boxes drawn on whitespace; duplicated spread pages exist).
  Text grounding and relative geometry are unaffected — this is an overlay/provenance-display class
  (and likely much of ENH-002's "bbox imprecision").
- **REQ-PROD-016 — visible redaction rendering for published story spans**: PII substitutions are
  never silent — bracketed replacements in the rendered span + per-story privacy disclosure +
  `pii_redacted`/`redaction_type` fields; internal record stays true-verbatim. Precedence: name
  granularity = floor, sensitive context = ceiling (stricter wins).

## C2. From the unbuilt-mechanism sweep (operator-approved 2026-06-12)

- **REQ-PROD-030 — garble detector (ENH-004)**: doc-level glyph-soup/PUA + single-char-token fraction
  at stage 0; route to re-extract (pdftotext) or exclude. Mostly mitigated by config B; detector still owed.
- **REQ-PROD-031 — standalone/bare-abstraction gate (ENH-005)**: vague-subject word-list pre-filter +
  LLM micro-check ("what is being measured?"); standalone is a ship criterion and must be gated, not
  only measured.
- **REQ-PROD-032 — guarded page-search re-ground (ENH-007 fix 3)**: last recall residual; distinctive
  value (≥1000) or subject co-occurrence required; never small ints.
- **REQ-PROD-033 — span-faithfulness verification, LAYERED (operator-designed v3, 2026-06-12)**:
  L1 deterministic span-alignment on EVERY metric (quantity-in-span; unit head-noun in span;
  predicate lemma/synonym overlap — catches predicate substitution 'presented'->'hosted'; NE
  provenance with **CONTEXT_DERIVED vs HALLUCINATED** tiers — org name from doc context is tiered,
  not failed; dependency-position refinement: quantity as framing of a qualitative predicate
  downgrades confidence). L2 NLI faithfulness (MiniCheck / AlignScore — purpose-built grounding
  scorers; eval before adoption, threshold-tuned on the gold set). L3 decompose-then-verify (atomic
  claims, doc-wide retrieval, program-scoped) on survivors -> **support tiers: A fully
  span-supported / B quantity-supported + context-derived attribution / C inferred predicate** —
  published metrics carry the tier; display distinguishes context-derived attribution (the BBox
  click-through must show the claim being made). Failures route to review/auto-downgrade, never
  hard-reject. **Regression methodology: gold (span,metric,label) triples + adversarial MUTATION
  suite** (swap_predicate, inject_entity, shift_quantity, change_unit, widen_scope,
  aggregate_claim) — every mutation of a SUPPORTED pair must flip; per-error-type precision/recall.
  Known failure modes encoded: interleaved-program windows (d2e6b6ff:3), cross-page support
  (131309e3:3) -> doc-wide retrieval mandatory; rewrites never auto-applied.
  **FALSIFIED ASSUMPTION (2026-06-12): word-overlap bands do NOT bound drift risk** — known-bad
  cases span all bands (or->and distortion at 0.79 overlap; wrong-caption at 0.75). Overlap measures
  paraphrase DEGREE not SAFETY; surgical one-word drifts live in 'near-verbatim' text. L1 must run
  on EVERY metric; the census is a prioritization hint only, never a safety gate.
- **REQ-PROD-034 — story engineering cluster**: stage-1 prompt sha-lock; per-class drift monitor with
  alarm; low-confidence routing; story-aware chunking (no severed stories at page breaks);
  normalized/fuzzy span anchoring; derived-summary field labeled derived.
- **REQ-PROD-036 — composite (multi-locus) metrics (operator-designed 2026-06-12)**: the model
  legitimately composes metrics from components on DIFFERENT pages (131309e3:3: 'eight group homes'
  p2 + '24-hour care' p3 — 100% correct, previously tossed). Doctrine: **verbatim-or-quarantine
  applies at the ATOM level** — every component verbatim-supported somewhere in the doc; composition
  may never create new numbers (the 64+50=114 line holds). Schema: **multi-citation provenance**
  (list of component→location anchors, REQ-011/012 extension). Risk control: the LLM sanity check
  verifies the JOIN ('does the doc present these as the same entity/program/timeframe?' — the
  d2e6b6ff interleave is the canonical wrong-join), **publish-on-proof polarity**: composite
  publishes ONLY if join-confirmed; unconfirmed -> quarantine. Composites are a minority class ->
  LLM cost trivial. **PROVEN FAILURE MODE (2026-06-12): a TEXT-ONLY join check blessed known
  stat-grid mispairs** (all atoms genuinely exist in the doc; cross-pairing looks plausible without
  layout) — join verification for grid-sourced claims REQUIRES page-layout awareness (geometry/
  page-read verify stage); page-truth adjudications always override the join check. **ORDERING RULE (operator catch,
  38c9a81d:9): recovery = grounding-objection withdrawal ONLY — recovered metrics MUST re-enter the
  pipeline at the validity stage** (factoid/year/duration/etc. rules ran on published metrics only,
  so a grounding-quarantined factoid never met them); recovery never routes straight to publish.
- **REQ-PROD-035 — parse metadata + config hygiene**: source_method/bbox_status per item; retire the
  dormant bare DocumentConverter path; explicit do_table_structure; force_ocr route for image-only docs.

## C3. REJECTED BY DESIGN (do not resurrect — sweep bucket C)
- Geometry auto-publish on designed pages (ENH-003 'graduation') — superseded by flag→verify.
- Grounding rule R3 (near-contiguous reordered match) — violates verbatim-or-quarantine.
- Spike-era Docling knob hunts (Egret-XL, orphan-cluster levers) — overtaken by config-B lock + verify stage.
- Table-reconciliation verifier & header normalizer — sourced from the DISCREDITED verification handoff.
- Rounded-value repair — 50% precision (near-duplicate trap).

## C. Carried-forward pipeline obligations

- **REQ-PROD-020 — render single-namespace (`m##`) markers** applied at the next extraction
  (prefix-swap prevention; twin-repair stays as the net).
- **REQ-PROD-021 — Docling config locked: CONFIG B (pypdfium2 backend, default pipeline otherwise). DONE 2026-06-11** (operator-confirmed from the 120-doc A/B/C test: garble 1→0 docs, +6.6% elements, +30% bare-number capture, zero error/speed cost; C showed no measurable gain over B). Applies to ALL future parses incl. the national run.
- **REQ-PROD-022 — verify stage wired** (geometry filter -> small-VLM verifier) with its decisions
  recorded per metric (`verifier_verdict` field), per the validated two-stage architecture.
- **REQ-PROD-023 — the M4 suite passes against production output** (same fixture, same bars) before
  cutover; production numbers re-locked after.

## Status

| REQ | status |
|---|---|
| 001–004 | PENDING |
| 010–017, 019 | PENDING |
| 030–036 | PENDING (sweep adds + composite) |
| 018 | BUILT on research (prod wiring pending) |
| 020, 022, 023 | PENDING |
| 021 | **DONE 2026-06-11** (config B) |

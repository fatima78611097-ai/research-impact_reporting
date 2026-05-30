# Plan 0057 — LLM Extraction Faithfulness Verification

- **Project:** 0057   **Spec:** `locard/specs/0057-llm-faithfulness-verification.md` (specified)
- **Status:** conceived (initial plan draft — awaiting plan-review, red-team-plan, human approval)
- **Author:** Architect, 2026-05-30

> Builder-executable plan. Phases are sequenced; each lists deliverables, tests, and acceptance. Resolves the red-team plan-phase checklist (multi-span semantics, R2 tie-breaks, value normalization, 0060-trust, determinism).

## Key plan decisions (resolving spec §10 + red-team)
1. **Source-text provider is dependency-injected.** The gate grounds against a `SourceTextProvider`. **v1 uses current parsed text** (`lava_parse.sections` + `tables`); when **0060** ships, its `pdftotext`-repaired text swaps in behind the same interface. **This unblocks 0057 now** without waiting on 0060 — and garble cases simply surface as Tier-C in v1 and get rescued when repair lands (the failure catalog quantifies the impact).
2. **Tie-break:** test **R1 then R2**; first match wins; record `grounding_rule ∈ {R1, R2, none}`. Deterministic by construction.
3. **Multi-span:** offsets is a list, **cap N = 8** (config); grounded iff **all** required spans match; >N spans or scattered single-token spans → defect (anti-gaming/DoS).
4. **Numeric normalization v1 = defect-by-default.** No "$2.8M"↔"$2.8 million" allowlist in v1 (revisit with catalog data). Normalization N alters neither digits nor units.
5. **Quarantine = a flag, never a delete.** Tier-C rows stay in the DB with `verification_tier='quarantine'`, excluded from published surfaces by query.
6. **DDL is operator-run.** Claude cannot apply RDS migrations; the plan produces the migration SQL for the operator (single-operator DB, no zero-downtime needed).

## Phase 0 — Decisions frozen + provider interface
- Freeze the decisions above; define `SourceTextProvider` (returns normalized-ready section+table text for a `content_sha256`, with a `source` tag = `docling` | `pdftotext-repaired`).
- **Deliverable:** interface + the frozen-decisions doc-block in code. **Acceptance:** interface stubbed; v1 (current-text) implementation present.

## Phase 1 — The grounding verifier (pure, deterministic, isolated)
- `lavandula/faithfulness/grounding.py`: `normalize(text)` (lowercase → collapse ws → Unicode NFC → fold curly/straight quotes & dashes; reject non-printable control chars except normalized whitespace); `check(snippet, source_text, tables) -> Verdict{rule, offsets[], grounded:bool}` implementing R1 (substring) + R2 (same-table-row label+value); `value_present(value, denominator, snippet) -> bool`.
- **Tests (the spec's required set — this module is TDD'd against the spec's counterexample table):** whitespace/NFC/quote/dash equivalence; R1 contiguous; R2 across cells + multi-row; multi-span all-match + N-cap; numeric-reformat → defect; scatter → defect; control-char rejection; determinism (same input → identical Verdict).
- **Acceptance:** 100% of the spec §4.2 counterexamples produce the spec's verdict; module has no DB/network deps (pure).

## Phase 2 — Data-model migration (operator-run)
- Migration SQL adding to `lava_vocab.llm_metrics` (+ provenance fields on `llm_stories`): `verification_tier` enum, `grounding_rule` text, `grounding_offsets` jsonb, `context_window` text, `tier_b_confidence` numeric NULL, `modality` enum (`achieved|proposed|advocated|projected|unspecified`), `numerator`/`denominator` numeric NULL (ratio preservation). All **nullable**, default `verification_tier='unverified-legacy'`; index on `verification_tier`.
- **Deliverable:** `lavandula/migrations/0057_faithfulness_fields.sql` + a rollback. **Operator applies it.** **Acceptance:** columns present, existing rows default-tiered, backward compatible (no existing query breaks).

## Phase 3 — The gate runner (batch + score)
- Management command `verify_faithfulness <run_tag>`: iterate the run's `llm_metrics`/`llm_stories`, pull source text via the provider, run the Phase-1 verifier, write `verification_tier`/`grounding_rule`/`grounding_offsets`, set Tier-C for failures, set Tier-B for OCR sources (from the provider's `source`/0060 signal), emit a **per-run faithfulness score** to `extraction_runs.stats_json`.
- **Tests:** integration on a fixture run (mixed grounded/defect/OCR); determinism (re-run = identical tiers); idempotency.
- **Acceptance:** every fact gets a tier; Tier-C never appears in the "published" query; score emitted + trended.

## Phase 4 — Run on existing data → failure catalog (test→fix Phase 1)
- Run `verify_faithfulness` over run 10 (68,772 metrics + 10,679 stories) with the **hardened** normalization → produce the **true** grounding rate and the categorized failure catalog (the 4 categories: composed prose / numeric reformat / table cell-vs-prose [now R2-handled] / unicode [now N-handled]).
- **Deliverable:** `locard/operations/0057-failure-catalog.md` (rates + category breakdown + examples). **Acceptance:** true defect rate established; categories quantified; artifacts (table/unicode) confirmed reduced vs the crude 13%/22% baseline.

## Phase 5 — Disclosure contract + presentation handoff
- Document the **tier → badge** contract (viewer + API): API exposes `verification_tier`, `tier_b_confidence`, label; published surfaces render Tier-A "✓ sourced", Tier-B "OCR — unverified" + confidence, never expose Tier-C. Viewer/API badge rendering = thin follow-on (handoff ticket, not core 0057 build).
- **Acceptance:** contract documented; API returns the tier fields; quarantine excluded from public endpoints.

## Phase 6 — Lock the gate (regression)
- Promote `verify_faithfulness` to run automatically after each extraction run; commit regression **fixtures derived from the failure catalog**; CI runs the Phase-1 verifier tests.
- **Acceptance:** new extraction runs are auto-verified; the verifier test suite is in CI; gate is permanent.

## Dependencies & handoffs
- **0060 (parse fidelity / `pdftotext` repair):** swaps in behind `SourceTextProvider` (Phase 0 interface) — not a blocker for v1.
- **Extractor prevention (extractive spans):** companion in the LLM-extractor project; **driven by the Phase-4 failure catalog**; the gate is the backstop and does not require it.
- **Viewer/API badge rendering:** handoff from Phase 5.

## Acceptance criteria (rollup, from spec §6)
- Tier assigned to 100% of facts; **0 published ungrounded**; deterministic verdicts with recorded rule+offsets; per-run faithfulness score; after extractor fixes (companion), **≥99% of non-OCR facts Tier-A**; verifier unit suite green in CI.

## Risks / traps (banked this session)
- Do **not** use sequence-alignment for grounding (measures order, not content — 0060 finding).
- Hold the whitespace/Unicode-OK vs semantic-paraphrase-defect line precisely.
- Never collapse ratios to scalars (denominator is part of the metric).
- DDL is operator-gated (Phase 2 produces SQL; operator runs it).
- Don't block on 0060 — the provider injection is what decouples them.

## Suggested build sequencing for the builder
Phase 1 (pure verifier, TDD) → Phase 2 (migration SQL, hand to operator) → Phase 3 (runner) → Phase 4 (catalog) → Phase 5 (contract) → Phase 6 (lock). Phases 1 and 2 can proceed in parallel.

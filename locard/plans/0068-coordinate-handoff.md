# Plan 0068 — Coordinate Handoff: Per-Metric Docling-Location Markers

**Spec**: locard/specs/0068-coordinate-handoff.md
**Status**: draft
**Created**: 2026-06-16

> Builder-executable plan. Phases are sequenced; each lists deliverables, tests, and acceptance.
> Resolves the spec §10 open questions and the red-team plan-phase checklist below.

## Executive Summary
Productionize the validated research marker chain (`render.render_tagged` + loc1 prompt +
idmap resolution) so every extracted metric carries a server-validated Docling-location marker
(`⟨t#⟩` text / `⟨c#⟩` cell) resolved to `{page, bbox, row, col}` and stored on the metric row.
Build is split into a **pure render module**, a **pure resolution module** (both TDD'd), an
**operator-run migration**, a **production extraction runner**, and a **validation pass** against
the 49 coord-bearing demo docs + the frozen CanCare/BGCSM fixtures. No judgment/gating here —
that is 0069. DDL is operator-gated (no direct RDS writes).

## Key plan decisions (resolving spec §10 + red-team)
1. **One-pass loc1, not the demo's two-pass.** The extractor cites `value_ref`/`subject_ref`
   directly via the loc1 prompt (one DeepSeek call), avoiding the demo's separate GROUND pass.
   Phase 0 measures one-pass resolvable-rate vs the two-pass demo on the 49-doc set; one-pass
   ships if within 5pp (expected — loc1 was built for this).
2. **`LOC1_BASELINE` is measured in Phase 0** (loc1 resolvable-`value_ref` rate on the 49-doc set)
   and frozen as the acceptance reference.
3. **Coordinate threshold `T` (eligibility):** a doc is eligible iff ≥ 80% of its text elements
   carry a bbox AND 100% of its table cells carry row+col+bbox. Phase 0 confirms `T` against the
   re-parsed-doc distribution; adjust only with evidence.
4. **`MAX_IDMAP_ITEMS`:** Phase 0 measures the items/doc distribution; set the cap at ~p99.9 + margin
   (working default 8,000). Over-cap → skip `idmap_too_large`.
5. **Surge thresholds (§5.9):** per-doc quarantine if > 50% of selected metrics have null/forged
   `value_ref` **AND ≥ 3 invalid refs** (absolute floor avoids false quarantine on 1–2-metric docs
   — Gemini MEDIUM); per-run alert if skip-rate > 20% or forged-ref-rate > 5%. Tunable in Phase 0.
6. **Idmap: recompute on demand** (deterministic), not persisted (spec §10.1).
7. **`period` slot: OUT** of 0068 (spec §2) — later slot spec.
8. **Migration ordering: interim on `lava_vocab.llm_metrics`.** 0068 ships additive columns there
   under a new `run_tag`; columns move to the 0066 clean schema when it lands. 0068 does NOT block
   on 0066. Legacy 0051 rows retained read-only.
9. **Coordinate re-parse is an external dependency** (parse track, operator-run, qualified set
   only). 0068 extraction **skips** uncoordinated docs; the first validation run uses the 49 docs
   that already have coordinates.

## Acceptance Test Matrix (MANDATORY — maps spec §7 AC + §6 tests)
| AC | Acceptance Criterion (spec) | Phase | Test type | Location |
|----|------------------------------|-------|-----------|----------|
| AC1 | Resolvable-`value_ref` ≥ `LOC1_BASELINE`−5pp on the 49-doc set (denominator = selected metrics on **eligible** docs only; skipped docs excluded — Codex MEDIUM) | P5 | Integration | `tests/integration/test_0068_validation.py` |
| AC2 | 100% table-located metrics resolve to a cell marker (row+col+bbox) | P2/P5 | Unit+Integration | `tests/unit/test_0068_resolve.py` |
| AC3 | Round-trip: stored coords == `idmap[value_ref]` (50-sample) | P2/P5 | Unit | `test_0068_resolve.py` |
| AC4 | Eligible docs meet §5.4 threshold, verified by count | P4 | Integration | `test_0068_runner.py` |
| AC5 | `value_ref=null` metrics stored + flagged, not dropped | P2/P4 | Unit+Integration | `test_0068_resolve.py` |
| AC6 | ~~CanCare/BGCSM published-metric content unchanged vs frozen~~ **DEFERRED** (architect PR #53 review 2026-06-17; ill-posed vs composed-sentence baseline — re-scoped to 0068↔0069 diff). Tracked in `0068-validation.md`. | — | — | follow-up |
| AC7 | Zero invented markers (every stored ref in idmap) — code-enforced | P2 | Unit (adversarial) | `test_0068_resolve.py` |
| S1 | Item ceiling: over-`MAX_IDMAP_ITEMS` doc skipped, no OOM | P1 | Unit (adversarial) | `test_0068_render.py` |
| S2 | ID-collision → safe skip, not overwrite | P1 | Unit | `test_0068_render.py` |
| S3 | NaN/inf/neg/out-of-range bbox → bbox=null, no malformed jsonb | P1 | Unit (adversarial) | `test_0068_render.py` |
| S4 | Forged/out-of-range ref → null, never string-parsed | P2 | Unit (adversarial) | `test_0068_resolve.py` |
| S5 | Skipped/truncated/null-marker counts in run report; surge trips quarantine | P4/P6 | Integration | `test_0068_runner.py` |
| S6 | Deterministic render IDs (same parse rows ⇒ identical idmap) | P1 | Unit | `test_0068_render.py` |
| S7 | Truncation retains table cells, drops narrative first, sets `truncated` | P1 | Unit | `test_0068_render.py` |
| S8 | Element-level fail-soft: ref resolves only to `page=null,bbox=null` → `marker_resolved=false`, retained (distinct from forged/absent) | P2 | Unit | `test_0068_resolve.py` |
| S9 | Rerun idempotent: **atomic** delete-then-insert per `(run_id, content_sha256)` (crash-safe) | P4 | Integration | `test_0068_runner.py` |
| S10 | Unknown/unverifiable `parse_version` rejected (`parse_unverified`), not trusted | P4 | Integration | `test_0068_runner.py` |
| S11 | Source-derived text escaped before logs/stats/UI | P4 | Unit | `test_0068_runner.py` |
| S12 | bbox jsonb strict shape (exactly `l,t,r,b,coord_origin` scalars); nested/extra rejected | P2/P3 | Unit (adversarial) | `test_0068_resolve.py` |
| S13 | Oversized/malformed ref dropped at parse-time (`^⟨[tc]\d+⟩$`) before any sink/resolver | P4 | Unit (adversarial) | `test_0068_runner.py` |
| S14 | Shared sanitizer strips `\r`/`\n`/ANSI; raw marker text cannot reach logs/stats/UI | P4 | Unit | `test_0068_runner.py` |

## Phase Breakdown

### Phase 0 — Freeze decisions + measure baselines (no production code)
- Run the loc1 prompt on the 49 coord-bearing docs (Experiment 0002 set); record
  `LOC1_BASELINE` (resolvable-`value_ref` rate) and the one-pass-vs-two-pass delta. Measure
  items/doc → set `MAX_IDMAP_ITEMS`; confirm threshold `T`; set surge thresholds.
- **Measurement protocol (Codex MEDIUM — reproducibility):** freeze the exact 49-doc `content_sha256`
  list (and its hash) as the dataset snapshot; denominator = selected metrics on **eligible** docs
  only (skipped excluded, per AC1); record the numbers as committed JSON so any reviewer/rerun
  reproduces them.
- **Deliverable:** `locard/operations/0068-baselines.md` (the frozen numbers + dataset hash) + a
  decisions doc-block. **Acceptance:** all spec-deferred numbers (`LOC1_BASELINE`, `T`,
  `MAX_IDMAP_ITEMS`, surge thresholds) fixed with evidence; one-pass loc1 confirmed within tolerance.

### Phase 1 — Tagged render module (pure, deterministic, TDD)
- Port `comp-metric-regression/render.py::render_tagged` to a supported module under `lavandula/`
  reading `lava_parse.sections.source_locations` + `lava_parse.tables.cell_locations`. Add: idmap
  **ID-uniqueness assertion** (collision → raise/skip), **`MAX_IDMAP_ITEMS` ceiling**, **bbox
  sanitization + clamp** (numeric/finite/coord_origin/page-bounds), **truncation prioritizing
  table cells** with `truncated` flag, **deterministic tie-break** for duplicate/malformed records.
- DB **read-only**; no network. Returns `(section_text, tagged_text, idmap)`.
- **Tests:** S1, S2, S3, S6, S7 (above). **Acceptance:** deterministic; resource/integrity bounds
  enforced; matches research render semantics on a fixture doc.

### Phase 2 — Marker resolution + canonical selection (pure)
- Given LLM-emitted refs + idmap: validate each ref by **idmap lookup only** (never string-parse);
  resolve to `{page,bbox,row,col,table}`; apply canonical-ref selection (spec §5.7); set
  `marker_resolved`; null/forged → retain+flag.
- **Tests:** AC2, AC3, AC5, AC7, S4. **Acceptance:** every counterexample yields the spec verdict;
  pure (no DB/network).

### Phase 3 — Data-model migration (operator-run)
- **Deliverable:** `lavandula/migrations/0068_metric_markers.sql` (additive columns on
  `lava_vocab.llm_metrics`: `value_ref, subject_ref, value_page, value_bbox, value_row, value_col,
  value_table` + 5 `subject_*`, `marker_resolved bool not null default false`,
  `parse_version text NOT NULL`, `render_version text NOT NULL` (render-module commit/hash so idmap
  regeneration is auditable — Codex LOW)) + a rollback. **Operator applies it** (no direct RDS writes).
- **Idempotency key already exists (resolves Codex CRITICAL):** `llm_metrics` already has
  `run_id` (FK → `extraction_runs.run_tag`) + `content_sha256` — the idempotency key needs **no new
  column**; the migration is purely additive marker/coord/provenance columns.
- **Strict bbox shape (Gemini MEDIUM):** `value_bbox`/`subject_bbox` store exactly
  `{l,t,r,b,coord_origin}` with scalar values; nested objects / extra keys are rejected before
  insert (no jsonb-DoS surface).
- **Migration smoke-test (Codex):** a builder preflight before Phase 4 uses the columns — apply on
  a scratch/clone, assert columns present + types + default, run a representative existing query
  unchanged, then verify rollback restores prior state. Phase 4 is blocked until smoke-test passes.
  Operational note for the operator: additive `ALTER ... ADD COLUMN` (no volatile default) is a
  fast metadata-only change on Postgres — minimal lock; still apply in a safe window (Codex LOW).
- **0066 transition (Codex):** the interim columns are named **identically** to the 0066 clean-schema
  target, so the future move is a **schema relocation, not a re-derivation** — no backfill (0068
  produces fresh rows under its own `run_tag`; legacy rows stay marker-less by design) and **no
  duplicated logic** (the same Phase-1/Phase-2 modules write both paths; only the target table
  changes). Documented in the migration header.
- **Acceptance:** columns present; existing rows default (`marker_resolved=false`); no existing
  query breaks; smoke-test + rollback verified.

### Phase 4 — Production extraction runner
- New runner (extends the Spec 0051 path / dashboard llm-extract): per eligible doc → Phase-1
  render → one-pass loc1 DeepSeek extract → Phase-2 resolve → write metric + markers + resolved
  coords + `parse_version` + `run_tag`. **Doc-level eligibility gate** (skip ineligible, record
  reason); **per-run counters** (skip/null/forged/truncated) + **surge quarantine/escalate**;
  ineligible/skipped docs do NOT enter production output.
- **Idempotency (Gemini/Codex, spec §5.6):** a rerun executes **delete-then-insert per the
  existing `(run_id, content_sha256)`**, wrapped in a **single atomic DB transaction** so a
  crash/OOM between delete and insert cannot lose the doc's metrics (Gemini HIGH). Both statements
  use **parameterized queries** (Gemini LOW). Mirrors 0051 `_write_metrics`, hardened to one txn.
- **Skip reporting IN THIS PHASE (Codex — not deferred to P6):** skipped docs are **counted as
  failed extraction** and recorded with reason + per-run skip-rate in `extraction_runs.stats_json`;
  "does not enter production output" AND "reported + counted as failed" are both runner
  responsibilities, asserted by tests here.
- **`parse_version` provenance binding (Gemini + Codex, spec §8):** before accepting a coordinate
  snapshot, the runner validates the doc's `parse_version` against `lava_parse.parse_runs`
  provenance; an **unknown/unverifiable parse_version is rejected** (doc skipped `parse_unverified`,
  re-resolve from a known-good parse) — never trusted blindly.
- **Strict ref validation at parse-time (Gemini HIGH):** an emitted `value_ref`/`subject_ref` is
  validated against the strict grammar `^⟨[tc]\d+⟩$` the instant the LLM output is parsed; a ref
  that doesn't match (5MB payload, injected prose, etc.) is **dropped to null and counted as forged
  BEFORE** it reaches logging, `stats_json`, or the resolver — never escaped-and-stored.
- **Single shared sanitizer for all sinks (Gemini MEDIUM, Codex MEDIUM):** source-derived text
  written to logs / `stats_json` / review UI passes through one shared utility that strips/replaces
  `\r`, `\n`, and ANSI escape sequences (prevents terminal log-forging) plus context-appropriate
  escaping; a unit test proves raw marker-like strings cannot reach any sink.
- **Tests:** AC4, S5, S8; integration on the 49-doc set; eligibility skip + skip-counted-as-failed;
  idempotent rerun (delete-then-insert); `parse_unverified` rejection; surge threshold; log/stats
  escaping. **Acceptance:** runs end-to-end on the 49-doc set; abnormal states counted + surfaced;
  rerun is idempotent; unverifiable parse_version rejected.

### Phase 5 — Validate vs baseline + fixtures
- Run Phase 4 on the 49-doc set + the frozen CanCare/BGCSM fixtures.
- **Deliverable:** `locard/operations/0068-validation.md` (resolvable rate vs `LOC1_BASELINE`,
  table-cell coverage, fixture diff, round-trip sample). **Acceptance:** AC1, AC2, AC3, AC6 pass.

### Phase 6 — Reporting/observability + handoff to 0069
- Per-run report (in `extraction_runs.stats_json`) surfaces skipped/truncated/null-marker/forged
  counts; marker+coord fields documented as the 0069 gate input contract.
- **Acceptance:** abnormal states first-class in reporting; 0069 can read markers+coords without
  re-rendering.

## Dependencies & handoffs
- **Coordinate re-parse** (parse track, operator): populates `source_locations`+`cell_locations`
  on the qualified set; 0068 skips docs without it. First run uses the existing 49 coord docs.
- **0066 clean schema:** 0068 ships interim on `llm_metrics`; columns migrate when 0066 lands.
- **0069 (precision gates):** consumes 0068's markers + resolved coords (the value-at-marker check,
  is-a-metric, mispair quarantine, publish decision).
- **0067 (qualification):** bounds the re-parse set; not a code blocker for the 49-doc validation.

## Risks / traps
- **DDL is operator-gated** (Phase 3 produces SQL; operator runs it; no direct RDS writes).
- **Coordinate availability** is the external long pole — Phases 1/2/5 proceed on the 49 docs now;
  full-scale needs the re-parse.
- **One-pass vs two-pass** is a measured Phase-0 gate, not assumed — if one-pass underperforms,
  fall back to the demo's two-pass (extract+ground) without re-architecting Phases 1–3.
- **Idmap recompute determinism** is load-bearing for round-trip and stored-coord trust; Phase 1
  S6 test guards it.

## Suggested build sequencing
Phase 0 (measure) → Phase 1 (render, TDD) ∥ Phase 3 (migration SQL → operator) → Phase 2 (resolve,
TDD) → Phase 4 (runner) → Phase 5 (validate) → Phase 6 (reporting/handoff). Phases 1 and 3 run in
parallel; 2 depends on 1; 4 depends on 1+2+3.

## Red-team-plan resolutions (Gemini + Codex, both REQUEST_CHANGES → addressed)
- **Codex CRITICAL** idempotency key column → uses existing `(run_id, content_sha256)`, no new column (Phase 3).
- **Gemini HIGH** crash between delete/insert → single **atomic transaction** (Phase 4).
- **Gemini HIGH** escape-vs-validate refs → strict `^⟨[tc]\d+⟩$` parse-time validation (Phase 4).
- **Codex HIGH** provenance NOT NULL → `parse_version`/`render_version` NOT NULL + parse_runs binding (Phase 3/4).
- **Gemini MEDIUM** bbox jsonb DoS → strict `{l,t,r,b,coord_origin}` scalar shape (Phase 3, S12).
- **Gemini MEDIUM** log-forging → sanitizer strips `\r`/`\n`/ANSI (Phase 4, S14).
- **Gemini MEDIUM** low-N surge → `>50% AND ≥3 invalid refs` floor (decision §5).
- **Gemini LOW** parameterized deletes; **Codex LOW** render_version audit + DDL lock note.
- **Codex MEDIUM** Phase-0 measurement protocol + AC1 eligible-only denominator.

## Plan-phase hardening checklist
All plan-review + red-team-plan findings resolved above. Remaining genuinely-deferred items are
the Phase-0 measured numbers (`LOC1_BASELINE`, `T`, `MAX_IDMAP_ITEMS`, surge thresholds) and the
one-pass-vs-two-pass confirmation — all set in Phase 0 before build.

## Consultation Log

### First Consultation (After Initial Draft)
**Date**: 2026-06-16
**Models Consulted**: Gemini (gemini-3-pro), Codex (GPT-5)
**Commands**:
```
consult --model gemini --type plan-review plan 0068
consult --model codex  --type plan-review plan 0068
```
**Key Feedback**: Gemini **COMMENT** (HIGH) — idempotency/sanitization/provenance not assigned to
phases. Codex **REQUEST_CHANGES** (HIGH) — `parse_version` provenance path, skip-reporting in the
runner, 0066-transition under-specified, missing migration smoke-test, missing fail-soft test.
**All addressed** (Phase 3/4 edits + test matrix S8).

### Red Team Security Review (MANDATORY)
**Date**: 2026-06-16
**Commands**:
```
consult --model gemini --type red-team-plan plan 0068
consult --model codex  --type red-team-plan plan 0068
```
Both **REQUEST_CHANGES**. Findings: crash between delete/insert → data loss (Gemini HIGH), escape-
vs-strict-validate refs (HIGH), missing `content_sha256` idempotency key (Codex CRITICAL — already
exists on `llm_metrics`), `NOT NULL` provenance (HIGH), bbox jsonb DoS shape (MEDIUM), log-forging
`\r`/`\n`/ANSI (MEDIUM), low-N surge floor (MEDIUM), reproducible Phase-0 protocol + AC1 denominator
(MEDIUM). **All addressed** (see "Red-team-plan resolutions" + Phase 3/4 + tests S9–S14).
**Verdict**: APPROVE — all findings resolved; **0 unresolved CRITICAL**.

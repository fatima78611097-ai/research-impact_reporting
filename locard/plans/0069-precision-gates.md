# Plan 0069 — Precision Gates to Production (marker-grounded publish/quarantine)

**Spec**: locard/specs/0069-precision-gates.md
**Status**: draft
**Created**: 2026-06-17

> Builder-executable. Phases sequenced; each lists deliverables, tests, acceptance. Resolves spec
> §10 open questions + the red-team checklist (below). DDL operator-gated (no direct RDS writes).

## Executive Summary
Consume 0068's stored markers and decide **publish vs quarantine** per metric: a **pure ported
gate** (value-verbatim-at-marker, subject grounded, small-int measurable) reproduces research
`gate.verdict` (regression-locked); **production policies** (is-a-metric re-home, prose de-dup,
mispair detect-and-quarantine, stale-coords) layer on top, kept separate so regressions isolate.
Decisions land on `llm_metrics` under a server-assigned `gate_run_id`; a `published_metrics` view
exposes only the publish set (slots, no `metric_text`); a **human spot-review** measures precision
vs a named SLA before the run is "shippable". Validate on the live `0068-markers-2026-06-17` run
(1,066 markers) + frozen fixtures. Mispairs are **quarantined + triaged recoverable** (recovery is
0070, not here).

## Key plan decisions (resolving spec §10 + red-team)
1. **`PUBLISH_PRECISION_SLA` (spec §10.1):** propose **≥98% right-number-AND-right-label per-metric**
   on the published spot-sample, **and ≤1 wrong published metric per org** — confirm the exact
   numbers with the operator in Phase 0 (operator bar: "95% not enough, fewer-but-accurate").
2. **Spot-review sample (§10.1):** stratified — prose vs table-located × across orgs — sized for a
   tight CI at the SLA (working: ~150–200 published metrics; set in Phase 0). Frozen + hashed.
3. **`gate_confidence` (§10.2):** EMIT a coarse advisory score (grounding margin + co-location
   strength); **does not affect the decision**, only prioritizes the spot-review sample.
4. **Mispair detection (§10.3):** MVP relies on **subject-grounding + a conservative
   column-coherence DETECT** (re-introduce `gate.column_guard` in *detect→quarantine only* mode),
   but **only after Phase 0 measures its false-flag rate** on the 1,066-marker run; if too noisy,
   ship subject-grounding + spot-review alone and defer column-coherence. **Never auto-fix.**
5. **`gate_decision` vs `verification_tier` (§10.4):** `gate_decision` is authoritative for
   marker-bearing rows; the published view filters `gate_decision='publish'`. 0057
   `verification_tier` retained as secondary/legacy, not required by the view.
6. **De-dup:** the §5.4 heuristic (value == + `labels_agree` + `gate.co_located`), keep
   lowest-reading-order, keep-both-on-uncertain.
7. **Stale-coords:** always quarantine `stale_coords`; no re-resolve (§5.8).

## Acceptance Test Matrix (maps spec §7 AC + security)
| AC | Criterion | Phase | Test type | Location |
|----|-----------|-------|-----------|----------|
| AC1 | Pure gate reproduces `gate.py` verdicts on the frozen counterexample set | P1 | Unit (oracle) | `tests/unit/test_0069_gate.py` |
| AC2 | Every metric in `0068-markers-2026-06-17` gets a decision+reason; split+histogram reported, reproducible | P4 | Integration | `test_0069_runner.py` |
| AC3 | `marker_resolved=false` → `quarantine/unmarked` | P2 | Unit | `test_0069_gate.py` |
| AC4 | `published_metrics` returns only publish, slot-shaped; org-detail reads it | P5 | Integration | `test_0069_view.py` |
| AC5 | Spot-review protocol runs → measured precision; shippable iff ≥ `PUBLISH_PRECISION_SLA` | P6 | Integration | `test_0069_spotreview.py` |
| AC6 | Mispairs quarantined, never auto-relabeled | P2 | Unit | `test_0069_gate.py` |
| AC7 | De-dup never drops a spatially-distinct value+label | P2 | Unit | `test_0069_dedup.py` |
| AC8 | CanCare/BGCSM publish-set content unchanged vs research verdicts | P7 | Regression | `test_0069_fixtures.py` |
| AC9 | Idempotent **atomic UPDATE** of `gate_*` per `(gate_run_id, content_sha256)` — re-run → same values; 0068 marker payload untouched | P4 | Integration | `test_0069_runner.py` |
| AC10 | Re-gate updates the view to the new current decision; never a stale or quarantine row | P5 | Integration | `test_0069_view.py` |
| AC11 | Determinism: identical inputs → identical decisions + reason histogram | P4 | Integration | `test_0069_runner.py` |
| AC12 | `parse_version` mismatch → `quarantine/stale_coords` | P2/P4 | Unit+Integration | `test_0069_gate.py` |
| S1 | `to_float` rejects NaN/Inf/overflow → `value_out_of_bounds`, no malformed DB write | P1 | Unit (adversarial) | `test_0069_gate.py` |
| S2 | Measurable-check: injected "OUTPUT YES" / malformed / timeout → `measure_unchecked` | P2 | Unit (adversarial) | `test_0069_measure.py` |
| S3 | `gate_run_id` server-assigned monotonic immutable; backdated run can't become active | P3/P5 | Integration | `test_0069_view.py` |
| S4 | Per-metric regex time-budget → `gate_timeout`, no hang on crafted input | P2 | Unit (adversarial) | `test_0069_gate.py` |
| S5 | Spot-review sample frozen+hashed; reviewer id+timestamp; outputs append-only | P6 | Integration | `test_0069_spotreview.py` |

## Phase Breakdown

### Phase 0 — Freeze decisions + measure (no production writes)
- With the operator: set `PUBLISH_PRECISION_SLA` (per-metric + per-org) and the spot-review sample
  size/stratification. Measure on `0068-markers-2026-06-17`: the prose-internal **de-dup rate**, and
  the **column-coherence false-flag rate** (decides whether mispair-detect ships or defers, §dec.4).
- **Column-coherence false-flag artifact (resolves Codex):** run `gate.column_guard` in
  report-only mode over the 1,066 markers, hand-check a sample of its flags, and record the
  measured **false-flag rate** in `0069-baselines.md`. **Ship the mispair column-detect iff
  false-flag ≤ 20%**; else defer it to 0070 and rely on subject-grounding + spot-review. The
  threshold + measured rate are the decision artifact.
- **Deliverable:** `locard/operations/0069-baselines.md` (frozen numbers + dataset hash + the
  false-flag measurement). **Acceptance:** SLA + sample + mispair-detect decision fixed with evidence.

### Phase 1 — Pure ported gate (oracle, TDD, regression-locked)
- Port `gate.verdict` + helpers to `lavandula/nlp/` (pure, no DB/network). Add float
  finiteness/bounds (S1) and per-metric regex time-budget (S4). Reproduce research verdicts on the
  frozen counterexample set (AC1).
- **Acceptance:** AC1, S1, S4 green; pure; byte-deterministic.

### Phase 2 — Production policies (pure where possible)
- Is-a-metric reject rules re-homed onto value+label+marker source; **measurable-value check**
  (hardened: delimited untrusted wrap, strict parser, max-len, refusal, ≤2 retries/total budget,
  malformed→`measure_unchecked`, S2); **de-dup** (§5.4 heuristic, AC7); **mispair detect→quarantine**
  (subject-grounding + optional column-coherence per Phase 0); **stale-coords** quarantine (AC12).
- Decision precedence: fixed order, first-failure-wins, single `gate_reason`.
- **Acceptance:** AC3, AC6, AC7, AC12, S2.

### Phase 3 — Data-model migration (operator-run)
- **Storage model (resolves Gemini #1 / Codex #1,#4):** gate decisions are **single-valued per
  metric row** — the `gate_*` columns hold the metric's **current** decision; re-gating **UPDATEs**
  them (no per-run decision rows, no delete/insert of metric rows). "Active run" is therefore
  intrinsic — the column IS the current decision, so the published view needs **no run-join**. The
  `gate_runs` registry is the **audit trail**; `gate_run_id` on the metric records which run last
  wrote it.
- **Deliverable:** `lavandula/migrations/lava_vocab/0069_gate_decision.sql` — additive columns on
  `llm_metrics` (`gate_decision text`, `gate_reason text`, `gate_run_id integer`,
  `gate_confidence numeric NULL`) + index `(gate_run_id, gate_decision)` + a `gate_runs` table
  (monotonic `id` via identity/sequence — **server-assigned, not client-supplied**; `created_at`;
  `source_run_id` FK; immutable) + rollback. Migration smoke-test + 0066-transition note (columns
  ride to the clean schema). **Operator applies.**
- **Acceptance:** columns/index/registry present, defaults safe, `gate_runs.id` monotonic +
  server-assigned (no client id, no backdating), smoke-test + rollback verified.

### Phase 4 — Gate runner
- Allocate a `gate_runs` row (server-assigned id). Iterate a 0068 run's marker-bearing
  `llm_metrics`, **streaming/batching per doc** (re-render via 0068 `marker_render`; bounded memory
  — Gemini #3); run Phase-1 oracle + Phase-2 policies; **UPDATE the `gate_*` columns in-place** on
  the existing rows (`gate_decision`/reason/confidence/`gate_run_id`), **atomic per doc**.
  **Idempotent:** re-gating overwrites the same `gate_*` values — no delete/insert, the 0068 marker
  payload is never touched. Emit per-run report (publish/quarantine counts, reason histogram,
  **quarantine triage**: recoverable-by-relabel / recoverable-by-vision / true-junk) + immutable
  audit to `extraction_runs.stats_json`. Run on `0068-markers-2026-06-17`.
- **Acceptance:** AC2, AC9 (idempotent atomic UPDATE), AC11.

### Phase 5 — Published view + product repoint
- Create `lava_vocab.published_metrics` = `SELECT content_sha256, source_org_ein, metric_value,
  label, unit, geo_impact, value_ref, value_page, value_bbox, value_row, value_col, gate_run_id
  FROM llm_metrics WHERE gate_decision='publish'` — **slot columns only, no `metric_text`**.
  Active-run is intrinsic (single-column current decision, Phase 3) → no run-join. Repoint the
  org-detail surface (`views.py` OrgDetailView) to read this view.
- **Acceptance:** AC4 (publish-only, slot-shaped), AC10 (re-gate → view reflects the new current
  decision, never a stale or quarantine row), S3.

### Phase 6 — Human spot-review + SLA gate
- Freeze + hash a stratified sample of the published set; capture reviewer id+timestamp; store
  results append-only; compute right-number-AND-right-label precision; mark the run **shippable iff
  ≥ `PUBLISH_PRECISION_SLA`** (offline eval — no per-metric write-back).
- **Storage + keying (resolves Codex):** the frozen sample manifest (sampled `llm_metrics.id` list
  + a content hash) is a committed artifact
  `locard/operations/0069-spotreview/<gate_run_id>-manifest.json`; review records are append-only in
  a `gate_review` table keyed `(gate_run_id, metric_id, reviewer, reviewed_at)` — a new review is a
  new row, never an overwrite.
- **Acceptance:** AC5, S5; the measured number recorded.

### Phase 7 — Validate + lock regression
- Validate publish-set content vs research verdicts on CanCare/BGCSM (AC8); deliverable
  `locard/operations/0069-validation.md` (publish/quarantine split, triage counts, spot-review
  precision, fixture diff). Commit regression fixtures; CI runs the oracle tests.
- **Acceptance:** AC8; gate locked.

## Dependencies & handoffs
- **0068** supplies markers/coords + `marker_render` (reused). **0070** consumes the quarantine
  triage (recoverable-by-vision/relabel) — the mispair/infographic recovery. **0066** clean schema:
  gate columns ride along (interim on `llm_metrics`). **0067/scale** gated by Phase 6 SLA.

## Risks / traps
- **DDL operator-gated** (Phase 3 SQL → operator). **Mispair-detect** may defer if column-coherence
  is too noisy (Phase 0 measures, not assumes). **Re-render determinism** is load-bearing for the
  oracle + idempotency (Phase 1 guards). **Spot-review** is the real precision backstop — automated
  publish ≠ trusted until it holds.

## Suggested build sequencing
P0 (measure) → P1 (oracle, TDD) ∥ P3 (migration → operator) → P2 (policies) → P4 (runner) →
P5 (view+repoint) → P6 (spot-review) → P7 (validate+lock). P1∥P3; P2 after P1; P4 after P1+P2+P3.

## Consultation Log

### First Consultation (After Initial Draft)
**Date**: 2026-06-17
**Models Consulted**: Gemini (gemini-3-pro), Codex (GPT-5)
**Commands**:
```
consult --model gemini --type plan-review plan 0069
consult --model codex  --type plan-review plan 0069
```
**Key Feedback**: Gemini **APPROVE** (HIGH) — idempotency should be **UPDATE-in-place** not
delete/insert (0069 writes onto 0068's existing rows); mock the LLM boundary in tests; batch the
re-render. Codex **COMMENT** (MEDIUM) — enumerate the exact published-view columns + run-selection;
make the Phase-0 false-flag measurement a concrete artifact+threshold; specify where the spot-review
manifest/records live + keying; state active-run as an enforced invariant. **All addressed**:
Phase 3 single-column current-decision + `gate_runs` audit (server-assigned monotonic), Phase 4
atomic UPDATE, Phase 5 exact view SQL (no run-join), Phase 0 false-flag artifact (ship iff ≤20%),
Phase 6 manifest + `gate_review` keying.

### Red Team Security Review (MANDATORY)
**Date**: pending
**Commands**:
```
consult --model gemini --type red-team-plan plan 0069
consult --model codex  --type red-team-plan plan 0069
```
**Verdict**: pending

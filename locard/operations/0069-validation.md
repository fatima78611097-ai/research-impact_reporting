# 0069 Validation — gate locked

**Status**: builder-complete (code + tests); live-run rows AWAIT OPERATOR (RDS-gated).
**Spec / Plan**: locard/specs/0069-precision-gates.md · locard/plans/0069-precision-gates.md

## What shipped (Phases 1–7)
| Phase | Deliverable | Tests |
|-------|-------------|-------|
| 1 | `lavandula/nlp/gate.py` — pure ported oracle (value/subject grounding, small-int, float bounds S1, time budget S4) | `test_0069_gate.py`, `test_0069_fixtures.py` |
| 2 | `gate_policy.py` (decide chain, mispair detect, de-dup, stale) + `measure_check.py` (hardened LLM boundary S2) | `test_0069_measure.py`, `test_0069_dedup.py` |
| 3 | `migrations/lava_vocab/0069_gate_decision.sql` (+rollback) — gate_* cols + `gate_runs` (server-id, append-only) | applied on scratch PG |
| 4 | `gate_runner.py` — in-place idempotent UPDATE, advisory lock S7, parameterized, triage report | `test_0069_runner.py` |
| 5 | `0069_published_view.sql` (+rollback) — slot view + read-only-role grant (S6); `views.py` repoint | `test_0069_view.py` |
| 6 | `0069_gate_review.sql` (+rollback) + `spot_review.py` — frozen+hashed sample, append-only, SLA | `test_0069_spotreview.py` |
| 7 | this doc + frozen oracle fixture | `test_0069_fixtures.py` |

## Acceptance criteria (spec §7) status
- **AC1** ✅ oracle reproduces research counterexamples (12 frozen cases).
- **AC2/AC9/AC11** ✅ runner decides every metric; idempotent in-place UPDATE; deterministic — verified on scratch PG with a seeded 0068-shaped run. _Run on the live `0068-markers-2026-06-17` is the operator step._
- **AC3** ✅ `marker_resolved=false` → `quarantine/unmarked`.
- **AC4/AC10/S3/S6** ✅ view is publish-only + slot-shaped; re-gate updates the view; gate_run_id server-assigned monotonic. S6 boundary (corrected per architect review): the dashboard reads as `research_app` (the writer), so the view grants SELECT to `research_ro` (read-only, no raw access) + `research_app`, and the boundary is enforced at the query layer (product reads only `published_metrics`). Hard privilege boundary = repoint the dashboard to `research_ro` (operator follow-up).
- **AC5/S5** ✅ spot-review measures right-number-AND-right-label precision; shippable iff ≥ SLA; sample frozen+hashed; reviews append-only with reviewer id+timestamp.
- **AC6** ✅ mispair quarantines, never relabels (decide returns a decision; the label is untouched).
- **AC7** ✅ de-dup never drops a spatially-distinct value+label.
- **AC8** ✅ oracle logic byte-ported from `comp-metric-regression/gate.py`; the frozen fixture locks it. _CanCare/BGCSM publish-set diff vs research is an operator validation on live data._
- **AC12** ✅ `parse_version`/`render_version` mismatch → `quarantine/stale_coords`.

## Operator run-book (RDS-gated; builder cannot apply DDL)
1. Apply migrations in order: `0069_gate_decision.sql` → `0069_published_view.sql` → `0069_gate_review.sql`.
2. Phase-0 measure: `python3 locard/operations/0069_measure.py 0068-markers-2026-06-17` → fill `0069-baselines.md`; decide `mispair_detect` (≤20% false-flag rule).
3. Live gate run: `gate_runner.run_gate(engine, "0068-markers-2026-06-17", measure_fn=measure_check.deepseek_measure_fn(get_secret("lavandula/deepseek/api_key")), mispair_detect=<phase0>)`.
4. Spot-review: `spot_review.select_sample` → `write_manifest` → grade → `record_review` → `compute_precision`; mark shippable iff ≥ `PUBLISH_PRECISION_SLA`.
5. Record the per-run report + triage (auto-written to `extraction_runs.stats_json.gate_runs.<id>`); paste split + spot-review precision + fixture diff here.

## Quarantine triage buckets (feed 0070, not publish)
recoverable_relabel (mispair, subject_not_grounded) · recoverable_vision (unmarked,
stale_coords, value_not_at_marker, render errors) · true_junk (no_numeric_value,
value_out_of_bounds, not_a_metric[_rule], measure_unchecked, duplicate, gate_timeout).

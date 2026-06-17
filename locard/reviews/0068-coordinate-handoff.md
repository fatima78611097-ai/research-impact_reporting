# Review 0068 — Coordinate Handoff: Per-Metric Docling-Location Markers

**Status**: integrated · **Date**: 2026-06-17
**Spec**: locard/specs/0068-coordinate-handoff.md · **Plan**: locard/plans/0068-coordinate-handoff.md
**PR**: #53 (merged to master) · **Production run**: `run_tag=0068-markers-2026-06-17` (run_id 12)

## Outcome
Shipped the linchpin for the metric-product refactor: every extracted metric now carries a
**server-validated Docling-location marker** (`⟨t#⟩` text / `⟨c#⟩` cell) resolving to
`{page, bbox, row, col, table}`, stored on the metric row with provenance. This is the first
coordinate-grounded metric data in production — the input 0069's precision gate consumes.

**What shipped:** `lavandula/nlp/marker_render` (deterministic tagged render from `lava_parse`),
`marker_resolve` (idmap-lookup-only ref validation, §5.7 canonical selection, fail-soft),
`marker_extract` (one-pass loc1 extraction, eligibility gate, surge quarantine, atomic
delete-then-insert, sanitizer) + additive migration (operator-run) + validation harness. 68 tests.

**Validation (measured):** `LOC1_BASELINE` 84.79% (1054/1243), AC2 table-cell 100%, AC3 round-trip
100%, AC4 eligibility 117/117. **Production write verified live:** 1,066 markers, 97.7% resolved,
439 table-cell-located, 103 docs, all real `parse_version`; 74,240 legacy rows untouched; 14
image-heavy docs quarantined for the vision path by design.

## Lessons learned
1. **Validate-then-port beats build-from-scratch.** The marker mechanism (`render_tagged` + loc1)
   was already proven in research; Phase −1 (Experiment 0002) validated the chain end-to-end on
   real coord docs *before* the spec. The build was a low-risk port, not invention.
2. **The hard error is flattened TABLES, not hero-stat infographics.** Calibration showed
   figure-count is the wrong "hard" signal; cell-level markers (`⟨c#⟩` with row/col/bbox) are
   essential. Made table-cells a hard requirement — 439/1066 production markers are cell-located.
3. **Interim-on-`lava_vocab` was the right call.** Additive columns on the keeper table
   (`llm_metrics`) ride to the future clean schema via `ALTER TABLE … SET SCHEMA` — no rework, no
   blocking on the 0066 cleanup. Columns named identically to the 0066 target.
4. **"No-writes" must be literally enforced.** The first dry-run still wrote an `extraction_runs`
   metadata row — caught in the architect integration review; fixed to be fully read-only.
5. **AC6 (regression-vs-frozen-baseline) was ill-posed.** 0068's short-slot output ≠ the
   *composed-sentence* frozen baseline, so the diff is apples-to-oranges. Re-scoped to a
   0068↔0069 diff (tracked follow-up), not merged with an open AC.
6. **`value_ref=null` (image-only) is a first-class outcome,** not a failure — ~15% of metrics,
   routed to the vision path. Don't drop them.
7. **Process friction worth banking:** `af spawn` validates a `## Consultation Log` with specific
   subsections (`### First Consultation` + `### Red Team Security Review` + `**Verdict**`); builder
   worktree creation needs disk headroom (the repo's committed binary bloat blocked it, which
   surfaced and triggered a full repo-bloat cleanup + reconcile); `af`/`consult` need a pty when
   driven non-interactively.

## Follow-ups (tracked)
- **AC6** regression check → re-scoped to the 0068↔0069 diff (in 0069).
- **0069** consumes `value_ref`/`subject_ref` + resolved coords (value-verbatim-at-marker,
  is-a-metric, mispair detect-and-quarantine → publish/quarantine).
- The 14 quarantined image-heavy docs → vision path (re-scoped vision spec).

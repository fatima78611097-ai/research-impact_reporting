# Spec 0069 — Precision Gates to Production (marker-grounded publish/quarantine)

**Status**: conceived (AI ceiling — human approves `specified`)
**Date**: 2026-06-17
**Depends on**: 0068 (per-metric markers + resolved coords, integrated)
**Blocks**: national scale (Phase 5), the published product surface
**Protocol**: SPIDER

---

## 1. Problem & Motivation

Spec 0068 put a **server-verified location marker** on each extracted metric (`value_ref` /
`subject_ref` → page/box/row/col, stored on `llm_metrics`; production run `0068-markers-2026-06-17`
= 1,066 markers, 97.7% resolved). But **nothing yet decides which of those metrics are accurate
enough to publish.** The only live accuracy signal is Spec 0057's text-substring match, which does
not verify the number is present and cannot catch mispairing (right number, wrong label).

The decision logic already exists and is validated in research (`comp-metric-regression/gate.py`,
`pipeline.py`, `measurable_value_check.py`, `item1_nonmetric_rules.py`) — it runs only because the
research harness feeds it coordinates. **0069 wires that gate onto production markers**: for every
0068 metric, verify the value is verbatim **at its marker**, the subject is grounded, the small
counts are real measurements, reject non-metrics, de-dup, and emit a **publish / quarantine**
decision. This is where "fewer, highly accurate" metrics actually start publishing.

Phase −1 measured the realistic ceiling: the automated gate tops out ~90% precision (the residual
is **table-column mispairing** — value in the right row, wrong column — which has **no reliable
auto-fix**, ENH-003 geometry mis-flags 88%). So 0069 **detect-and-quarantines** mispairs (never
auto-fixes) and pairs the automated gate with a **human spot-review** of the published set to hit
the operator's bar.

## 2. Scope (what 0069 ships) / Non-Goals

**Ships:**
1. **The marker-grounded gate** (port `gate.verdict`): value grounded at `value_ref`, subject
   grounded at `subject_ref`, co-location/anti-mispair check → publish/quarantine + reason.
2. **Small-int measurable gate** (port `measurable_value_check`): for |value|≤20, an LLM check
   that the number measures a real quantity (rejects event=1 / rank / multiplier / identifier).
3. **Is-a-metric reject rules re-homed onto slots+source** (port `item1_nonmetric_rules`):
   forecasts/targets, rankings/awards, year-as-value, tenure/duration, population factoids —
   keyed on `value` + `label` + the marker's source text (NOT a composed sentence).
4. **De-dup (prose-internal):** collapse a metric whose identical value+label appears more than
   once within the same document.
5. **Decision storage:** a `gate_decision` (publish/quarantine) + `gate_reason` + `gate_run_id`
   per metric (additive migration, operator-run).
6. **Published-data contract:** a verified-only, slot-based **view** exposing
   `gate_decision='publish'` rows (value/label/unit/marker/coords) that the product reads; fix the
   org-detail surface that currently shows ALL tiers + the old `metric_text`.
7. **Human spot-review + quarantine triage:** a stratified sample of the published set graded to a
   stated precision SLA (the run isn't "shippable" until it holds); quarantine bucketed into
   recoverable (mispair/relabel, vision) vs true-junk.

**Non-Goals (later):**
- **Vision recovery** of image-only numbers and the 14 image-heavy quarantined docs → vision spec.
- **Prose-vs-infographic de-dup** → needs vision (Phase 4).
- **Mispair auto-repair** → explicitly out (no reliable fix; detect-and-quarantine only).
- **Schema cleanup (0066)**, **document qualification (0067)**, **national scale** — separate.
- 0069 does not re-extract or re-mark; it consumes 0068's `value_ref`/`subject_ref` + coords.

## 3. Background & Validation
- **The gate is validated** (`gate.py`): `value_grounded` is robust to spelled-out / `$`/`M`/`k` /
  rounding / parse-shattered joins, and **hardened for small ints** (exact token match, no
  substring/±1 — closes the "1 matches any stray digit" hole). `subject_grounded` = label-word
  overlap OR co-location. The publish bar = **value grounded at its marker AND subject grounded at
  its marker**. The 10-doc demo (Experiment 0002) ran exactly this and hand-checked 0 errors on the
  published prose set; the gate correctly quarantined rankings (`not_a_metric`) and ungrounded
  values (`value_not_at_marker`).
- **0068 supplies the inputs:** `value_ref`, `subject_ref`, `marker_resolved`, resolved coords, and
  the per-doc parse rows that `marker_render` re-renders deterministically into the `idmap`
  (recompute-on-demand, per 0068 §10.1) the gate needs for the marker's text + co-location.
- **Ceiling (Phase −1):** ~90% automated precision; the residual is table-column mispairing with no
  auto-fix → quarantine + human spot-review.

## 4. The Gate (decision procedure — what publishes)
Per metric (using `value_ref`/`subject_ref` + the doc's recomputed `idmap`):
1. `marker_resolved=false` (null/unlocatable marker) → **quarantine** `unmarked` (vision candidate).
2. **value grounded** — the value is verbatim at `idmap[value_ref].text` (`gate.value_grounded`).
   No numeric value → quarantine `no_numeric_value`; not at marker → quarantine `value_not_at_marker`.
3. **subject grounded** — label words overlap `subject_ref` text OR co-located → else quarantine
   `subject_not_grounded`.
4. **small-int measurable** — |value|≤20 and the measurable-value check says "not a measurement" →
   quarantine `not_a_metric`.
5. **is-a-metric reject rules** (slots+source) — forecast/target, ranking/award, year-as-value,
   tenure/duration, population-factoid → quarantine `not_a_metric_rule`.
6. **mispair / column-coherence** — DETECT incoherent value↔label spatial relationships
   (e.g. value in a cell whose column header conflicts with the label, or value duplicated across
   non-total columns) → **quarantine** `mispair` (NOT auto-fixed).
7. else → **publish**.

The decision + reason are stored; **publish requires every check to pass.** Mispair and
small-int/is-a-metric checks are conservative (quarantine when in doubt — false-publish is costly,
false-quarantine is recoverable).

## 5. Requirements

### 5.1 Re-render for idmap (reuse 0068)
- The gate runner re-renders each doc with the **same deterministic `marker_render`** 0068 used, to
  obtain `idmap[value_ref].text` / co-location fields. No new render logic; recompute-on-demand.
- A doc whose re-render no longer matches the stored `parse_version` → re-resolve, never gate on
  stale coords (0068 §8 carries `parse_version`).

### 5.2 Port the gate (pure, deterministic, TDD)
- Port `gate.verdict` + helpers (`value_grounded`, `subject_grounded`, `co_located`,
  `candidate_numbers`) as a supported, pure module — reproduce the research verdicts on the frozen
  fixtures (the spec's counterexample behavior is the test oracle).

### 5.3 Small-int measurable + is-a-metric (the LLM-assisted layer)
- `measurable_value_check` (DeepSeek) runs only for |value|≤20 (where non-measurements hide).
- Is-a-metric reject rules re-homed onto `value` + `label` + marker source text; each rule measured
  for false-quarantine before activation (precision-over-recall: bias to quarantine, but don't drop
  a real "9 vans" / "served 6 counties").

### 5.4 De-dup (prose-internal only)
- Within a doc, collapse metrics whose `value` AND label (distinctive stems) match AND are not
  spatially distinct. Default to **NOT merging** when uncertain (a false merge destroys a real
  metric). Prose-vs-infographic de-dup is out (vision).

### 5.5 Decision storage (additive migration, operator-run)
- `gate_decision text` (`publish`|`quarantine`), `gate_reason text`, `gate_run_id integer`,
  optional `gate_confidence numeric`. Additive on `llm_metrics` (rides to 0066 clean schema). The
  0057 `verification_tier` (text-substring) is retained but **`gate_decision` is the authoritative
  publish signal** for marker-bearing rows.

### 5.6 Published-data contract
- A view (e.g. `published_metrics`) exposing only `gate_decision='publish'`, surfacing **slots**
  (value, label, unit, value_ref, page/bbox) — not the raw `metric_text`. The org-detail product
  surface reads this view (fixes the current all-tiers/`metric_text` leak).

### 5.7 Human spot-review + precision SLA (the bar)
- A **stratified sample** of the published set (prose vs table-located; across orgs) is graded for
  **right-number AND right-label**. The run is **not declared shippable** until the sample meets the
  operator's SLA (a number — per-metric AND per-org; set in the plan). Automated publish ≠ trusted
  until the spot-review holds. This gate also fronts any future scale (Phase 5).
- **Quarantine triage:** bucket quarantine into recoverable-by-relabel, recoverable-by-vision,
  true-junk; report counts. Recoverable feeds the vision/0069-follow-up, not the publish set.

## 6. Technical Implementation (reference)
- Port `gate.py`/`pipeline.decide` to `lavandula/nlp/` (pure verifier + decision chain), reusing
  `marker_render` (0068) and `measurable_value_check`. A runner iterates a 0068 run's
  marker-bearing `llm_metrics`, re-renders per doc, runs the gate, writes `gate_decision`/reason
  under a `gate_run_id`, and emits a per-run report (publish/quarantine counts + reason histogram +
  mispair/unmarked/triage) to `extraction_runs.stats_json`. Idempotent per `(gate_run_id,
  content_sha256)`, atomic. Validate against the frozen CanCare/BGCSM fixtures + the
  `0068-markers-2026-06-17` run.

## 7. Acceptance Criteria (frozen — testable)
1. Gate module reproduces `gate.py`'s verdict on the frozen counterexample set (value spelled-out /
   abbreviated / rounded grounds; small-int stray-digit does NOT; rankings → `not_a_metric`).
2. On `0068-markers-2026-06-17` (1,066 markers), every metric gets a `gate_decision` + reason; the
   publish/quarantine split + reason histogram are reported. Counts are reproducible (deterministic).
3. `marker_resolved=false` rows → `quarantine/unmarked` (never published).
4. The `published_metrics` view returns only publish rows, slot-shaped; org-detail reads it.
5. **Human spot-review** of a stratified published sample meets the SLA (number set in plan);
   right-number-AND-right-label measured, not asserted.
6. Mispairs are quarantined, never auto-relabeled (no `gate_decision='publish'` row had its label
   changed by the gate).
7. De-dup never drops a metric whose value+label is spatially distinct within the doc.
8. CanCare/BGCSM fixtures: the publish set's metric *content* is unchanged vs the gate's research
   verdicts (no regression in the ported logic).

## 8. Security & Abuse Considerations
- **Untrusted source text** (already in `idmap`) — the gate reads parsed text only; no model output
  is trusted without idmap validation (inherited from 0068). The measurable-value LLM call wraps
  source text as untrusted data; its output is a boolean gate, never stored verbatim.
- **Quarantine is fail-safe** — any error/uncertainty defaults to quarantine, never publish
  (precision-over-recall). A doc whose re-render fails → quarantine, reported, not published.
- **No new writes to legacy rows** — 0069 writes only `gate_*` columns on the targeted run; the
  74,240 legacy rows and other runs are untouched.
- **Published view cannot leak quarantine** — the contract is a hard `gate_decision='publish'`
  filter, tested (AC4).
- **DoS** — re-render is bounded by 0068's `MAX_IDMAP_ITEMS`; the measurable check is |value|≤20 only.

## 9. Failure & Error Scenarios (fail-safe)
- **Re-render mismatch / stale `parse_version`** → re-resolve or quarantine `stale_coords`; never
  gate on stale text.
- **Measurable-value LLM unavailable** → small-ints quarantine `measure_unchecked` (do not publish
  an unverified small int).
- **Ambiguous de-dup** → keep both (don't merge).
- **Doc re-render error** → quarantine all its metrics, report; no partial publish.

## 10. Open Questions (plan-phase)
1. **Precision SLA number(s)** and the spot-review sample size/stratification (set in plan with the
   operator).
2. **Confidence score** — emit a `gate_confidence` (e.g. from grounding margin / co-location
   strength) to prioritize spot-review, or binary only.
3. **Mispair detection strength** — reinstate a tuned column-coherence guard (gate.py's removed
   `column_guard`, measured for false-flags) vs rely on subject-grounding + spot-review.
4. **`gate_decision` vs `verification_tier`** — keep both, or have the published view also require
   `verification_tier` not in the quarantine set.

## Consultation Log
(to be populated from spec-review + red-team-spec consults)

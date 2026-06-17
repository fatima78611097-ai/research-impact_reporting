# Spec 0069 — Precision Gates to Production (marker-grounded publish/quarantine)

**Status**: conceived (AI ceiling — human approves `specified`)
**Date**: 2026-06-17
**Depends on**: 0068 (per-metric markers + resolved coords, integrated)
**Blocks**: national scale (Phase 5), the published product surface
**Protocol**: SPIDER
**Review**: Gemini/Codex spec-review (COMMENT / REQUEST_CHANGES → addressed inline + §11); Gemini/Codex
red-team-spec (APPROVE / REQUEST_CHANGES → addressed §12, 0 unresolved CRITICAL)

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

**Decision precedence (resolves Codex #1): checks run in the fixed order above (1→7); the FIRST
failing check wins and is the single stored `gate_reason`.** Deterministic, single-valued, no ties.

**Pure oracle vs production policy (resolves Codex #9): two clearly separated layers.**
- **Ported verdict oracle** (steps 2–4): a pure reproduction of research `gate.verdict` +
  `measurable_value_check`, **regression-locked** to the research verdicts (so any change is caught).
- **Production-only policies** (steps 1, 5, 6 + de-dup + spot-review/triage): new logic that may be
  tuned, kept out of the oracle so its regressions isolate from the oracle's.

## 5. Requirements

### 5.1 Re-render for idmap (reuse 0068)
- The gate runner re-renders each doc with the **same deterministic `marker_render`** 0068 used, to
  obtain `idmap[value_ref].text` / co-location fields. No new render logic; recompute-on-demand.
- A doc whose re-render no longer matches the stored `parse_version` → **quarantine `stale_coords`,
  single-valued; 0069 NEVER re-resolves** (re-marking is upstream — a fresh 0068 run after re-parse;
  see §5.8). (Resolves Codex HIGH stale-coords contradiction.)

### 5.2 Port the gate (pure, deterministic, TDD)
- Port `gate.verdict` + helpers (`value_grounded`, `subject_grounded`, `co_located`,
  `candidate_numbers`) as a supported, pure module — reproduce the research verdicts on the frozen
  fixtures (the spec's counterexample behavior is the test oracle).
- **Float finiteness + bounds (Gemini HIGH):** `to_float` MUST reject non-finite values (`NaN`,
  `Infinity`) and values outside the `numeric`/JSON-safe schema range → quarantine
  `value_out_of_bounds` *before* grounding. No non-finite/overflow value reaches the DB or the view.

### 5.3 Small-int measurable + is-a-metric (the LLM-assisted layer)
- `measurable_value_check` (DeepSeek) runs only for |value|≤20 (where non-measurements hide).
- **Trust boundary + injection hardening (resolves Codex #7 CRITICAL, Gemini MEDIUM):** the
  measurable check uses a **hardened prompt template** — source text wrapped in explicit delimiters
  and labeled untrusted data, with an instruction-injection guard; a **strict output parser**
  (single canonical yes/no token only); **max input length**; explicit **refusal handling**. Any
  malformed / repeated / non-canonical / refused response → quarantine `measure_unchecked`.
  **Retry budget is bounded** (≤2 retries, capped backoff, hard total-time budget); exhaustion →
  quarantine `measure_unchecked`. Blast radius is contained to |value|≤20 small ints. Note in the
  threat model: a successful injection on a small int could force one false publish — bounded, and
  the human spot-review (§5.7) backstops it.
- Is-a-metric reject rules re-homed onto `value` + `label` + marker source text; each rule measured
  for false-quarantine before activation (precision-over-recall: bias to quarantine, but don't drop
  a real "9 vans" / "served 6 counties").

### 5.4 De-dup (prose-internal only — concrete heuristic, resolves Codex #5 / Gemini)
Within one document, two **publish-eligible** metrics are merged iff ALL hold:
1. equal numeric `value` (after `gate.to_float` normalization);
2. label agreement via `regroup.labels_agree` (distinctive-stem overlap, the same helper 0068 used);
3. **not spatially distinct** — their `value_ref` markers are co-located (`gate.co_located`: same
   cell, same row, or same page within bbox proximity). Different page or distinct cell → NOT merged.
On merge, **keep the occurrence with the lowest reading order** (page, then table/row/col; text
before table); the dropped one is recorded `gate_reason=duplicate`. When any condition is uncertain,
**keep both** (a false merge destroys a real metric). Prose-vs-infographic de-dup is out (vision).

### 5.5 Decision storage (additive migration, operator-run)
- `gate_decision text` (`publish`|`quarantine`), `gate_reason text`, `gate_run_id integer`,
  `gate_confidence numeric NULL`. Additive on `llm_metrics` (rides to 0066 clean schema). The
  0057 `verification_tier` (text-substring) is retained but **`gate_decision` is the authoritative
  publish signal** for marker-bearing rows.
- **`gate_confidence` is ADVISORY (resolves Codex #2):** it does **not** influence the binary
  publish/quarantine decision (the §4 checks alone do); it only **prioritizes the spot-review
  sample** (e.g. grounding margin / co-location strength). The decision is reproducible without it.
- **Index (Gemini):** the migration adds an index on `(gate_run_id, gate_decision)` so the
  published view / product queries are performant.

### 5.6 Published-data contract (exact — resolves Codex #3 / Gemini)
- A view `lava_vocab.published_metrics` with a **fixed slot column set** (no `metric_text`):
  `content_sha256, source_org_ein, metric_value, label, unit, geo_impact, value_ref, value_page,
  value_bbox, value_row, value_col, gate_run_id`. Filter: `gate_decision='publish'`.
- **Active-run only + run integrity (Gemini #1, Codex CRITICAL):** the view surfaces decisions from
  the **latest gate_run_id per source extraction-run**, never an aggregate of historical `publish`
  verdicts. `gate_run_id` is **server-assigned, monotonic, immutable**, bound to an immutable
  extraction-run identity + creation timestamp — NOT user-settable or backdatable. Active-run =
  greatest server-assigned `gate_run_id` for that extraction run (monotonic ⇒ no ties, no
  backdated-run selection). A poisoned/backdated run cannot become active; the selection is
  provenance-checked.
- Nullability: coord columns may be NULL only when a published row is text-located (no cell);
  `marker_resolved=true` is implied by publish (unmarked rows quarantine, §4.1).
- The org-detail product surface reads this view (fixes the current all-tiers/`metric_text` leak).

### 5.7 Human spot-review + precision SLA (the bar)
- An **OFFLINE evaluation (resolves Codex #4 / Gemini):** a stratified sample of the published set
  (prose vs table-located; across orgs) is graded for **right-number AND right-label**. It
  **measures** the run's precision; it does **NOT write per-metric corrections back** to
  `gate_decision` in 0069 (a correction/override UI is a separate concern). 0069 ships the
  **protocol + the measured number**; the SLA itself is a **named plan parameter**
  (`PUBLISH_PRECISION_SLA`, per-metric AND per-org) the operator sets. The "shippable" judgment =
  the measured sample precision ≥ the SLA — a documented, repeatable check, not a code branch.
- **Sample integrity (Codex HIGH):** the spot-review sample is **frozen at selection** (sampled row
  ids + a content hash recorded), graded with **reviewer identity + timestamp**, and the review
  outputs stored **immutably** (append-only) — so a sample can't be swapped, relabeled, or replayed
  to fake SLA compliance.
- **Quarantine triage:** bucket quarantine into recoverable-by-relabel, recoverable-by-vision,
  true-junk; report counts. Recoverable feeds the vision/0069-follow-up, not the publish set.

### 5.8 Stale-coords boundary (resolves Codex #6)
0069 **gates on what 0068 stored; it does NOT re-mark or re-resolve.** If a doc's re-rendered
`idmap` no longer matches the stored `parse_version` (a re-parse shifted IDs), the metric →
quarantine `stale_coords` (reported). Re-marking is **upstream** (a fresh 0068 run after re-parse),
out of 0069's scope. Clean boundary: 0069 never silently re-derives coordinates.

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
5. **Spot-review protocol runs** on a stratified published sample and produces a *measured*
   right-number-AND-right-label precision; the run is marked shippable iff that measure ≥
   `PUBLISH_PRECISION_SLA` (named plan param). The protocol + the measurement are the testable
   artifact; the threshold is config.
6. Mispairs are quarantined, never auto-relabeled (no `gate_decision='publish'` row had its label
   changed by the gate).
7. De-dup never drops a metric whose value+label is spatially distinct within the doc.
8. CanCare/BGCSM fixtures: the publish set's metric *content* is unchanged vs the gate's research
   verdicts (no regression in the ported logic).
9. **Idempotency:** re-running the gate for a `(gate_run_id, content_sha256)` is atomic
   delete-then-insert — no duplicate or partial decisions.
10. **View isolation:** with multi-run data, `published_metrics` returns only active-run publish
    rows — never a quarantine row nor a superseded `gate_run_id`.
11. **Determinism:** identical inputs → identical decisions AND identical reason histogram.
12. **Stale coords:** a `parse_version` mismatch yields `quarantine/stale_coords`, never a decision
    gated on stale text.

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
- **Governance & audit (Codex CRITICAL — single-operator-bounded):** this is a single-operator DB, so
  full RBAC is out of scope, but gate runs, `PUBLISH_PRECISION_SLA` values (version-controlled in
  the plan/git), spot-review results, and any "run promoted to shippable" action are recorded
  **immutably** (append-only: `extraction_runs.stats_json` + git) with operator + timestamp. SLA
  lowering / run promotion is an explicit, recorded operator action — never silent.
- **ReDoS / algorithmic complexity (Gemini MEDIUM):** all per-metric regex/string matching
  (`value_grounded`, `subject_grounded`, `labels_agree`) runs under a strict per-metric time budget
  and bounded input length (the `idmap` is already capped by `MAX_IDMAP_ITEMS`); linear-time
  patterns only, no catastrophic backtracking. A metric exceeding the budget → quarantine `gate_timeout`.
- **View least-privilege + privacy (Codex HIGH, Gemini LOW):** `published_metrics` is read by the
  product role (least privilege); raw `llm_metrics` stays app-role only. The current corpus is public
  IRS-990-derived reports, so location/bbox columns are not sensitive and need no redaction — flagged
  for re-evaluation if private collateral enters the corpus. The DeepSeek measurable-check transmits
  verbatim small-int source text to an external API (acceptable for public reports; revisit / run
  in-house before any private/PII collateral).
- **Float bounds** — non-finite / overflow values are rejected at `to_float` (§5.2), so no malformed
  numeric reaches the DB or the JSON view.

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

## 11. Spec-review resolutions (Gemini COMMENT + Codex REQUEST_CHANGES → all addressed)
- Decision precedence (first-failure-wins, single reason) → §4. Pure-oracle vs production-policy
  separation → §4. Measurable-check trust boundary (strict boolean, timeout, fail→quarantine) → §5.3.
  De-dup concrete heuristic + keep-first → §5.4. `gate_confidence` advisory + index → §5.5.
  Exact published view contract + active-run-only → §5.6. Spot-review = offline eval, SLA a named
  param, no write-back → §5.7. Stale-coords boundary (quarantine, no re-resolve) → §5.8.
  Idempotency/view-isolation/determinism/stale-coords tests → §7 AC9–12.

## 12. Red-team security resolutions (Gemini APPROVE / Codex REQUEST_CHANGES → all addressed; 0 unresolved CRITICAL)
- **Float NaN/Inf/overflow** (Gemini HIGH) → §5.2 (`to_float` rejects non-finite/overflow → `value_out_of_bounds`).
- **Measurable-check prompt injection** (Codex CRITICAL, Gemini MEDIUM) → §5.3 (hardened template/parser/max-len/refusal; malformed→quarantine; blast radius bounded + spot-review backstop).
- **Active-run integrity / run selection** (Codex CRITICAL) → §5.6 (server-assigned monotonic immutable `gate_run_id`; provenance-checked; no backdated-run selection).
- **Governance/audit for SLA + run promotion** (Codex CRITICAL) → §8 (immutable append-only audit; SLA/promotion are recorded operator actions; single-operator-bounded).
- **Stale-coords contradiction** (Codex HIGH) → §5.1 aligned with §5.8 (always quarantine, never re-resolve).
- **De-dup tie-break / spatially-distinct threshold** (Codex HIGH) → §5.4 (explicit `gate.co_located`; borderline → keep both).
- **Spot-review sample immutability** (Codex HIGH) → §5.7 (frozen+hashed sample, reviewer id+timestamp, append-only outputs).
- **View access/redaction** (Codex HIGH, Gemini LOW) → §8 (least-privilege view; public-report data not sensitive; revisit for private collateral).
- **ReDoS** (Gemini MEDIUM) → §8 (per-metric time budget + linear patterns → `gate_timeout`).
- **Retry budget** (Codex MEDIUM) → §5.3 (≤2 retries, capped backoff, total budget).
- **Secondary diagnostics** (Codex MEDIUM) → single authoritative `gate_reason` kept; advisory secondary diagnostics may ride `gate_confidence` (§5.5).
- **Partial marker metadata** (Codex MEDIUM) → covered by §4 (e.g. resolved `value_ref` but null `subject_ref` → `subject_not_grounded`).

## Consultation Log

### First Consultation (After Initial Draft)
**Date**: 2026-06-17
**Models Consulted**: Gemini (gemini-3-pro), Codex (GPT-5)
**Commands**:
```
consult --model gemini --type spec-review spec 0069
consult --model codex  --type spec-review spec 0069
```
**Key Feedback**: Gemini **COMMENT** (HIGH) — view multi-run state, index, de-dup keep-rule,
spot-review write-back. Codex **REQUEST_CHANGES** (HIGH) — decision precedence, `gate_confidence`
contract, exact view columns, spot-review testability, de-dup vagueness, stale-coords boundary,
measurable-check trust boundary, missing idempotency/view/determinism tests, oracle-vs-policy scope.
**All addressed** (§11 map).

### Red Team Security Review (MANDATORY)
**Date**: 2026-06-17
**Commands**:
```
consult --model gemini --type red-team-spec spec 0069
consult --model codex  --type red-team-spec spec 0069
```
Gemini **APPROVE** (0 CRITICAL, 1 HIGH: float bounds). Codex **REQUEST_CHANGES** (3 CRITICAL:
measurable-check injection, active-run integrity, governance/audit; 4 HIGH: stale-coords, de-dup
tie-break, sample immutability, view access). **All addressed in §12.**
**Verdict**: APPROVE — all findings resolved; **0 unresolved CRITICAL**.

# Spec 0068 — Coordinate Handoff: Per-Metric Docling-Location Markers

**Status**: conceived (AI ceiling — human approves `specified`)
**Date**: 2026-06-16
**Depends on**: 0066 (clean metric schema), parse coordinate population (0058/0064)
**Blocks**: 0069 (precision gates to production)
**Protocol**: SPIDER
**Review**: Gemini/Codex spec-review (APPROVE / REQUEST_CHANGES → addressed §11); Gemini/Codex
red-team-spec (both REQUEST_CHANGES → addressed §12, 0 unresolved CRITICAL)

---

## 1. Problem & Motivation

Production metric extraction (Spec 0051, `lavandula/nlp/llm_extract.py`) stores only a copied
text **snippet** per metric — `metric_text, metric_value, unit, geo_impact, source_snippet`.
There is **no record of where on the page the number came from**. Consequently:

- The number-aware grounding gate built in research (`comp-metric-regression/gate.py`) cannot
  run on production rows — it requires a per-metric **marker** resolving to a page location
  (`value_ref` / `subject_ref` → `{page, bbox, row, col}`).
- The only production accuracy check (Spec 0057) is a **text-substring** match that does not
  verify the number is present, and cannot detect **mispairing** (right number, wrong label).
- **Phase −1 calibration** (2026-06-16) showed the residual error concentrates in **flattened
  dotted-leader TABLES**, where a value and its label scramble in the text stream — exactly the
  case that needs **table-cell coordinates**, not just text spans, to verify.

Coordinates exist in the parse layer (`lava_parse.sections.source_locations`,
`lava_parse.tables.cell_locations`/`bbox`) but only ~1% of rows are populated, and the extractor
never cites them. **This spec is the linchpin**: make every extracted metric carry a verifiable
Docling-location marker resolved to page coordinates, so the gate (0069) and the product can
verify each number *at its place on the page*. Phase −1(a) validated the end-to-end chain
(`run_doc.py`) on real coord-bearing docs; this spec productionizes its **marker-emission half**.

## 2. Scope (what 0068 ships) / Non-Goals

**Ships:**
1. The **marker contract** (§4) — `value_ref`/`subject_ref` = Docling-location IDs resolving via
   a per-document `idmap` to `{page, bbox, row, col, table, text}`.
2. **Productionized tagged render** — a supported module (from `render.render_tagged`) reading
   `lava_parse` and emitting tagged text + `idmap` (deterministic IDs).
3. **Marker-citing extraction** — the extractor emits `value_ref`/`subject_ref` per metric
   (`comp-metric-prompt.v-loc1.txt`), `null` when not locatable, never invented.
4. **Storage** (§5.5) — marker refs + **server-resolved** coordinate snapshot + `parse_version`
   on the clean metric table (0066).
5. **Table cells first-class** — markers cover table cells (`⟨c#⟩`, row/col/bbox), not only text.
6. **Doc-level coordinate-eligibility** (§5.4) — extract only coordinate-bearing docs.

**Non-Goals (later specs):**
- Publish/quarantine **decision**, is-a-metric, mispair detect-and-quarantine, measurable-value
  pass, twin-repair → **0069**. 0068 does NOT judge whether a cited marker is *correct* (value
  verbatim there); it records the marker + resolved coords + `marker_resolved`. The
  value-verbatim-at-marker check is 0069.
- **`period`/timeframe slot** — deferred to a later slot spec (it has an unresolved grounding
  policy; keeping it out of 0068 keeps this contract complete). 0068's required output is
  value + label + unit + value_ref + subject_ref only.
- Vision recovery of image-only numbers; document qualification (0067); schema retirement (0066).

## 3. Background & Validation (Phase −1, 2026-06-16)
- Marker mechanism **already validated in research**: `render.render_tagged` tags each Docling
  text element `⟨t42⟩` and each table cell `⟨c17⟩` and builds an `idmap`
  `{kind, text, page, bbox, row, col, table, row_text}`; the loc1 prompt cites markers; `gate.py`
  resolves them. The 10-doc demo ran this end-to-end with no new build.
- **Calibration** (49 verified metrics): ~85–90% already correct; near-zero outright mispairs;
  the unconfirmable tail was flattened tables → cell-level markers essential.
- **Recall**: many featured numbers are image-only (no text) → those metrics legitimately get
  `value_ref=null` and route to the vision path — `null` markers are a first-class outcome, not a
  failure.

## 4. The Marker Contract (core)
A **marker** is a stable per-document Docling-location ID:
- `⟨t{n}⟩` — text element (from `sections.source_locations[i]`) → `{kind:text, text, page, bbox}`.
- `⟨c{n}⟩` — table cell (from `tables.cell_locations[i]`) → `{kind:cell, text, page, row, col,
  table, bbox, row_text}`.

The `idmap` is produced deterministically by the tagged render in a fixed traversal order
(sections by `section_index`, then tables by `table_index`, cells by `row` then `col`). IDs are
**document-local and render-deterministic** — same parse rows ⇒ same IDs.

Each metric stores the cited `value_ref`/`subject_ref` **and** their server-resolved fields
snapshotted at extraction time, plus `marker_resolved`. The snapshot decouples downstream from
the render and survives a later re-parse (detectable via stored `parse_version`).

**Decision (operator, 2026-06-16): the Docling-location-ID marker is the primary grounding
anchor.** The Spec 0057 character-offset grounding is retained as a **secondary/fallback** for
rows without resolvable markers (legacy, OCR-only); not removed in 0068 (deprecation = future).

## 5. Requirements

### 5.1 Tagged render (productionized)
- Supported module renders `(section_text, tagged_text, idmap)` from `lava_parse`, matching
  `render.render_tagged` semantics; deterministic IDs.
- **Truncation priority (Gemini):** the 60k-char tagged input is metric-dense in tables, so when
  the cap is exceeded, **retain table cells and drop low-value narrative first** (not "tables
  last"). Record `truncated=true` + what was dropped so unlocated tail metrics aren't mistaken
  for image-only.

### 5.2 Marker-citing extraction
- Per metric, emits: `metric_value`, `label`, `unit`, `value_ref`, `subject_ref`.
- Refs MUST be markers present in the tagged input, or `null`; the prompt forbids invented
  markers. A value that cannot be located gets `value_ref=null` (vision candidate).
- Reuse the validated loc1 selection contract (~5–15 headline metrics/doc).

### 5.3 Table cells first-class (hard requirement)
- Cell markers carry `row`, `col`, `table`, `bbox`, enabling 0069's same-cell/same-row
  co-location check (the mispair guard). Acceptance measures table-located metrics specifically.

### 5.4 Coordinate eligibility — single rule (resolves Codex #1)
Two distinct levels, one rule each — not contradictory:
- **Document level (eligibility):** a doc is eligible for 0068 extraction iff it has been
  re-parsed to the coordinate-bearing schema AND meets a populated-coordinates threshold
  (≥ T% of text elements carry a bbox AND every table cell carries row+col+bbox; T set in plan).
  **Ineligible docs are SKIPPED** — 0068 does **not** trigger a re-parse on the fly. An
  asynchronous batch re-parse job (parse track) populates coordinates first; it runs on the
  **qualified** set only (0067), never the full corpus, and its cost is surfaced before running.
- **Element level (fail-soft):** within an eligible doc, a rare individual element with a missing
  location resolves to a marker with `page=null,bbox=null`; a metric whose `value_ref` resolves
  only to such an element has `marker_resolved=false` (retained + flagged, never dropped).

### 5.5 Data-model additions (intent; exact DDL → plan)
On the clean metric table (0066): `value_ref text NULL`, `subject_ref text NULL`;
resolved `value_page int NULL`, `value_bbox jsonb NULL` (shape `{l,t,r,b,coord_origin}` matching
Docling), `value_row int NULL`, `value_col int NULL`, `value_table int NULL`; the four
`subject_*` equivalents (same shapes, NULL); `marker_resolved boolean NOT NULL`;
**`parse_version text NOT NULL`** (the parse output version the coords came from);
`run_tag text NOT NULL`. All resolved-coordinate columns are NULL when the corresponding ref is
null/unresolved. (Interim: additive migration on `lava_vocab.llm_metrics` if 0066 has not landed;
columns move with the table.)

### 5.6 Re-extraction / idempotency & legacy (resolves Codex #6)
- 0068 produces a **fresh extraction** over the eligible set under a new `run_tag`.
- **Idempotent per `(run_tag, content_sha256)`:** a rerun deletes that run_tag's rows for the doc
  and re-inserts (mirrors `llm_extract.py` delete-then-insert). No cross-run dedup.
- The legacy 56,443 Spec-0051 "verified" rows are **retained read-only** under their own run_tag,
  never mutated; they do not carry forward (no markers) and are kept only for comparison.

### 5.7 Canonical ref selection — ambiguous evidence (resolves Codex #4)
When a value appears in more than one element, `value_ref` is chosen deterministically:
1. an element whose text contains the value's numeric token **verbatim** (required); else `null`.
2. prefer a **cell** marker over a text marker (more precise location).
3. among cells, prefer the cell whose `row_text` also contains the metric's label terms.
4. tie-break by reading order (lowest page, then table/row/col).
`subject_ref` selects analogously on the **label** terms. If selection remains genuinely
ambiguous after these rules, set the ref `null` (flag, do not guess). (Mixed text+table evidence
for the same value: rule 2 makes the table cell canonical.)

### 5.8 Resource & integrity bounds (red-team)
- **Item ceiling (Gemini CRITICAL — "table bomb"):** the render enforces a hard maximum on
  idmap items (text elements + cells), `MAX_IDMAP_ITEMS` (set in plan). A doc exceeding it is
  **ineligible/skipped** with reason `idmap_too_large`. Character-count truncation alone does NOT
  bound resource use — a microscopic many-celled table is caught here before render/store.
- **ID uniqueness (Gemini MEDIUM):** idmap keys MUST be unique by construction; a collision (from
  malformed/overlapping parse indices) → fail-safe skip with reason `idmap_id_collision`, never a
  silent overwrite. Asserted in code + test.
- **Coordinate sanitization + coordinate-system contract (Gemini HIGH, Codex LOW):** every stored
  bbox is strictly numeric and finite, `coord_origin` ∈ the Docling-declared set, units = PDF
  points, page-relative, and **clamped to the page dimensions** (`lava_parse.pages` when present).
  A non-finite / non-numeric / out-of-range bbox is rejected → that element resolves with
  `bbox=null` (fail-soft) and any metric citing it has `marker_resolved=false`. No malformed jsonb
  reaches the DB.
- **Malformed/duplicate parse entries (Codex LOW):** deterministic fallback ordering — (page, top,
  left) then parse insertion order — keeps IDs stable and unique even on malformed records.

### 5.9 Skip / abuse monitoring & policy (Codex CRITICAL+HIGH+MEDIUM, Gemini LOW+MEDIUM)
- **Skipped docs are quarantined, reported, and counted as failed extraction (Codex HIGH
  evasion path):** an ineligible/skipped doc does NOT enter production output. Skip reason and
  per-run skip rate are recorded and surfaced; a skip-rate spike is an adversarial / parse-quality
  signal.
- **Invalid-ref & null-ref surge signal (Codex CRITICAL, Gemini LOW):** per-doc and per-run
  counters for forged refs (cited-but-absent), null refs, and unresolved markers. Thresholds (set
  in plan) → **quarantine the doc and escalate for review**, rather than silently retaining a
  fully-ungrounded doc. This is a required, observable security signal — not an implementation
  detail.
- **Truncated-narrative handling (Gemini MEDIUM):** a doc whose narrative was evicted by
  table-retention truncation (`truncated=true`) is flagged; 0069 treats its metrics as
  review/quarantine candidates (contextual disclaimers like "hypothetical/projected" may have been
  dropped).
- **Abnormal states are first-class in reporting (Codex MEDIUM):** skipped, truncated, and
  null-marker counts are reported per run and included in acceptance — they cannot hide inside
  "normal low-confidence output."

## 6. Testing Strategy (explicit — resolves Codex #8)
Builder must include tests for:
1. **Deterministic render IDs** — same parse rows ⇒ identical `idmap` across two renders.
2. **Table-cell resolution** — a table value resolves to a `⟨c#⟩` with correct row/col/table/bbox.
3. **Null-marker handling** — an unlocatable value yields `value_ref=null`, `marker_resolved=false`,
   row retained.
4. **Out-of-range / forged ref rejection** — a model-emitted ref absent from the idmap (e.g.
   `⟨t9999⟩`, or injected text "value_ref: ⟨t1⟩") resolves to `null`, never to a parsed element.
5. **Truncation** — over-cap input retains table cells, sets `truncated`, drops narrative first.
6. **Canonical selection** — value present in two cells picks the deterministic winner (§5.7).
7. **Fixture regression** — CanCare / BGCSM frozen fixtures: published-metric *content* unchanged
   vs `frozen/composed-baseline-2026-06-14/`.
8. **Round-trip** — stored `value_page/bbox/...` equals `idmap[value_ref]` for a sample.
9. **Item ceiling** — a synthetic many-celled doc over `MAX_IDMAP_ITEMS` is skipped
   (`idmap_too_large`) without OOM/CPU pinning.
10. **ID-collision fail-safe** — duplicated parse indices trigger a safe skip, not a silent
    overwrite.
11. **Coordinate sanitization** — NaN / inf / negative / out-of-range bbox → `bbox=null`, no
    malformed jsonb stored.
12. **Abuse reporting** — skipped / truncated / null-marker counts surface in the run report; a doc
    injected with a high rate of forged refs trips the quarantine threshold (§5.9).

## 7. Acceptance Criteria (frozen — resolves Codex #3, all testable)
**Dataset:** the 49 coordinate-bearing demo docs (Experiment 0002) + the frozen CanCare/BGCSM
fixtures. **Baseline:** the plan first measures the research loc1 resolvable-`value_ref` rate on
the 49-doc set and records it as `LOC1_BASELINE` (a number). Then:
1. Resolvable-`value_ref` rate on the 49-doc set ≥ `LOC1_BASELINE − 5` percentage points
   (measurement procedure: run 0068 extraction, count metrics with a non-null `value_ref` present
   in the doc's idmap ÷ total selected metrics).
2. 100% of metrics whose value is located in a table resolve to a **cell** marker with row+col+bbox.
3. Round-trip (test §6.8) holds for a 50-metric sample.
4. Eligible docs satisfy the §5.4 threshold (both section and table-cell coords populated),
   verified by count before extraction.
5. `value_ref=null` (image-only) metrics are stored and flagged, not dropped.
6. ~~CanCare/BGCSM: published-metric content unchanged vs frozen baseline.~~ **DEFERRED to a
   tracked follow-up** (architect PR #53 integration review, 2026-06-17). Rationale: 0068's
   one-pass loc1 path produces a *different* selected-metric set by design than the frozen
   **composed-sentence** baseline (`frozen/composed-baseline-2026-06-14/`), so a literal
   "content unchanged vs that baseline" diff is not well-posed for this path. The fixture
   regression is re-scoped to compare 0068↔0069 marker output once 0069's published surface
   exists. Tracked in `locard/operations/0068-validation.md` → Follow-ups.
7. Zero invented markers (every stored non-null ref exists in that doc's idmap) — enforced in code.

## 8. Security & Abuse Considerations
- **Prompt injection / marker forgery (resolves Codex #7):** document text is untrusted; a report
  could embed `value_ref: ⟨t1⟩`. The model's ref claims are **advisory** — every ref is validated
  server-side against the rendered `idmap`. **Rejection path:** an invalid/out-of-range ref is set
  to `null` with `marker_resolved=false`; the **metric is RETAINED and flagged**, never dropped
  and never re-scored by string-parsing the ref. A metric with a null `value_ref` is a vision/0069
  candidate. Refs are resolved only by idmap lookup, never by parsing `t9999`.
- **Resource use** — render+extraction bounded (60k cap, per-doc); re-parse cost gated by the
  qualified-set count (0067), surfaced before running.
- **PII** — markers/coords add no new PII beyond what parse stores; metric-text path unchanged;
  stories/PII out of scope (Phase 3).
- **Stale coordinates** — `parse_version` stored per metric; a re-parse that shifts IDs is
  detectable, triggering re-resolution rather than silent stale service.
- **Parse-artifact trust boundary (Codex HIGH):** coordinate snapshots are accepted only from a
  trusted, provenance-tracked parse run — `parse_version` is **immutable** and bound to the parse
  track's recorded run (`lava_parse.parse_runs` provenance). A snapshot whose `parse_version` is
  unknown/unverifiable is not trusted (re-resolve from a known-good parse). Artifact signing is a
  future hardening; the minimum bar is immutability + `parse_runs` provenance binding.
- **Untrusted-text escaping in logs/UI (Codex MEDIUM):** source-derived text — including
  marker-like strings such as `value_ref: ⟨t1⟩` — is untrusted data and MUST be escaped/sanitized
  before being written to logs or rendered in the review UI (prevents stored prompt-injection / XSS
  in the viewer).
- **Resource exhaustion** — bounded by the §5.8 item ceiling (not char-truncation alone).

## 9. Failure & Error Scenarios (fail-safe)
- **Ineligible doc (no/low coords)** → SKIP (do not emit marker-less metrics); await async re-parse.
- **Ref cited but not in idmap** → `value_ref=null`, `marker_resolved=false`; retain+flag.
- **Tagged input exceeds cap** → retain tables, drop narrative first, set `truncated`.
- **IDs shifted after re-parse** → detected via stored `parse_version`; re-resolve, never serve
  stale coords silently.
- **Both refs null** → metric retained, `marker_resolved=false`, vision/0069 candidate.

## 10. Open Questions (genuine plan-phase decisions)
1. **Idmap persistence — DECISION: recompute on demand** (deterministic), do **not** persist;
   coords are snapshotted on the row, so the viewer recomputes only if it needs the exact tagged
   text (Gemini concurred). Revisit only if recompute proves hot.
2. **Char-offset grounding (0057) deprecation timeline** — keep as fallback for how long.
3. **One pass vs two** — loc1 single marker-citing prompt vs the demo's extract-then-ground
   two-pass; decide on measured accuracy/cost in the plan (loc1 likely avoids the second LLM call).
4. **Coordinate threshold `T`** (§5.4) and `LOC1_BASELINE` (§7) — both measured and set in the plan.

## 11. Consult resolutions (this revision)
- **Codex REQUEST_CHANGES** — all 8 addressed: #1 eligibility single-rule (§5.4); #2 storage
  shape/nullability + `parse_version` (§5.5); #3 testable acceptance w/ measured baseline (§7);
  #4 canonical ref selection (§5.7); #5 `period` removed from scope (§2); #6 idempotency/legacy
  (§5.6); #7 rejection-path retain+flag (§8); #8 explicit tests (§6).
- **Gemini APPROVE** considerations — truncation prioritizes tables (§5.1/§9); re-parse is async
  skip-not-trigger (§5.4); idmap recompute-not-persist (§10.1).

## 12. Red-team security resolutions (Gemini + Codex red-team-spec)
Both verdicts REQUEST_CHANGES; all findings addressed, no unresolved CRITICAL:
- **Gemini CRITICAL** table-bomb DoS → §5.8 item ceiling (`MAX_IDMAP_ITEMS`, skip `idmap_too_large`).
- **Gemini HIGH** unvalidated bbox / jsonb injection → §5.8 coordinate sanitization + system contract.
- **Gemini MEDIUM** truncation context-eviction → §5.9 truncated-narrative flag → 0069 review.
- **Gemini MEDIUM** deterministic ID collisions → §5.8 uniqueness assert + fail-safe + test §6.10.
- **Gemini LOW** forged-marker feedback loop → §5.9 invalid-ref surge signal.
- **Codex CRITICAL** retained-but-unmonitored invalid refs → §5.9 surge thresholds → quarantine/escalate.
- **Codex HIGH** skipped-doc evasion path → §5.9 skip = quarantine + reported + counted-as-failed.
- **Codex HIGH** parse_version trust boundary → §8 immutable + `parse_runs` provenance binding.
- **Codex MEDIUM** per-run counters / abnormal-state reporting → §5.9 + acceptance §7.
- **Codex MEDIUM** log/UI sanitization of untrusted text → §8 escaping rule.
- **Codex LOW** bbox coordinate-system contract → §5.8; duplicate tie-break → §5.8.

## Plan-phase hardening checklist
- Set the measured numbers the spec defers to the plan: `LOC1_BASELINE` (§7), coordinate
  threshold `T` (§5.4), `MAX_IDMAP_ITEMS` (§5.8), invalid-ref/null-ref/skip-rate quarantine
  thresholds (§5.9).
- One-pass (loc1) vs two-pass extract+ground decision on measured accuracy/cost (§10.3).
- Migration ordering vs 0066 clean schema; interim-on-`llm_metrics` fallback (§5.5).

## Consultation Log

### First Consultation (After Initial Draft)
**Date**: 2026-06-16
**Models Consulted**: Gemini (gemini-3-pro), Codex (GPT-5)
**Commands**:
```
consult --model gemini --type spec-review spec 0068
consult --model codex  --type spec-review spec 0068
```
**Key Feedback**: Gemini **APPROVE** (HIGH). Codex **REQUEST_CHANGES** (HIGH) — 8 gaps: eligibility
rule contradiction, storage shape + `parse_version` undefined, untestable acceptance, ambiguous
ref selection, `period` both required and open, backfill/dedup boundaries, invalid-ref rejection
path, no explicit tests. **All addressed in §11.**

### Red Team Security Review (MANDATORY)
**Date**: 2026-06-16
**Commands**:
```
consult --model gemini --type red-team-spec spec 0068
consult --model codex  --type red-team-spec spec 0068
```
Both **REQUEST_CHANGES**. Findings: table-bomb DoS (Gemini CRITICAL), unvalidated bbox/jsonb
injection (HIGH), skip-path evasion (Codex HIGH), invalid-ref surge with no quarantine (Codex
CRITICAL), `parse_version` trust boundary (HIGH), truncation context-eviction, deterministic ID
collisions, log/UI sanitization, coordinate-system contract. **All addressed in §12.**
**Verdict**: APPROVE — all findings resolved; **0 unresolved CRITICAL** (see §12 resolution map).

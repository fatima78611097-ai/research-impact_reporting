# Short-Metric Pipeline — Production Implementation Plan

Every process required to take the current slot/short-metric pipeline from a dev corpus to running
in production at scale. Ordered as a document flows through, plus the cross-cutting work.

Status key: **READY** (built + validated, transfers as-is) · **WIRE** (built/validated but not
connected to the production decision) · **BUILD** (new work) · **TUNE** (exists, needs revision).

---

## A. Runtime pipeline — what executes per document, in order

| # | Stage | What it does (plain) | Status | Work needed |
|---|---|---|---|---|
| 0a | Material-type filter | Keep annual/impact reports; drop tax forms & financial-only docs | TUNE | Old classifier is low-quality; needs a better in/out gate |
| 0b | Low-value doc gate | Skip reports with no real numbers / all narrative-photo | READY | Port as-is |
| 0c | Attribution gate | Require sha + archived PDF + EIN + NTEE before processing | BUILD | Provenance completeness checks |
| 1 | Parse (Docling config B) | PDF → machine text + per-element coordinates (bbox/page) | READY | Port; locked config |
| 1b | Page geometry capture | Per-page dimensions + landscape/spread flag | BUILD | Needed so source-excerpt boxes land correctly at scale (REQ-017) |
| 2 | Marker tagging | Tag every text chunk / table cell so we can cite the exact source | WIRE | Single-namespace `m##` markers (prevents prefix-swap, REQ-020) |
| 3 | Geometry re-grouping | Pair each number with its correct caption from coordinates | READY | Port `regroup.py` |
| 3b | Born-correct input | Feed re-grouped input to extraction so pairing is right from the start | WIRE | Validated 20/20 in experiment; wire into the extraction call |
| 4 | **Slot extraction (DeepSeek)** | Read the page, pull each metric as slots: value, label, unit, period, source markers | **TUNE** | **Revise the prompt to emit clean slots + the context-stat selection rule; re-validate vs fixture** |
| 5 | Render short form (code) | Build "4,425 units served" deterministically from value+label+unit | BUILD | Productionize the spike renderer (%, $, acronym handling) |
| 6a | Grounding gate | Confirm the value is verbatim at the cited source | READY | Port |
| 6b | Simple confirm | Confirm value AND label words are in the source page | READY | Port |
| 6c | is-a-metric (cheap LLM) | Reject forecasts, tenure, factoids, context/market stats | READY | Port (aggressive setting) |
| 6d | Mispairing: detect→fix→verify | Relabel with geometry caption, vision-confirm, publish only verified | WIRE | Connect geometry relabel + vision verify into the publish decision |
| 7 | Validation pass (cheap DeepSeek) | Fresh semantic re-read: does value belong to label, org's own work, plausible? | BUILD | One decomposed DeepSeek call on survivors → confidence input |
| 8 | Vision verify (Qwen on g6) | Page-image adjudication for the hard residual + mispairing recovery | WIRE | Validated ~90% / ~$1·600 docs; wire the two-stage trigger (REQ-022) |

## B. Output — the metric card

| Field | Source | Status |
|---|---|---|
| Metric (label) | slot label | READY |
| Value | grounded value | READY |
| Period (FY 2022–23) | timeframe slot | BUILD (the one missing extraction field) |
| Source (report name/org) | doc identity | READY |
| Evidence (source excerpt) | boxed page image, on-demand + cached | WIRE (needs page-geometry, 1b) |
| Status (organization-reported) | constant for now | READY |
| Confidence (High/Med/Low) | gates + validation pass | BUILD (derivation rule) |
| Captured from (table/990/etc.) | marker kind + material_type | READY |
| Last reviewed (date) | timestamp | READY |

## C. Confidence & validation (how we earn the confidence label)

- **Production confidence** = cheap deterministic gates + the single DeepSeek validation pass. Runs on every metric.
- **Calibration (dev only)** = multi-pass dual-Opus sweep (vision + text) on a sample, proving "all gates green" ≈ "high accuracy." Not run per-metric in production.
- **Source-excerpt service** = render page + draw value box (red/value reliable) on demand, cache result.

## D. Cross-cutting infrastructure & ops

| Item | What it is | Status |
|---|---|---|
| Canonical metric data model | identity + slots + provenance + confidence + tags | TUNE (schema exists; add slot fields) |
| Production DB tables / migrations | where published metrics live | BUILD |
| Acceptance test suite | the GREEN-to-ship contract (REQ-023) | WIRE (port `test_suite.py`) |
| Vision ground-truth fixture | frozen answer key for regression | READY |
| Scale orchestration | multi-host parse + g6 for vision | READY (infra exists) |
| Per-cohort drift monitoring | new NTEE → small audit run before trusting | BUILD (operating model) |
| Decision attribution | `decided_by` per metric | READY |
| Snapshot / rollback | before-apply snapshots | READY (process) |

## E. Rollout sequence

1. **Revise the extraction prompt** to emit clean slots (value, label, unit, period, source markers) + context-stat selection; re-validate against the fixture.
2. **Build the deterministic renderer** + confidence derivation + DeepSeek validation pass.
3. **Wire the recovery/verify stages** (born-correct input, mispairing relabel+verify, Qwen vision).
4. **Port the gates + suite** behind the acceptance contract; each stage must hold its dev numbers.
5. **End-to-end run on fresh docs** (a new NTEE cohort audit) → turns the estimate into a certified number.
6. **Cutover** when the suite is GREEN on production output.

## F. The honest summary

- **Transfers as-is (READY):** parse, geometry-identify, grounding, low-value, is-a-metric, vision fixture, infra.
- **The real new build is small and specific:** the slot-extraction prompt revision, the deterministic renderer, the confidence/validation pass, and wiring the recovery+verify stages that were validated but never connected.
- **One genuinely new extraction field:** Period/timeframe.
- The pivot does **not** throw away the validated work — most of it transfers; the slot model just makes the assembly (render + card + source excerpt) clean and the drift gates unnecessary.

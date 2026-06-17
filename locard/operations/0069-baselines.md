# 0069 Phase-0 Baselines — frozen decisions + measurements

**Status**: AWAITING OPERATOR RUN (skeleton committed; numbers filled from `0069_measure.py`)
**Spec**: locard/specs/0069-precision-gates.md · **Plan**: locard/plans/0069-precision-gates.md
**Source run under test**: `0068-markers-2026-06-17` (1,066 markers, 97.7% resolved)

> Builder note: the code (gate oracle + policies + runner + view + spot-review) is built
> and unit/integration-tested against scratch Postgres. The numbers below require the live
> RDS + DeepSeek and are an **operator step** (no direct RDS access from the builder). Run
> `python3 locard/operations/0069_measure.py 0068-markers-2026-06-17` (read-only dry run)
> and paste its JSON, then confirm the decisions.

## 1. PUBLISH_PRECISION_SLA (spec §10.1, plan §dec.1)
Operator bar: "95% is not enough; fewer-but-accurate." Proposed, **confirm before shipping**:

- **per-metric:** ≥ **98%** right-number-AND-right-label on the published spot-sample.
- **per-org:** ≤ **1** wrong published metric per org.

Encoded as `lavandula/nlp/spot_review.PUBLISH_PRECISION_SLA` (version-controlled; lowering
it is an explicit recorded operator action per spec §8). **Confirmed value:** _<fill>_

## 2. Spot-review sample (spec §10.1)
Stratified: prose vs table-located × across orgs (`spot_review.select_sample`,
lowest-confidence-first within a stratum, frozen + content-hashed).

- **proposed size:** ~150–200 published metrics (tight CI at the SLA). **Confirmed:** _<fill>_
- manifest committed at `locard/operations/0069-spotreview/<gate_run_id>-manifest.json`.

## 3. De-dup rate (spec §5.4)
From `0069_measure.py` → `dedup_merged`. **Measured:** _<fill>_ of _<total>_ publish-eligible.

## 4. Column-coherence (mispair) DETECT decision (plan §dec.4 / §0)
From `0069_measure.py` → `mispair_flagged` (count of rows the column DETECT would
quarantine). Hand-check a sample of those flags for the **false-flag rate**.

- **mispair_flagged count:** _<fill>_
- **hand-checked sample size:** _<fill>_ · **false-flags in sample:** _<fill>_
- **measured false-flag rate:** _<fill>_%
- **DECISION (rule: ship iff ≤ 20%):** ☐ ship column-detect (`mispair_detect=True`)
  ☐ defer to 0070 (`mispair_detect=False`), rely on subject-grounding + spot-review.

## 5. Dataset hash
Record the source-run row count + a content hash of the gated id set for reproducibility:
**rows:** _<fill>_ · **id-set sha256:** _<fill>_

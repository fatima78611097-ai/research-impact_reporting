# Spec 0048: Recovery Readiness — Repository Stabilization & Data-Quality Bar

**Status**: Conceived
**Priority**: High
**Protocol**: MAINTAIN (stabilization) + standards authoring (data-quality bar)
**Dependencies**: 0047 (cross-origin recovery — its hot-patches are part of the divergence to reconcile)
**Estimated effort**: Medium — the git/code reconciliation is small but high-stakes; the QA standard is a focused authoring task requiring one sponsor decision

## Problem Statement

The corpus-integrity macro-plan (`locard/operations/macro-plan-corpus-integrity-rag-readiness.md`)
defines a 7-phase program to turn the ~186K-document pile into a trustworthy,
attributed corpus. Its **First Micro-Plan Gate** (macro-plan §"First Micro-Plan
Gate") is an explicit blocker: *no production recovery or extraction may run
from a dirty or ambiguous git state, and no bulk ingestion may run without a
defined attribution-QA bar.*

Both halves of that gate are currently unmet:

### Half 1 — The repository is in an indefensible state

Verified state (2026-05-18):

- **Git history has diverged.** Local `master` is **4 commits ahead** of
  `origin/master`; `origin/master` is **2 commits ahead** of local `master`.
  Merge-base is `3cd36c4` (PR #39 merge).
  - The 4 local-only commits are direct-to-master hot-patches, never PR'd,
    never pushed:
    - `0eb46c1` Add threading + CDN throttle override to recovery_pass1
    - `c85da57` Fix recovery_pass1: correct schema references
    - `d7567a3` Fix recovery pass queries: ns.website → ns.website_url
    - `e2183a5` [Spec 0047] Update status to implementing, add plan path
  - The 2 `origin/master`-only commits are the clean PR path the current
    branch sits on: `7c1e7d4` (PR #40 merge), `6b4c0f6` (reconcile_s3 fix).
- **The same recovery-script content exists three ways at once**: committed in
  the 4 local-master hot-patches, staged in the working tree on the current
  branch, and partially re-edited uncommitted. This triple-presence makes data
  lineage impossible to defend ("which code produced this row?").
- **An unreviewed security bypass is uncommitted and could ship as a default.**
  `recovery_pass1.py` (worktree) sets `validate_structure=False` and
  `fetch_pdf.MISMATCH_DISABLED = True`; `fetch_pdf.py` introduces the
  `MISMATCH_DISABLED` flag. This disables Spec 0047's Content-Type / structure
  validation and per-domain mismatch throttling.
- **Legitimate security hardening is intermixed with the bypass**, unstaged and
  untriaged: `settings.py` `+CSRF_COOKIE_SECURE = True`; `base.html` logout
  converted GET→POST with `{% csrf_token %}`. These must be *kept*, not swept
  away with the bypass.
- **An untriaged behavioral change** sits in `views.py` (`_v3_state_grid`
  removes the `cr.run_id = (SELECT MAX(...))` join condition) with no recorded
  rationale.
- **Generated artifacts and real untracked work are commingled**: ~22
  `.crawler.*.lock` files, `reports.db`, `staticfiles/`, `downloads/`,
  `screenshots/`, root scratch JSON — alongside files that are plausibly real
  work (`sample_pdfs/migration_004_*.sql`, `migration_005_*.sql`,
  `locard/reviews/0040-audit-findings.md`, the macro-plan itself,
  `lavandula/migrations/classifier_refactor/`, `locard/spikes/001-data/`).

Running any production recovery from this state would make every recovered
row's provenance unprovable.

### Half 2 — "Trustworthy" is undefined

The macro-plan cites "sampled QA" as a gate in three places (§Workstreams 2, 3;
§Quality Gates) but never defines the sample design, the acceptance threshold,
the failure action, or how `attribution_confidence` is computed. Without a
written, falsifiable bar, a recovery run can complete and *then* discover that
"trustworthy" was never specified — forcing a full redo. The QA bar must exist
**before** the first production recovery, not after.

## Goals

1. Produce a **single, reviewable, defensible git baseline**: the current
   branch, local `master`, and `origin/master` reconciled; an explicit human
   decision recorded for the 4 hot-patch commits; the security bypass discarded;
   the CSRF hardening preserved; generated noise removed or ignored.
2. Produce a **written Data-Quality & Attribution-Confidence Standard** that is
   falsifiable and binding on all downstream recovery/ingestion specs.
3. Leave the repo in a state where the macro-plan's Phase 2 (orphan
   reconciliation) can begin from known code against a defined quality bar.

## Non-Goals

- Running any production recovery, reconciliation, or recrawl (later phases).
- Building the attribution-layer schema/table (separate downstream project; this
  spec defines the *standard* it must meet, not the DDL).
- Re-running or fixing document classification (tracked separately).
- Docling / extraction work.
- Any change to product behavior. This is a stabilization + standards milestone.

## Technical Implementation

### Part 1: Reconcile git history (human-gated)

The 4 local-master hot-patches contain real, wanted work (the
`website → website_url` schema fix, status mapping, recovery_pass1 threading)
plus one housekeeping commit. They must not be silently discarded, nor silently
kept.

- **Re-home the substantive work** onto a reviewed branch cut from
  `origin/master`, as a normal PR (so the recovery scripts land on a known,
  reviewed commit — macro-plan §Workstream 1 "Recovery scripts used for
  production writes are on a known commit").
- **The `[Spec 0047] Update status…` commit** is housekeeping; fold its intent
  into the projectlist reconciliation rather than carrying it as a code commit.
- **Reconcile local `master` to `origin/master`** only after the work is
  re-homed. Resetting/fast-forwarding local `master` is **destructive and
  requires explicit human sign-off** (it discards the 4 local commits' SHAs;
  acceptable only once their content is preserved in the PR).
- The staged working-tree copies of the same content become redundant once the
  PR exists and must be dropped, not double-committed.

**Decision required from sponsor (gating):** approve the re-home-then-reset
strategy, or specify an alternative disposition for the 4 commits.

**Fallback if the sponsor rejects the local `master` reset (Option B):** do
*not* reset. Instead, preserve the 4 commits permanently on
`backup/master-pre-0048`, treat `origin/master` as the sole canonical line, and
stop using local `master` as a working ref (all future work branches from
`origin/master`). This still satisfies "recovery scripts on a known commit"
(the re-homed PR) and leaves no ambiguous *active* divergence, at the cost of a
stale local `master` ref that is explicitly documented as abandoned. Option B
is non-destructive and requires no further sign-off.

### Part 2: Classify and resolve the working tree

The table below is the **binding classification ruleset**. Applying it produces
a required, auditable artifact: `locard/maintain/0048-inventory.md`, an
exhaustive per-path table with **exactly one row per logical path** (a path
appearing in multiple git states — tracked/staged/untracked — is normalized to
one row with one final disposition).

**Frozen-snapshot protocol:** the artifact is built against a captured snapshot
— record the commit SHA, the verbatim `git status --porcelain`, and
`git ls-files --others --exclude-standard` output at a fixed timestamp at the
top of the artifact. Classification and deletion act against that frozen list,
not the live tree. If the working tree changes before cleanup completes, the
snapshot is re-captured and the artifact reconciled. No path may be deleted
until its row exists, its `defer` (if any) is resolved, and the snapshot is
current. The artifact — not this spec table — is the completeness gate.

**Decision-record fields:** every `defer` row and every sponsor decision
records: the decision, the deciding individual/role, the date, and a link to
the supporting rationale/PR/thread. Mechanical (`keep`/`discard`/`ignore`) rows
need no decider but still appear in the artifact.

**Decision ownership:** `keep`/`discard`/`ignore` rows follow the ruleset
mechanically (builder executes). Every `defer` row, and the `views.py` row
specifically, requires an explicit decision recorded by the **project owner**
(architect may recommend; only the human ratifies). For `views.py`: the
required evidence is a written rationale (a committed rationale file or the PR
description — not "documented somewhere") for removing the
`cr.run_id = (SELECT MAX(...))` join condition, which must explicitly assess
whether dropping the `MAX` condition widens the returned result set (data
exposure) or increases query load (DoS), even though the change is not
security-motivated. **If unresolved by the time the milestone would otherwise
close, the default is REVERT** — an unexplained behavioral change must never
ride through a stabilization pass.

Binding classification ruleset:

| Path | Class | Action |
|------|-------|--------|
| `settings.py` (+`CSRF_COOKIE_SECURE`) | Security hardening | **Keep** — re-home with recovery PR or its own small PR |
| `base.html` (logout GET→POST + csrf) | Security hardening | **Keep** |
| `fetch_pdf.py` (`MISMATCH_DISABLED` flag) | Latent bypass | **Default action: remove the flag entirely.** Retain ONLY if Spec 0047 strictly requires it for a legitimate path; if retained: must default `False`, carry an in-code `# SECURITY:` warning of the open-redirect/Content-Type impact, and be covered by a negative test asserting no production code path sets it `True`. Setting it `True` outside dev is review-blocking. |
| `recovery_pass1.py` worktree delta (`validate_structure=False`, `MISMATCH_DISABLED=True`) | **Security bypass** | **Discard** — must not ship |
| `views.py` `_v3_state_grid` subquery removal | Untriaged behavioral | **Defer** — record rationale or revert; do not bundle blindly |
| `recovery_pass1/2.py` staged schema fix | Real work | Land via the Part 1 PR, not as loose staged diff |
| `.crawler.*.lock`, `reports.db`, `staticfiles/`, `downloads/`, `screenshots/`, root `*_website.json`, `output.json` | Generated noise | Remove + add targeted `.gitignore` rules |
| `sample_pdfs/migration_004_*.sql`, `migration_005_*.sql` | Possible real work | **Defer** — human decision before delete |
| `locard/reviews/0040-audit-findings.md`, macro-plan, `lavandula/migrations/classifier_refactor/`, `locard/spikes/001-data/`, `cohort_junk_rate.sql` | Possible real work | **Defer** — human decision before delete |
| `GEMINI.md` | Possible canonical docs | **Defer** — may hold security/process conventions; before any delete/relocate, verify no unique canonical content (security workflow, dev conventions) and migrate it to a permanent location first |
| `locard/project-handoff*.{md,log,tsv}`, `experiments/`, `lavandula/review_uploads/` | Scratch/handoff | Delete or relocate per human decision (do not delete logs before confirming superseded) |
| `locard/plans/0048-repository-cleanup-for-approval.md` | Superseded draft | Delete — replaced by this spec/plan |

### Part 3: Author the Data-Quality & Attribution-Confidence Standard

Deliverable: `locard/resources/data-quality-standard.md`, binding on all
downstream recovery/ingestion specs. It must define:

1. **Attribution-confidence rubric** — explicit tiers with computable criteria,
   e.g.:
   - `high` — document fetched from a URL on the org's own resolved eTLD+1, EIN
     from the crawl path.
   - `medium` — EIN from S3 object metadata only; source URL present and
     plausible; no contradicting evidence.
   - `low` — multi-hop / cross-origin redirect, or EIN inferred indirectly.
   - `reject` — no defensible EIN→document evidence path.
   Each tier states exactly which fields/evidence produce it.
2. **Sampling design** — stratified by `state × NTEE-major × recovery-source`;
   minimum sample `n` per stratum; the statistic measured (EIN-misattribution
   rate: sampled documents whose content/source contradicts the attributed EIN).
3. **Acceptance threshold** — the maximum tolerable misattribution rate per
   stratum. The standard ships with a **strawman default the sponsor accepts or
   overrides**, so the document is falsifiable even before sign-off:
   - `high`/`medium` strata: ≤ **1%** sampled EIN-misattribution rate
     (deliberately fail-closed; the sponsor may loosen with justification).
   - `low` tier: excluded from downstream production use by default (not
     gated by a rate — its presence alone bars production ingestion).
   Until the sponsor ratifies or overrides, the standard is marked
   **PROVISIONAL**: the milestone may still close, but no production recovery
   write may proceed against a provisional threshold (the gate moves, it does
   not disappear).
4. **Failure action** — **stratum-level quarantine**, not all-or-nothing: a
   failing stratum is held out of downstream use and re-worked; passing strata
   proceed.
5. **Extraction-QA gate (symmetry requirement)** — a stub requirement that text
   extraction quality must be gated before vocabulary mining, with the detailed
   bar deferred to the Docling project but explicitly owned here so it is not
   forgotten.

**Field provenance (source of truth):** the standard must state, per field,
whether it is existing, derived, or downstream-schema:
- `NTEE-major` — **existing**, derived from `nonprofits_seed` NTEE code (seed
  layer is intact per the macro-plan).
- `recovery-source` — **derived** at recovery time from the tool/provenance
  path (e.g., `pass1`, `pass2`, `s3-orphan`); recorded by the recovery run.
- `attribution_confidence` — **new derived** field whose *computation* this
  standard defines; its *persistence* is downstream attribution-schema work
  (Spec 0048 non-goal). Builders treat it as computed-at-evaluation, not a
  column to read, until the schema project lands.

This standard is a *contract*, not code. Its canonical home is
`locard/resources/data-quality-standard.md` (discoverable, permanent — not a
scratch location). Later recovery/ingestion specs MUST link it; spec/plan
review explicitly checks that any downstream spec touching recovery or
ingestion references this standard and does not ingest below-threshold data or
omit the confidence rubric.

## Out of Scope (considered during red-team, deferred)

- **Process policy to prevent recurrence** (mandating PR-only, prohibiting
  direct-to-master). Valid, but a governance change beyond this stabilization
  milestone — captured as a recommended follow-up project, not 0048 scope.
- **`CSRF_COOKIE_HTTPONLY`**: intentionally left at Django's default (the CSRF
  cookie must be JS-readable); not a gap. Noted so it is not re-raised.
- **Secure/overwrite deletion of sensitive deferred files**: out of scope for
  git hygiene; standard `git rm` + history considerations only.

### Part 4: Normalize repository hygiene

Add **targeted** `.gitignore` rules for the durable generated paths only
(`lavandula/reports/.crawler.*.lock`, `lavandula/reports/reports.db`,
`lavandula/dashboard/staticfiles/`, `downloads/`, `screenshots/`, root
`*_website.json`, `/output.json`). No broad patterns that could mask source.

## Data Model Changes

None. (The standard *describes* the attribution model's QA contract; the schema
is a separate downstream project.)

## Security Considerations

### Threat: shipping the validation bypass as a default
**Risk**: `MISMATCH_DISABLED=True` / `validate_structure=False` disables Spec
0047's open-redirect and Content-Type defenses for the entire corpus if merged.
**Mitigation**: Part 2 discards the bypass; the `MISMATCH_DISABLED` flag (if
retained at all) must default `False` and any production tool setting it `True`
is a review-blocking finding.

### Threat: losing the CSRF hardening during cleanup
**Risk**: The macro-plan and the earlier draft frame all uncommitted edits as
"the bypass," risking deletion of `CSRF_COOKIE_SECURE` and the logout-CSRF fix.
**Mitigation**: Part 2's table explicitly classes these as keep.

### Threat: destructive history reconciliation losing wanted work
**Risk**: Resetting local `master` discards 4 commits' content.
**Mitigation**: Part 1 requires the content be re-homed in a reviewed PR
*before* any reset, and the reset is human-gated.

### Threat: blanket artifact deletion destroying real work
**Risk**: Migration SQL, audit findings, spike data deleted as "noise."
**Mitigation**: Part 2 defers every ambiguous path to an explicit human
decision; never delete logs before a replacement is confirmed.

## Success Criteria

- [ ] Local `master`, current branch, and `origin/master` reconciled; the 4
      hot-patch commits dispositioned by recorded human decision.
- [ ] Security bypass absent from the tree; CSRF hardening preserved.
- [ ] `views.py` behavioral change either justified-in-writing or reverted.
- [ ] Every untracked path has a recorded keep/ignore/delete/defer decision; no
      blanket deletion of ambiguous work.
- [ ] Targeted `.gitignore` rules added; previously-noisy paths untracked.
- [ ] `locard/resources/data-quality-standard.md` exists with confidence rubric,
      sampling design, sponsor-approved acceptance threshold, stratum-level
      quarantine policy, and the extraction-QA symmetry stub.
- [ ] `git status` shows only intentional, reviewable changes.
- [ ] Macro-plan Phase 2 can start from a known commit against a defined bar.

## Open Decisions (sponsor sign-off required)

Each has a defined default so the milestone is never silently blocked — the
decision changes the default, it does not gate basic progress:

1. Disposition of the 4 hot-patch commits. **Default if no decision:** Option B
   (non-destructive — preserve on `backup/master-pre-0048`, no reset).
   Recommended: re-home via PR, then human-gated reset (Option A).
2. The attribution-QA acceptance threshold. **Default if no decision:** the
   strawman (≤ 1% high/medium; `low` excluded), standard marked PROVISIONAL,
   production writes blocked until ratified.
3. Fate of the deferred ambiguous artifacts (migration SQL, 0040 audit
   findings, classifier_refactor migrations, spikes/001-data). **Default if no
   decision:** retain in place (no deletion) until explicitly ratified.

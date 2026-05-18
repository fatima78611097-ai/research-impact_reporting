# Plan 0048: Recovery Readiness — Repository Stabilization & Data-Quality Bar

**Protocol**: MAINTAIN (Parts 1, 2, 4) + standards authoring (Part 3)
**Spec**: locard/specs/0048-recovery-readiness-baseline.md
**Created**: 2026-05-18
**Status**: draft

## Overview

Execute Spec 0048: reconcile the divergent git state, resolve the working tree
(discard the validation bypass, keep the CSRF hardening), remove/ignore
generated noise, and author the binding Data-Quality & Attribution-Confidence
Standard. Outcome: a defensible code baseline and a falsifiable QA bar so the
macro-plan's Phase 2 (orphan reconciliation) can begin.

This plan touches git history and is destructive at one step (local `master`
reset). Those steps are explicitly **human-gated** and must stop for sign-off.

## Implementation Steps

### Step 0: Snapshot and inventory (no mutation)

**Files**: none (read-only)

- Record current SHAs: `git rev-parse HEAD master origin/master` and
  `git merge-base master origin/master`.
- Capture `git log --oneline origin/master..master` (the 4 hot-patches) and
  `master..origin/master` (the 2 clean commits) into the maintenance record.
- **Freeze a snapshot**: write the captured commit SHA, verbatim
  `git status --porcelain`, and `git ls-files --others --exclude-standard`
  output (with timestamp) at the top of `locard/maintain/0048-inventory.md`.
- Produce the full per-path classification table from Spec 0048 Part 2 against
  that frozen snapshot — **exactly one row per logical path** (normalize a path
  that appears tracked + staged + untracked into a single disposition).
  Nothing is deleted until the table is written, every `defer` carries a
  recorded decision (decider/date/link), and the snapshot is still current
  (re-freeze + reconcile if the tree moved).
- Create a safety tag/branch on local `master`'s tip
  (`git branch backup/master-pre-0048 master`) so the 4 commits' SHAs are
  recoverable even after reset.

### Step 1: Re-home the substantive hot-patch work (PR, human-gated)

**Files**: `recovery_pass1.py`, `recovery_pass2.py`, `settings.py`,
`base.html` (the wanted content)

- Cut a clean branch from `origin/master`.
- Bring over the *wanted* content only: the `website → website_url` schema fix,
  `_map_status` / valid-status set, recovery_pass1 threading, plus the CSRF
  hardening (`CSRF_COOKIE_SECURE`, logout GET→POST + `{% csrf_token %}`).
  Source it from the 4 commits and/or the staged worktree — whichever is
  cleanest — but **exclude** `validate_structure=False`,
  `MISMATCH_DISABLED=True`, and (unless justified) the `fetch_pdf.py`
  `MISMATCH_DISABLED` flag.
- Open a PR. This is the "recovery scripts on a known commit" outcome. Normal
  review applies. Do **not** self-merge (Architect rule).
- **GATE:** sponsor approves the re-home contents and the disposition of the
  `[Spec 0047] status` commit (fold into projectlist, not a code commit)
  before proceeding to Step 2's reset.

### Step 2: Reconcile branches (destructive — human-gated)

**Files**: none (refs only)

- After the Step 1 PR is merged to `origin/master`: fast-forward / reset local
  `master` to `origin/master`.
- **GATE:** this discards the 4 local commit SHAs. Require explicit human
  "yes, reset local master" confirmation. `backup/master-pre-0048` from Step 0
  is the recovery path. Never run `reset --hard` here without that confirmation.
- Verify: `git rev-list --left-right --count master...origin/master` → `0  0`.

### Step 3: Resolve the working tree

**Files**: `views.py`, `fetch_pdf.py`, `recovery_pass1.py` (worktree),
staged diffs

- Confirm the security bypass is absent from the post-reconcile tree
  (no `validate_structure=False`, no `MISMATCH_DISABLED=True`). If any residue
  remains uncommitted, discard it.
- **Non-negotiable (Spec 0048 Part 2):** default action is to remove the
  `MISMATCH_DISABLED` flag from `fetch_pdf.py` entirely. It is retained only if
  Spec 0047 strictly requires it; if retained it must default `False`, carry an
  in-code `# SECURITY:` warning, and have a negative test (below). Any
  production path setting it `True` is a blocking finding.
- `views.py` `_v3_state_grid` subquery removal: either record a written
  rationale (and route it through the Step 1 PR or its own PR) or revert it.
  Do not leave it as an unexplained loose change.
- Drop the now-redundant staged copies (their content lives in the merged PR).
- Confirm CSRF hardening is present in the reconciled tree (it rode in via
  Step 1).

### Step 4: Remove/ignore generated noise; defer ambiguous work

**Files**: `.gitignore`, generated artifact paths

**Precondition:** Steps 3 and 4 may not begin until Step 1's PR is merged
**and** Step 2 is resolved (either the reset is confirmed, or Option B is
chosen). Until then the tree state is not final and any cleanup is premature.

- Add **targeted** `.gitignore` rules only:
  `lavandula/reports/.crawler.*.lock`, `lavandula/reports/reports.db`,
  `lavandula/dashboard/staticfiles/`, `downloads/`, `screenshots/`,
  `/*_website.json`, `/output.json`.

**Step 4a — safe to delete now (regenerable, no decision needed):** after the
ignore rules exist, remove the clearly-generated artifacts (crawler locks,
`reports.db`, `staticfiles/`, `downloads/`, `screenshots/`, root scratch JSON)
and the superseded `locard/plans/0048-repository-cleanup-for-approval.md`.
These are reproducible or explicitly replaced — no human gate.

**Step 4b — delete only after recorded human confirmation:** migration SQL
under `sample_pdfs/`, `locard/reviews/0040-audit-findings.md`,
`lavandula/migrations/classifier_refactor/`, `locard/spikes/001-data/`,
`cohort_junk_rate.sql`, handoff files, `GEMINI.md`,
`lavandula/review_uploads/`, `experiments/`. Present the list; act only on the
recorded decision in `locard/maintain/0048-inventory.md`. Default = retain.
Never delete logs before a replacement is confirmed.

### Step 5: Author the Data-Quality & Attribution-Confidence Standard

**Files**: `locard/resources/data-quality-standard.md` (new)

- Write the attribution-confidence rubric (high / medium / low / reject) with
  the exact evidence/fields that produce each tier (Spec 0048 Part 3).
- Define the sampling design: strata = `state × NTEE-major × recovery-source`;
  minimum `n` per stratum; measured statistic = EIN-misattribution rate.
- **GATE (soft):** insert the sponsor-approved acceptance threshold(s). If the
  decision is delayed or withheld, do **not** stall the milestone: ship the
  strawman default (≤ 1% high/medium; `low` excluded), mark the standard
  **PROVISIONAL** at the top, and record that production recovery writes remain
  blocked until the threshold is ratified. The milestone closes; the *next*
  phase stays gated. (Spec 0048 §Open Decisions item 2.)
- Define stratum-level quarantine as the failure action (not all-or-nothing).
- Add the extraction-QA symmetry stub (detailed bar deferred to Docling
  project, ownership recorded here).
- Mark the standard binding: downstream recovery/ingestion specs fail review if
  they ingest below threshold or omit the confidence rubric.

### Step 6: Reconcile project tracking

**Files**: `locard/projectlist.md`

- Land the already-intended `0047 → integrated` status and the `0048`
  reservation as a coherent, isolated change (separate from any code), folding
  in the intent of the dropped `[Spec 0047] status` hot-patch commit.
- Update 0048 status per lifecycle (AI stops at `conceived`/`planned`; only the
  human marks `specified`/`integrated`).

### Step 7: Verify the cleaned state

**Files**: none

- `git status --porcelain` shows only intentional, reviewable changes.
- `git rev-list --left-right --count master...origin/master` → `0  0`.
- Grep the tree: no `validate_structure=False`, no `MISMATCH_DISABLED = True`.
- CSRF hardening present.
- `data-quality-standard.md` exists with all required sections including a
  concrete sponsor-approved threshold.
- Previously-noisy paths no longer appear in `git status`.

## Files Likely to Change

### New Files
- `locard/specs/0048-recovery-readiness-baseline.md` (this milestone's spec)
- `locard/plans/0048-recovery-readiness-baseline.md` (this plan)
- `locard/resources/data-quality-standard.md` (the binding QA standard)

### Modified Files
- `.gitignore` (targeted rules)
- `locard/projectlist.md` (0047→integrated, 0048 lifecycle)
- Recovery scripts + CSRF hardening land via the Step 1 PR (reviewed
  separately), not as loose edits in this change set.

### Deleted Files
- `locard/plans/0048-repository-cleanup-for-approval.md` (superseded)
- Generated artifacts per Step 4 (after ignore rules)

## Testing Strategy

### Manual Validation

**Git reconciliation:**
- `git rev-list --left-right --count master...origin/master` → `0  0` (Option
  A) **or** documented Option B with `backup/master-pre-0048` present
  (`git rev-parse --verify backup/master-pre-0048`).
- `git status --porcelain` shows only intentional, reviewable paths.
- `git log origin/master..master` empty (A) or explicitly N/A (B).

**Bypass absence / hardening presence (grep gates):**
- `grep -rn "MISMATCH_DISABLED = True\|validate_structure=False"
  lavandula/` → no hits.
- `CSRF_COOKIE_SECURE = True` present in `settings.py`; logout `<form
  method="post"` + `{% csrf_token %}` present in `base.html`.

**Ignore rules:** `git check-ignore` returns a hit for each previously-noisy
path. **Hard reject:** if any currently-tracked path
(`git ls-files`) matches a newly-added ignore rule, the rule is too broad —
back it out and narrow before proceeding.

**Inventory artifact:** `locard/maintain/0048-inventory.md` exists; every
`git status` / untracked path has a row; zero `defer` rows remain
unresolved (or each carries a recorded human decision + date).

**Data-quality standard checklist** (each must be checkable, not prose):
- [ ] Confidence rubric: all four tiers (high/medium/low/reject) with the exact
      fields/evidence producing each.
- [ ] Sampling design: strata defined, minimum `n` per stratum stated,
      measured statistic named.
- [ ] Threshold: a concrete number present (strawman or ratified), and a
      PROVISIONAL marker iff unratified.
- [ ] Failure action: stratum-level quarantine explicitly stated (not
      all-or-nothing).
- [ ] Field provenance table present (existing / derived / downstream).
- [ ] Extraction-QA symmetry stub present with recorded ownership.
- [ ] Binding clause present (downstream specs fail review if non-compliant).

### Automated Validation
- Run the narrowest relevant `lavandula/reports/tools` test subset for the
  recovery scripts that land via the Step 1 PR (validates the schema fix
  doesn't regress).
- Confirm previously-noisy paths are ignored: `git check-ignore` on each.
- **Negative test for the bypass flag**: if `MISMATCH_DISABLED` is retained,
  assert via test that no production code path sets it `True` and that its
  module default is `False`. If the flag was removed, assert it is absent.
- **Inventory completeness assertion**: every entry in the frozen
  `git status --porcelain` + untracked snapshot maps to exactly one row in
  `locard/maintain/0048-inventory.md`; fail if any path is missing or doubled.

## Success Criteria

- [ ] Branches reconciled; 4 hot-patch commits dispositioned by recorded
      human decision; `backup/master-pre-0048` exists.
- [ ] Security bypass absent; CSRF hardening present; `views.py` change
      justified or reverted.
- [ ] Every untracked path decided; ambiguous work deferred to human, not
      blanket-deleted.
- [ ] Targeted `.gitignore` rules added; noisy paths untracked.
- [ ] `data-quality-standard.md` complete with sponsor-approved threshold.
- [ ] Macro-plan Phase 2 can start from a known commit against a defined bar.

## Risks

| Risk | If Occurs |
|------|-----------|
| Local `master` reset loses wanted work | Recover from `backup/master-pre-0048`; re-home before retry |
| CSRF hardening swept away with the bypass | Step 1/3 explicitly carry & verify it; grep gate in Step 7 |
| Ambiguous artifact deleted as noise | Step 0 table + Step 4 human-defer gate; recover from git/backup |
| Standard ships with placeholder threshold | Step 5 gate blocks completion until a real number is supplied |
| Step 1 PR self-merged | Architect does not merge; builder/human merges after review |

## Notes

This is the macro-plan's critical-path blocker. It is part stabilization
(MAINTAIN) and part standards authoring. Two steps are destructive/irreversible
(Step 2 reset) or require a sponsor decision (Steps 1, 2, 5 thresholds) — these
must stop and wait, not proceed autonomously.

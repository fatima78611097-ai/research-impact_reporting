# 0048 Repository Stabilization — Frozen-Snapshot Inventory

**Spec**: `locard/specs/0048-recovery-readiness-baseline.md`
**Plan**: `locard/plans/0048-recovery-readiness-baseline.md` (Step 0)
**Builder**: `builder/0048-recovery-readiness-baseline`
**This artifact is the completeness gate.** No path may be deleted until its row
exists here, its `defer` (if any) is resolved, and the snapshot is current.
The Spec 0048 Part 2 table is the *ruleset*; this file is the *binding,
auditable application of it* to the actual tree.

---

## 1. Frozen Snapshot (verbatim capture)

Classification and any later deletion act against **this frozen list**, not the
live tree. If the working tree changes before cleanup completes, the snapshot
must be re-captured and this artifact reconciled.

```
FROZEN_TIMESTAMP_UTC=2026-05-18T20:14:33Z
MAIN_CHECKOUT=/home/ubuntu/research
MAIN_CHECKOUT_BRANCH=fix/reconcile-s3-fetchlog-index-perf
HEAD=284b09791091bc6d7bd77f492de009a68a717de2
master=0eb46c109c0a5fafdfbd1b3a02ff75351621775b
origin/master=7c1e7d48c40f62ddddda37b47da3306f2aaa4d7f
merge_base(master,origin/master)=3cd36c4927927c8588b21865edca47c7e07e2d5e
backup/master-pre-0048=0eb46c109c0a5fafdfbd1b3a02ff75351621775b
left_right_master...origin_master=4	2
```

### 1a. `git -C /home/ubuntu/research status --porcelain` (verbatim)

```
 M lavandula/dashboard/dashboard/settings.py
 M lavandula/dashboard/pipeline/templates/pipeline/base.html
 M lavandula/dashboard/pipeline/views.py
 M lavandula/reports/fetch_pdf.py
MM lavandula/reports/tools/recovery_pass1.py
M  lavandula/reports/tools/recovery_pass2.py
 M locard/config.json
MM locard/projectlist.md
?? GEMINI.md
?? downloads/
?? experiments/
?? health_people_inc_website.json
?? kendal_at_ithaca_website.json
?? lavandula/dashboard/staticfiles/
?? lavandula/migrations/classifier_refactor/
?? lavandula/reports/.crawler.AZ.lock
?? lavandula/reports/.crawler.CA.lock
?? lavandula/reports/.crawler.CO.lock
?? lavandula/reports/.crawler.GA.lock
?? lavandula/reports/.crawler.HI.lock
?? lavandula/reports/.crawler.ID.lock
?? lavandula/reports/.crawler.IN.lock
?? lavandula/reports/.crawler.KY.lock
?? lavandula/reports/.crawler.MA.lock
?? lavandula/reports/.crawler.MD.lock
?? lavandula/reports/.crawler.MN.lock
?? lavandula/reports/.crawler.NJ.lock
?? lavandula/reports/.crawler.NY.lock
?? lavandula/reports/.crawler.OK.lock
?? lavandula/reports/.crawler.PA.lock
?? lavandula/reports/.crawler.RI.lock
?? lavandula/reports/.crawler.SC.lock
?? lavandula/reports/.crawler.SD.lock
?? lavandula/reports/.crawler.TN.lock
?? lavandula/reports/.crawler.VA.lock
?? lavandula/reports/.crawler.WA.lock
?? lavandula/reports/.crawler.WI.lock
?? lavandula/reports/reports.db
?? lavandula/reports/tools/cohort_junk_rate.sql
?? lavandula/review_uploads/
?? locard/operations/macro-plan-corpus-integrity-rag-readiness.md
?? locard/plans/0048-repository-cleanup-for-approval.md
?? locard/project-handoff-reconcile-apply.log
?? locard/project-handoff-recovered-shas.tsv
?? locard/project-handoff.md
?? locard/reviews/0040-audit-findings.md
?? locard/spikes/001-data/
?? mission_investors_website.json
?? output.json
?? sample_pdfs/2024-Impact-Report.pdf
?? sample_pdfs/Samaritan_Medical_Center_2010_Annual_Report-c32ccbca.json
?? sample_pdfs/Samaritan_Medical_Center_2010_Annual_Report.pdf
?? sample_pdfs/migration_004_crawled_orgs_status_attempts.sql
?? sample_pdfs/migration_005_wayback_provenance.sql
?? screenshots/
```

### 1b. `git -C /home/ubuntu/research ls-files --others --exclude-standard --directory` (verbatim)

Lists the same untracked set, dir-collapsed, plus transient tool caches that
`status --porcelain` did not surface (recorded for completeness):

```
.pytest_cache/
.ruff_cache/
GEMINI.md
downloads/
experiments/
health_people_inc_website.json
kendal_at_ithaca_website.json
lavandula/dashboard/.pytest_cache/
lavandula/dashboard/staticfiles/
lavandula/migrations/classifier_refactor/
lavandula/nonprofits/raw/
lavandula/reports/.crawler.AZ.lock ... .WI.lock  (22 files, see 1a)
lavandula/reports/.pytest_cache/
lavandula/reports/.ruff_cache/
lavandula/reports/reports.db
lavandula/reports/tools/cohort_junk_rate.sql
lavandula/review_uploads/
locard/operations/macro-plan-corpus-integrity-rag-readiness.md
locard/plans/0048-repository-cleanup-for-approval.md
locard/project-handoff-reconcile-apply.log
locard/project-handoff-recovered-shas.tsv
locard/project-handoff.md
locard/reviews/0040-audit-findings.md
locard/spikes/001-data/
mission_investors_website.json
output.json
sample_pdfs/2024-Impact-Report.pdf
sample_pdfs/Samaritan_Medical_Center_2010_Annual_Report-c32ccbca.json
sample_pdfs/Samaritan_Medical_Center_2010_Annual_Report.pdf
sample_pdfs/migration_004_crawled_orgs_status_attempts.sql
sample_pdfs/migration_005_wayback_provenance.sql
screenshots/
```

### 1c. Divergence detail

- `origin/master..master` (4 local-only hot-patches, on `backup/master-pre-0048`):
  - `0eb46c1` Add threading + CDN throttle override to recovery_pass1
  - `c85da57` Fix recovery_pass1: correct schema references
  - `d7567a3` Fix recovery pass queries: ns.website → ns.website_url
  - `e2183a5` [Spec 0047] Update status to implementing, add plan path
- `master..origin/master` (2 clean PR-path commits):
  - `7c1e7d4` Merge PR #40: reconcile_s3 single-pass fetch_log sha index
  - `6b4c0f6` Fix reconcile_s3: single-pass fetch_log sha index
- The 4 hot-patches touch: `recovery_pass1.py` (+132), `recovery_pass2.py`
  (+2), `reconcile_s3.py` (−), `tests/unit/test_reconcile_s3_fetchlog_index.py`
  (deleted), `projectlist.md` (e2183a5). **The `reconcile_s3.py` hot-patch edits
  and the test deletion are superseded by `origin/master`'s merged PR #40 — they
  are NOT re-homed** (re-homing them would revert reviewed work). Only the
  `recovery_pass1/2` content is wanted (Spec 0048 Part 1).
- Verified: `master:recovery_pass1.py` line 249 is `validate_structure=True`;
  no `MISMATCH_DISABLED` setter is committed anywhere. The bypass exists **only**
  as unstaged worktree edits in the main checkout.

---

## 2. Decision-record convention

Every `defer` row and every sponsor decision records: **Decision · Decider ·
Date · Link**. Mechanical `keep`/`discard`/`ignore` rows follow the ruleset and
need no decider. Where no human decision has yet been recorded, the **Spec 0048
§Open Decisions default is in effect** (the milestone is never silently blocked;
the decision changes the default, it does not gate basic progress):

- Open Decision 1 (4 hot-patch commits): **default Option B** — non-destructive;
  preserved on `backup/master-pre-0048`; `origin/master` is the sole canonical
  line; local `master` is abandoned as a working ref; **no reset performed**.
- Open Decision 2 (QA threshold): **default strawman** ≤1% high/medium, `low`
  excluded; standard marked **PROVISIONAL**; production writes blocked until
  ratified.
- Open Decision 3 (ambiguous artifacts): **default retain in place** — no
  deletion until explicitly ratified.

---

## 3. Per-path classification (one row per logical path)

Disposition legend: **KEEP** (re-home via Step 1 PR) · **DISCARD** ·
**IGNORE+REMOVE** (Step 4a, post-merge, mechanical) · **DEFER** (human ratifies;
default applies meanwhile) · **RECONCILE** (Step 6).

### 3a. Tracked / modified

| # | Logical path | git state | Class | Disposition | Decision · Decider · Date · Link |
|---|---|---|---|---|---|
| 1 | `lavandula/dashboard/dashboard/settings.py` | ` M` | Security hardening (`+CSRF_COOKIE_SECURE = True`) | **KEEP** — re-home via Step 1 PR | mechanical (Spec 0048 Part 2 table) |
| 2 | `lavandula/dashboard/pipeline/templates/pipeline/base.html` | ` M` | Security hardening (logout GET→POST + `{% csrf_token %}`) | **KEEP** — re-home via Step 1 PR | mechanical |
| 3 | `lavandula/dashboard/pipeline/views.py` | ` M` | Untriaged behavioral (`_v3_state_grid` drops `cr.run_id = (SELECT MAX(...))` join cond.) | **DEFER → default REVERT** (see §4) | Decision: REVERT unless project owner records written rationale. Decider: **project owner** (architect recommends REVERT — see §4 assessment). Date: 2026-05-18. Link: §4 of this file + Spec 0048 Part 2. |
| 4 | `lavandula/reports/fetch_pdf.py` | ` M` | Latent bypass flag (`MISMATCH_DISABLED: bool`) | **DISCARD** (default-remove; not committed anywhere; 0047 does not require it) | mechanical (Spec 0048 Part 2 — default-remove). Negative test asserts absence (see plan validation). |
| 5 | `lavandula/reports/tools/recovery_pass1.py` | `MM` | Staged = real work; unstaged delta = **security bypass** (`validate_structure=False`, `fetch_pdf.MISMATCH_DISABLED=True`) | Real work → **KEEP** via Step 1 PR (sourced from `master` committed tip, bypass-free). Unstaged bypass delta → **DISCARD** | mechanical (Spec 0048 Part 2) |
| 6 | `lavandula/reports/tools/recovery_pass2.py` | `M ` | Real work (`ns.website → ns.website_url`) | **KEEP** via Step 1 PR | mechanical |
| 7 | `locard/config.json` | ` M` | Shared config drift (`claude-opus-4-6 → 4-7` for architect/builder shell) | **DEFER** — outside Spec 0048 Part 2 ruleset; not carried in any 0048 PR | Decision: retain/keep recommended (matches current operating model); pending ratification. Decider: **project owner / architect**. Date: 2026-05-18. Link: CLAUDE.md "don't modify shared config without Architect approval" + this file. |
| 8 | `locard/projectlist.md` | `MM` | Project tracking (0047→integrated; +0048 entry; Next 0048→0049) | **RECONCILE** — Step 6, landed on the builder branch (intent of `e2183a5` folded in here, not as a code commit) | mechanical (Spec 0048 Part 1/Step 6); 0048 status capped at `planned` per AI lifecycle ceiling |

### 3b. Untracked — generated noise (IGNORE+REMOVE; Step 4a, post-merge, mechanical)

| # | Logical path | Class | Disposition |
|---|---|---|---|
| 9 | `lavandula/reports/.crawler.*.lock` (22: AZ CA CO GA HI ID IN KY MA MD MN NJ NY OK PA RI SC SD TN VA WA WI) | Crawler lock files | IGNORE+REMOVE — `.gitignore`: `lavandula/reports/.crawler.*.lock` |
| 10 | `lavandula/reports/reports.db` | Generated SQLite | IGNORE+REMOVE — `.gitignore`: `lavandula/reports/reports.db` |
| 11 | `lavandula/dashboard/staticfiles/` | Django `collectstatic` output | IGNORE+REMOVE — `.gitignore`: `lavandula/dashboard/staticfiles/` |
| 12 | `downloads/` (`gemini-api-setup.md`, `gemini-test-prompt.md`) | Spec-classed generated noise | IGNORE+REMOVE — `.gitignore`: `downloads/`. **Note**: contents are Gemini setup notes; reproducible, non-canonical (canonical workflow lives in `GEMINI.md`, row 22). Human may object before the post-merge removal — listed here for visibility. |
| 13 | `screenshots/` | Generated screenshots | IGNORE+REMOVE — `.gitignore`: `screenshots/` |
| 14 | `health_people_inc_website.json` | Root scratch JSON | IGNORE+REMOVE — `.gitignore`: `/*_website.json` |
| 15 | `kendal_at_ithaca_website.json` | Root scratch JSON | IGNORE+REMOVE — `/*_website.json` |
| 16 | `mission_investors_website.json` | Root scratch JSON | IGNORE+REMOVE — `/*_website.json` |
| 17 | `output.json` | Root scratch JSON | IGNORE+REMOVE — `.gitignore`: `/output.json` |
| 18 | `locard/plans/0048-repository-cleanup-for-approval.md` | Superseded draft (replaced by Spec/Plan 0048) | **REMOVE** (Step 4a, mechanical — untracked, just delete the file in the main checkout post-merge) |

### 3c. Untracked — possible real work / scratch (DEFER; default = retain in place)

All rows below: **Decider = project owner · Date = 2026-05-18 · Link = Spec
0048 §Open Decisions item 3 + this file.** Default (no decision yet) = **retain
in place; no deletion.** Logs are never deleted before a replacement is
confirmed superseded.

| # | Logical path | Class | Recommendation to project owner |
|---|---|---|---|
| 19 | `lavandula/migrations/classifier_refactor/` | Possible real work (migration set) | Defer — likely real; do not delete |
| 20 | `lavandula/reports/tools/cohort_junk_rate.sql` | Possible real work (analysis SQL) | Defer — likely real; do not delete |
| 21 | `lavandula/review_uploads/` | Scratch/handoff | Defer — retain; verify not superseded before any future delete |
| 22 | `GEMINI.md` | Possible canonical docs (security/process conventions) | **Defer — do NOT treat as scratch.** Verify no unique canonical content; migrate to a permanent location before any delete/relocate (Spec 0048 Part 2) |
| 23 | `locard/operations/macro-plan-corpus-integrity-rag-readiness.md` | Real work — the driving macro-plan referenced by Spec 0048 | **Defer — strongly recommend KEEP/track** (this is the program's source-of-truth document; currently untracked) |
| 24 | `locard/project-handoff.md` | Handoff doc (session state, verify-commands) | Defer — retain; do not delete log/handoff before superseded confirmed |
| 25 | `locard/project-handoff-reconcile-apply.log` | Handoff log | Defer — retain; never delete logs before replacement confirmed |
| 26 | `locard/project-handoff-recovered-shas.tsv` | Handoff data (recovered SHAs) | Defer — retain; potential recovery evidence |
| 27 | `locard/reviews/0040-audit-findings.md` | Possible real work (audit findings) | Defer — likely real; do not delete |
| 28 | `locard/spikes/001-data/` | Possible real work (spike data) | Defer — likely real; do not delete |
| 29 | `experiments/` | Scratch/handoff (0001 serpex, 0047 pass2 check) | Defer — retain or relocate per human decision |
| 30 | `sample_pdfs/migration_004_crawled_orgs_status_attempts.sql` | Possible real work (migration SQL) | Defer — likely real; do not delete |
| 31 | `sample_pdfs/migration_005_wayback_provenance.sql` | Possible real work (migration SQL) | Defer — likely real; do not delete |
| 32 | `sample_pdfs/2024-Impact-Report.pdf` | Sample fixture | Defer — retain (test/sample fixture) |
| 33 | `sample_pdfs/Samaritan_Medical_Center_2010_Annual_Report.pdf` | Sample fixture | Defer — retain |
| 34 | `sample_pdfs/Samaritan_Medical_Center_2010_Annual_Report-c32ccbca.json` | Sample fixture metadata | Defer — retain |

### 3d. Untracked — transient tool caches (out of Spec 0048 Part 4 enumerated scope)

Recorded for completeness; **no deletion, no `.gitignore` change in this
milestone** (Spec 0048 Part 4 mandates only the enumerated targeted rules — no
broad patterns). Flagged as a recommended hygiene follow-up, not 0048 scope.

| # | Logical path | Class | Disposition |
|---|---|---|---|
| 35 | `.pytest_cache/` | Transient pytest cache | Out-of-scope noise; follow-up |
| 36 | `.ruff_cache/` | Transient ruff cache | Out-of-scope noise; follow-up |
| 37 | `lavandula/dashboard/.pytest_cache/` | Transient pytest cache | Out-of-scope noise; follow-up |
| 38 | `lavandula/reports/.pytest_cache/` | Transient pytest cache | Out-of-scope noise; follow-up |
| 39 | `lavandula/reports/.ruff_cache/` | Transient ruff cache | Out-of-scope noise; follow-up |
| 40 | `lavandula/nonprofits/raw/` | Raw data dir (only in `ls-files`, not `status`) | Defer — retain; likely raw data input, out of 0048 scope |

---

## 4. `views.py` `_v3_state_grid` — required data-exposure / DoS assessment

Spec 0048 Part 2 requires a written rationale for the `cr.run_id = (SELECT
MAX(id) FROM classification_runs WHERE finished_at IS NOT NULL)` join-condition
removal that **explicitly assesses data-exposure and DoS**, even though the
change is not security-motivated. Architect recommendation (project owner
ratifies):

**The change**: in `_v3_state_grid()` the `LEFT JOIN classification_results cr
ON c.content_sha256 = cr.content_sha256` previously also required
`AND cr.run_id = (SELECT MAX(id) FROM classification_runs WHERE finished_at IS
NOT NULL)`. The removal makes the join match `cr` rows from **all**
classification runs for a `content_sha256`, not only the latest finished run.
The query is a `GROUP BY s.state` aggregation feeding an internal state grid.

- **Data exposure**: The endpoint returns per-state aggregate counts on an
  authenticated internal dashboard — not per-row sensitive records. Removing the
  run filter does not widen the *kind* of data returned; it inflates/duplicates
  aggregate counts when a `content_sha256` was classified in multiple runs.
  → **No meaningful data-exposure widening.** It is a correctness regression
  (over-counting), not a confidentiality issue.
- **DoS / query load**: The removed correlated subquery is a cheap aggregate on
  the small `classification_runs` table; removing it marginally *lowers*
  per-join cost. The unfiltered join fans out more `classification_results` rows
  into the `GROUP BY`, but growth is bounded by `classification_results` size —
  no unbounded amplification, no user-controllable multiplier.
  → **Not a DoS vector.**
- **Conclusion**: Low security risk, but an **unexplained behavioral/correctness
  change** with no recorded rationale. Per Spec 0048 Part 2, an unexplained
  behavioral change must never ride through a stabilization pass.
  **Recommendation: REVERT** (the default) unless the project owner records a
  written feature rationale. Execution of the revert is a post-merge Step 3
  action in the main checkout (gated on Step 1 PR merged + Step 2 resolved).

---

## 5. Completeness assertion

Every entry in the frozen `git status --porcelain` (§1a) and the untracked
snapshot (§1b) maps to **exactly one** logical row in §3 (multi-state paths —
e.g. `recovery_pass1.py` tracked+staged+unstaged — are normalized to a single
row, #5). The 22 `.crawler.*.lock` files are one logical row (#9). No path is
missing; no path is doubled. If the working tree moves before post-merge
cleanup, re-capture §1 and reconcile §3 before any deletion.

## 6. Disposition of the 4 hot-patch commits (Open Decision 1)

**Default in effect: Option B (non-destructive).**

- `backup/master-pre-0048` = `0eb46c1` created and verified — the 4 commits'
  SHAs are permanently recoverable.
- Local `master` is **not reset** in this milestone (reset is destructive and
  human-gated; default Option B requires no reset and no further sign-off).
- `origin/master` is treated as the **sole canonical line**; all future work
  branches from `origin/master`. Local `master` is explicitly **abandoned as a
  working ref** (documented stale ref, recoverable via the backup branch).
- The wanted recovery-script content is re-homed via the Step 1 PR (separate,
  reviewed, cut from `origin/master`), satisfying macro-plan §Workstream 1
  "recovery scripts on a known commit."
- If the sponsor instead approves Option A (re-home-then-reset), the reset
  remains a separate human-gated action; `backup/master-pre-0048` is the
  recovery path.

# Plan 0037: Rename RDS Roles to Project-Prefixed Names

**Spec**: `locard/specs/0037-rds-role-rename.md`
**Status**: Draft
**Phases**: 5 (B = builder, O = operator, M = mixed)
**Prefix**: `research` (operator-confirmed 2026-05-10)

## Context for the Builder

This work renames two Postgres roles on the shared RDS instance so a second project can coexist without role-name collisions. The clean part: the codebase already abstracts usernames behind SSM keys (`rds-app-user`, `rds-ro-user`) — only the SSM **values** change, not the keys. This means **zero changes to `lavandula/common/db.py` or `lavandula/dashboard/dashboard/settings.py`**.

The builder's job is to (1) update 6 files where old role names are hardcoded, (2) write the operator runbook + helper scripts that drive the live cutover, and (3) prove correctness against a scratch database. The builder **does NOT execute the live cutover** — that is operator-only work documented in the runbook.

**Key codebase context**:
- Username flow: `lavandula/common/db.py:162-181` (`_engine_from_ssm`), `lavandula/dashboard/dashboard/settings.py:72` (`_APP_USER`)
- SSM secrets reader: `lavandula/common/secrets.py` — `get_secret(short_name)` with `lru_cache(maxsize=64)` and `_PARAM_PREFIX = "/cloud2.lavandulagroup.com/"`
- Test fixtures: `lavandula/common/tests/conftest.py` (role-presence assertions), `lavandula/common/tests/unit/test_db_adapter_0013.py` (11 hardcoded references)
- Migration runner: migrations are applied manually via `psql -f` (not Alembic/Django migrations); no migration framework metadata to update
- IAM policy: attached to EC2 instance profile `cloud2_lavandulagroup`; resource ARNs are `arn:aws:rds-db:us-east-1:ACCT:dbuser/DB-RES-ID/<rolename>`

**Important constraints**:
- The builder's PR must NOT be merged until after the live cutover succeeds (so source tree never describes a state that diverges from production for more than the cutover window).
- All SQL files use `app_user1` and `ro_user1` literally — no parameterization, so mechanical find/replace is correct.
- Scratch-DB validation requires Docker + Postgres locally, OR an ephemeral RDS scratch instance — the builder picks based on what's available.
- Helper scripts go under `locard/operations/0037-*` (a new directory) — they are operational artifacts, not application code.

---

## Phase 1: Source-tree Edits (B)

**Goal**: Replace every hardcoded `app_user1`/`ro_user1` reference in the 6 files identified in the spec inventory. Verify grep returns zero before moving on.

**Files**:
- `lavandula/migrations/rds/001_initial_schema.sql`
- `lavandula/migrations/rds/002_attribution_helper.sql`
- `lavandula/migrations/rds/010_990_people_filing_index.sql`
- `lavandula/migrations/rds/migration_011_990_index_automation.sql`
- `lavandula/common/tests/conftest.py`
- `lavandula/common/tests/unit/test_db_adapter_0013.py`

### Implementation Steps

1. **Mechanical replacement** in each of the 6 files:
   - `app_user1` → `research_app`
   - `ro_user1` → `research_ro`

2. **Verify zero remaining references** in source tree:
   ```bash
   grep -rn -E "app_user1|ro_user1" \
     lavandula/migrations/ lavandula/common/tests/
   # Expected: zero matches
   ```

3. **Verify SSM-indirection files are NOT edited** (regression check):
   ```bash
   git diff --name-only HEAD lavandula/common/db.py \
     lavandula/dashboard/dashboard/settings.py
   # Expected: zero output (these files must remain unchanged)
   ```

4. **Optional narrative-prose footnote edits** (low priority, defer if time-constrained): add a one-line footnote to `locard/specs/0013-rds-postgres-migration.md`, `0017-retire-sqlite.md`, `locard/plans/0013-phase2-backfill.md`, `0017-retire-sqlite.md` saying "Roles renamed to `research_app`/`research_ro` per Spec 0037 (2026-05-10)." Do not rewrite the historical narrative.

### Phase 1 Acceptance

- 6 files modified; diff shows ONLY `app_user1 → research_app` and `ro_user1 → research_ro` substitutions (no incidental changes).
- `grep -rn -E "app_user1|ro_user1"` against `lavandula/` returns zero hits.
- `lavandula/common/db.py` and `lavandula/dashboard/dashboard/settings.py` are byte-identical to `master`.

---

## Phase 2: Operator Runbook + Helper Scripts (B)

**Goal**: Produce a self-contained operator runbook that an operator can execute without re-reading the spec, plus the SQL/shell scripts the runbook invokes.

**Files (new)**:
- `locard/operations/0037-role-rename-runbook.md` — step-by-step T-1..T+6 procedure with exact commands, expected outputs, and decision points
- `locard/operations/0037-pre-rename-checks.sql` — the four pre-check queries from spec §"Pre-rename checks"
- `locard/operations/0037-snapshot-pre.sh` — bash script that runs the 4 snapshot queries and writes outputs to `/tmp/0037_*.before.txt`
- `locard/operations/0037-snapshot-post.sh` — same queries against post-rename DB; writes to `/tmp/0037_*.after.txt`
- `locard/operations/0037-parity-diff.sh` — applies `sed 's/app_user1/research_app/g; s/ro_user1/research_ro/g'` to each `before` file and `diff`s against `after`; non-zero exit if any diff is non-empty
- `locard/operations/0037-iam-policy-overlay.json` — the additive-overlap policy JSON for the EC2 instance profile (operator copies this into the AWS console at T-1)
- `locard/operations/0037-iam-policy-final.json` — the post-cleanup policy with only the new ARNs (operator applies at T+6)
- `locard/operations/0037-cutover-rename.sql` — the transactional rename
- `locard/operations/0037-cutover-rollback.sql` — the symmetric reverse rename
- `locard/operations/0037-smoke-test.sh` — runs the IAM-authenticated psql connect tests for both roles (T+4 and T+5)

### Implementation Steps

1. **Write the runbook** as a numbered T-1..T+6 sequence. Each step includes:
   - **Precondition**: SQL or shell command + expected output
   - **Action**: exact command(s) to run
   - **Verification**: SQL or shell command + expected output
   - **Failure branch**: pointer to spec §"Rollback Plan §Decision tree by failure point"

   The runbook copies content from spec §"Cutover sequence" verbatim, then expands placeholders to concrete values. The operator will fill in `$RDS_ENDPOINT`, `$DB`, `ACCT`, and `DB-RES-ID` as environment variables before running.

2. **Write `0037-pre-rename-checks.sql`** with the four queries from spec §"Pre-rename checks" (steps 1, 2, 3, and 4a-4d). Each query has a header comment explaining its purpose and expected output. The file is operator-runnable via `psql -f`.

3. **Write `0037-snapshot-pre.sh`**:
   - Reads `RDS_ENDPOINT`, `DB`, `IAM_USER` from env vars
   - Calls `aws rds generate-db-auth-token` to mint a token for the IAM_USER
   - Runs each snapshot query with `psql -At` (tab-aligned, no headers) and writes output to `/tmp/0037_<dimension>.before.txt`
   - Files produced: `grants_corpus.before.txt`, `grants_pipeline.before.txt`, `grants_dashboard.before.txt`, `default_acl.before.txt`, `ownership_objects.before.txt`, `ownership_schemas.before.txt`, `memberships.before.txt`
   - Exits non-zero if any query fails

4. **Write `0037-snapshot-post.sh`**: identical to `pre.sh` but writes `.after.txt` files. (Could be a single script with a CLI flag — builder's call.)

5. **Write `0037-parity-diff.sh`**:
   - For each of the 4 snapshot dimensions, apply `sed 's/app_user1/research_app/g; s/ro_user1/research_ro/g'` to the `.before.txt` file and `diff -u` against the `.after.txt` file
   - Print PASS/FAIL per dimension
   - Exit non-zero if ANY dimension diff is non-empty
   - This script implements AC #6 from the spec (privilege parity, 4 dimensions)

6. **Write `0037-iam-policy-overlay.json`**: a JSON IAM policy document with `Statement` containing `Action: rds-db:connect` and `Resource` listing all FOUR ARNs (old × 2 + new × 2). Operator applies this at T-1.

7. **Write `0037-iam-policy-final.json`**: the post-cleanup version listing only the two new ARNs. Operator applies at T+6.

8. **Write `0037-cutover-rename.sql`**:
   ```sql
   BEGIN;
   ALTER ROLE app_user1 RENAME TO research_app;
   ALTER ROLE ro_user1  RENAME TO research_ro;
   -- dashboard_user1 branch: only if pre-check directs DROP. If RENAME branch
   -- triggered, runbook HALTs — see spec §"dashboard_user1 decision rule".
   COMMIT;
   ```

9. **Write `0037-cutover-rollback.sql`**: the symmetric reverse, run only if rollback decision tree directs full rollback.

10. **Write `0037-smoke-test.sh`**:
    - Generates IAM tokens for `research_app` and `research_ro`
    - Connects with each via `psql` and runs `SELECT current_user, 1 AS ok`
    - Asserts the returned `current_user` matches the expected role
    - Runs read query (`SELECT 1 FROM lava_corpus.corpus LIMIT 1`) as `research_ro`
    - Runs write query (`INSERT into a no-op table; ROLLBACK`) as `research_app`
    - Exits 0 only if all four checks pass

### Phase 2 Acceptance

- All 9 files created under `locard/operations/0037-*`.
- `bash -n locard/operations/*.sh` (syntax check) passes for each shell script.
- `psql -f locard/operations/0037-pre-rename-checks.sql --set ON_ERROR_STOP=1` runs cleanly against any Postgres ≥14 (does not need RDS).
- The runbook references each helper script by relative path; no orphan or broken references.
- IAM policy JSONs validate against the AWS IAM policy schema (use `aws iam validate-policy-document` if available, otherwise manual review).

---

## Phase 3: Targeted Test Validation (B)

**Goal**: Prove the source-tree edits do not break the test suite and that fresh-rebuild correctness holds.

### Implementation Steps

1. **Run targeted unit tests** (per spec AC #8):
   ```bash
   cd /home/ubuntu/research
   pytest lavandula/common/tests/unit/test_db_adapter_0013.py -v
   pytest lavandula/common/tests/ -v -k "not slow"
   ```
   All tests must pass. If a test references the old names via parameterization that the grep-based search missed, it surfaces here.

2. **Run scratch-DB migration validation** (per spec AC #8):
   ```bash
   # Use a local Docker Postgres or ephemeral RDS scratch instance
   docker run -d --rm --name pg37 -e POSTGRES_PASSWORD=test \
     -p 5499:5432 postgres:16
   sleep 5
   psql "host=localhost port=5499 user=postgres dbname=postgres" \
     -c "CREATE DATABASE lava_test;"
   psql "host=localhost port=5499 user=postgres dbname=lava_test" \
     -c "CREATE ROLE research_app; CREATE ROLE research_ro; CREATE ROLE rds_iam;
         GRANT rds_iam TO research_app; GRANT rds_iam TO research_ro;"
   for f in lavandula/migrations/rds/{001,002,003,004,005,006,007,008,009,010,011,012,013,014}_*.sql; do
     psql "host=localhost port=5499 user=postgres dbname=lava_test" \
       -v ON_ERROR_STOP=1 -f "$f" \
       || { echo "FAIL: $f"; exit 1; }
   done
   docker stop pg37
   ```
   All 14 migrations must apply successfully.

3. **Document results** in the PR description: paste the test summary lines and the migration loop's "all OK" output. The reviewer should be able to read the PR description and see all checks passed without re-running them.

### Phase 3 Acceptance

- `pytest` exit code 0 for both test invocations.
- Scratch-DB migration loop applies all 14 migrations to completion (exit 0 from the for-loop).
- PR description contains the test output summary and migration validation output.

---

## Phase 4: Live Cutover (O — operator-only)

**Goal**: Execute the runbook against the live RDS instance. Builder is NOT involved in this phase except as documentation reference.

### Operator Steps (driven by runbook)

1. Schedule a maintenance window (3–10 min outage).
2. Run `0037-pre-rename-checks.sql` and capture the result. Decide branch for `dashboard_user1` per spec §"dashboard_user1 decision rule".
3. Run `0037-snapshot-pre.sh` to capture all 4 dimensions of pre-rename state.
4. **T-1**: Apply `0037-iam-policy-overlay.json` to `cloud2_lavandulagroup` instance profile. Wait ≥5 min. Verify additive overlap with the warm-up psql connect (expected: "role does not exist" — proves IAM allows the rds-db:connect through).
5. **T+0**: `systemctl stop lavandula-dashboard`; `ps -ef` audit; `pg_terminate_backend` for any leftover sessions.
6. **T+1**: Run `0037-cutover-rename.sql` (transactional).
7. **T+2**: Update SSM values for `rds-app-user` and `rds-ro-user`.
8. **T+3**: No action (additive overlap already in place).
9. **T+4**: Run smoke psql connect tests as `research_app` and `research_ro`. Both must succeed. Then `systemctl start lavandula-dashboard`.
10. **T+5**: Run `0037-smoke-test.sh`. Hit dashboard endpoints (`/orgs/`, `/jobs/`, write a noop Job).
11. Wait ≥30 minutes of stable operation.
12. Run `0037-snapshot-post.sh` and `0037-parity-diff.sh`. AC #6 must pass (zero diff across all 4 dimensions).
13. **T+6**: Apply `0037-iam-policy-final.json` (removes old ARNs).
14. Verify with the warm-up psql connect against the OLD role name — expected: "PAM authentication failed" (proves IAM no longer allows the old ARN).

### Failure Handling

If any step fails, consult spec §"Rollback Plan §Decision tree by failure point". Do NOT proceed past a failure without the spec's guidance for that specific failure point.

### Phase 4 Acceptance

- All AC items 1-6 from spec §"Acceptance Criteria" hold.
- Operator records execution log in `locard/operations/0037-cutover-log-YYYY-MM-DD.md` (timestamps, decisions taken at each branch point, any deviations from the runbook).

---

## Phase 5: PR Merge + Documentation (M)

**Goal**: Merge the source-tree PR after live cutover succeeds. Capture lessons learned.

### Steps

1. **Builder merges PR** after operator confirms Phase 4 acceptance. Ordering matters: live cutover MUST happen first, source-tree edits MUST be merged within the same maintenance window. If cutover succeeds but PR merge is delayed, source tree is correct but historical inconsistency persists — flag and merge ASAP.

2. **Builder writes lessons-learned entry** in `locard/resources/lessons-learned.md` if any deviations from the runbook occurred during cutover. Examples worth recording:
   - IAM propagation took longer/shorter than expected
   - Connection drain required `pg_terminate_backend` (sessions didn't close cleanly)
   - Privilege-parity diff revealed unexpected differences (and how they were resolved)
   - `dashboard_user1` branch hit any state other than "absent"

3. **Builder writes review document** at `locard/reviews/0037-rds-role-rename.md` summarizing:
   - Outage duration (actual vs estimated 3-10 min)
   - All 6 AC items from spec verified
   - Any changes made during execution (deviations from plan)
   - Sign-off: builder + operator

4. **Operator updates project status** in `locard/projectlist.md` from `planned` → `implementing` → `implemented` → `committed` → `integrated` as the work progresses.

### Phase 5 Acceptance

- PR merged to master.
- `locard/reviews/0037-rds-role-rename.md` exists and is signed off.
- `locard/projectlist.md` shows status `integrated` for project 0037.
- (If applicable) `locard/resources/lessons-learned.md` has new entry.

---

## Open Items / Decisions

1. **Maintenance window scheduling**: out of scope for the plan; operator picks based on operational calendar. Plan assumes the operator coordinates this independently.

2. **Scratch-DB choice (Phase 3 step 2)**: Docker is faster but builder may prefer ephemeral RDS for closer fidelity. Either is acceptable as long as Phase 3 acceptance is met.

3. **Narrative-prose footnotes (Phase 1 step 4)**: optional. Skip if time-constrained; can be a follow-up TICK.

## Risks Inherited from Spec

This plan does not introduce new risks beyond those documented in spec §"Risks & Mitigations". The plan's primary risk is **plan-execution divergence**: an operator deviating from the runbook without consulting the spec's decision tree. Mitigation: each step of the runbook explicitly cross-references the spec's failure-point decision tree.

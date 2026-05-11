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

2. **Verify zero remaining references** in source tree (matches Phase 1 acceptance and spec AC #7):
   ```bash
   grep -rn -E "app_user1|ro_user1" lavandula/
   # Expected: zero matches across the entire lavandula/ tree
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
- `locard/operations/0037-snapshot-pre.sh` — bash script that runs the snapshot queries and writes outputs to `/tmp/0037_*.before.txt`
- `locard/operations/0037-snapshot-post.sh` — same queries against post-rename DB; writes to `/tmp/0037_*.after.txt`
- `locard/operations/0037-parity-diff.sh` — applies `sed 's/app_user1/research_app/g; s/ro_user1/research_ro/g'` to each `before` file and `diff`s against `after`; non-zero exit if any diff is non-empty
- `locard/operations/0037-iam-policy-overlay.json` — the additive-overlap policy JSON for the EC2 instance profile (operator copies this into the AWS console at T-1)
- `locard/operations/0037-iam-policy-final.json` — the post-cleanup policy with only the new ARNs (operator applies at T+6)
- `locard/operations/0037-cutover-rename.sql` — the transactional rename
- `locard/operations/0037-cutover-rollback.sql` — the symmetric reverse rename
- `locard/operations/0037-cutover-dashboard-user-drop.sql` — conditional `DROP ROLE dashboard_user1;` for the vestigial branch (operator runs ONLY if pre-check directs DROP per spec §"dashboard_user1 decision rule")
- `locard/operations/0037-smoke-test.sh` — runs the IAM-authenticated psql connect tests for both roles (T+4 and T+5)

**Idempotency requirement for ALL scripts in this phase**: every script must be safe to re-run. Specifically:
- Snapshot scripts: overwrite (don't append) any pre-existing `/tmp/0037_*.{before,after}.txt` so a re-run produces a clean snapshot. Print a one-line warning if previous output is overwritten.
- Cutover SQL: each `ALTER ROLE` is wrapped in `DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user1') THEN ALTER ROLE app_user1 RENAME TO research_app; END IF; END $$;` — re-running after a successful rename is a no-op, not an error.
- Rollback SQL: symmetric — uses `IF EXISTS (... rolname = 'research_app')` guard.
- DROP-dashboard-user SQL: `DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dashboard_user1') THEN DROP ROLE dashboard_user1; END IF; END $$;`.
- Smoke test: connect tests are inherently idempotent; just ensure they don't accumulate side-effects (write test creates and rolls back).
- Parity diff: re-runnable; reads existing snapshots and reports.

### Implementation Steps

1. **Write the runbook** as a numbered T-1..T+6 sequence. **The runbook must be self-contained: an operator can execute it without reading the spec.** Each step includes:
   - **Precondition**: SQL or shell command + expected output
   - **Action**: exact command(s) to run
   - **Verification**: SQL or shell command + expected output
   - **Failure branch**: the failure-mode decision tree from spec §"Rollback Plan §Decision tree by failure point" is copied INTO the runbook at the relevant step (not referenced — copied). Each step's failure section names the exact next action.

   Concrete branches that MUST be fully resolved in the runbook (not pointers):
   - **`dashboard_user1` decision**: the runbook contains the exact `psql` invocation to query state, the literal three-row decision table, and the next-action for each row.
   - **Privileged pre-check execution**: the runbook explicitly states which connection (master role) to use for steps 2/4b/4c, and which connection (master OR IAM-tier) to use for steps 1/3/4a/4d. The operator does not have to figure this out.
   - **Smoke-test write path**: the exact INSERT into `lava_pipeline.org_provenance` with EIN `ZZ-SMOKETEST-37` (per step 10 below) is in the runbook verbatim — the operator does not improvise the write target.

   The runbook copies content from spec §"Cutover sequence" verbatim, then expands placeholders to concrete values. The operator fills in `$RDS_ENDPOINT`, `$DB`, `ACCT`, and `DB-RES-ID` as environment variables before running.

2. **Write `0037-pre-rename-checks.sql`** with the four queries from spec §"Pre-rename checks" (steps 1, 2, 3, and 4a-4d). Each query has a header comment explaining its purpose and expected output. The file is operator-runnable via `psql -f`.

3. **Write `0037-snapshot-pre.sh`** — must run as the **RDS instance master role** (the bootstrap superuser), not as `research_app` or `research_ro`. Reason: snapshots 4b (`pg_default_acl`) and 4c (`pg_class.relowner`) read columns that require ownership-or-superuser access. Running as the IAM-tier role would silently return empty rows and mask privilege regressions.

   - Reads env vars: `RDS_ENDPOINT`, `DB`, `MASTER_USER` (the master role name), `MASTER_PW` (master password from SSM).
   - Connects via password auth (NOT IAM) — the master role uses password auth: `PGPASSWORD="$MASTER_PW" psql -h $RDS_ENDPOINT -U $MASTER_USER -d $DB --set=sslmode=require -v ON_ERROR_STOP=1`.
   - Runs each of the 7 snapshot queries with `psql -At` (tab-aligned, no headers). Overwrites pre-existing output files (idempotent).
   - Files produced (overwritten on each run): `grants_corpus.before.txt`, `grants_pipeline.before.txt`, `grants_dashboard.before.txt`, `default_acl.before.txt`, `ownership_objects.before.txt`, `ownership_schemas.before.txt`, `memberships.before.txt`.
   - Print one-line warning if previous output files existed and were overwritten.
   - Exits non-zero if any query fails.

4. **Write `0037-snapshot-post.sh`**: identical to `pre.sh` but writes `.after.txt` files. (Could be a single script with a `--mode pre|post` flag — builder's call.)

5. **Write `0037-parity-diff.sh`** — must cover ALL 7 produced files (4 dimensions, but ownership splits into objects+schemas and object-privileges splits across 3 schemas). The script iterates this exact list:

   ```bash
   FILES=(
     grants_corpus           # dim 4a: object privileges, lava_corpus
     grants_pipeline         # dim 4a: object privileges, lava_pipeline
     grants_dashboard        # dim 4a: object privileges, lava_dashboard
     default_acl             # dim 4b: pg_default_acl
     ownership_objects       # dim 4c: pg_class
     ownership_schemas       # dim 4c: pg_namespace
     memberships             # dim 4d: pg_auth_members
   )
   for f in "${FILES[@]}"; do
     sed 's/app_user1/research_app/g; s/ro_user1/research_ro/g' \
       "/tmp/0037_${f}.before.txt" \
       | diff -u - "/tmp/0037_${f}.after.txt"
     if [[ $? -ne 0 ]]; then echo "FAIL: $f"; FAIL=1; fi
   done
   exit ${FAIL:-0}
   ```
   - Print `PASS: <name>` or `FAIL: <name>` per file.
   - Exit non-zero if ANY file diff is non-empty.
   - This script implements AC #6 from the spec (privilege parity across all 7 outputs).

6. **Write `0037-iam-policy-overlay.json`**: a JSON IAM policy document with `Statement` containing `Action: rds-db:connect` and `Resource` listing all FOUR ARNs (old × 2 + new × 2). Operator applies this at T-1.

7. **Write `0037-iam-policy-final.json`**: the post-cleanup version listing only the two new ARNs. Operator applies at T+6.

8. **Write `0037-cutover-rename.sql`** with idempotency guards:
   ```sql
   BEGIN;
   DO $$ BEGIN
     IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user1') THEN
       ALTER ROLE app_user1 RENAME TO research_app;
     END IF;
     IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ro_user1') THEN
       ALTER ROLE ro_user1 RENAME TO research_ro;
     END IF;
   END $$;
   COMMIT;
   ```
   The `dashboard_user1` HALT branch is enforced in the runbook (operator stops if pre-check finds it with grants); this SQL file does NOT touch dashboard_user1. The DROP path lives in a separate file (step 8b below) so the operator runs it explicitly only when pre-check directs.

8b. **Write `0037-cutover-dashboard-user-drop.sql`** — operator runs ONLY if pre-check determines `dashboard_user1` exists AND has zero GRANTs AND zero non-`rds_iam` memberships. The SQL re-validates BOTH conditions before dropping (defense-in-depth — operator could misinterpret pre-check):
   ```sql
   DO $$
   DECLARE
     v_grant_count int;
     v_owned_count int;
     v_nonimg_member_count int;
   BEGIN
     IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dashboard_user1') THEN
       RAISE NOTICE 'dashboard_user1 does not exist — no-op';
       RETURN;
     END IF;

     -- Check 1: zero non-rds_iam memberships
     SELECT count(*) INTO v_nonimg_member_count
     FROM pg_auth_members am
     JOIN pg_roles r ON r.oid = am.member
     JOIN pg_roles m ON m.oid = am.roleid
     WHERE r.rolname = 'dashboard_user1' AND m.rolname <> 'rds_iam';

     IF v_nonimg_member_count > 0 THEN
       RAISE EXCEPTION 'dashboard_user1 has % non-rds_iam memberships — '
                       'HALT per spec §"dashboard_user1 decision rule"',
                       v_nonimg_member_count;
     END IF;

     -- Check 2: zero object-privilege grants on the role across all schemas
     -- (covers tables, sequences, functions, schemas).
     SELECT count(*) INTO v_grant_count
     FROM (
       SELECT 1 FROM information_schema.table_privileges
         WHERE grantee = 'dashboard_user1'
       UNION ALL
       SELECT 1 FROM information_schema.routine_privileges
         WHERE grantee = 'dashboard_user1'
       UNION ALL
       SELECT 1 FROM information_schema.usage_privileges
         WHERE grantee = 'dashboard_user1'
     ) g;

     IF v_grant_count > 0 THEN
       RAISE EXCEPTION 'dashboard_user1 has % object-privilege grants — '
                       'HALT per spec §"dashboard_user1 decision rule"',
                       v_grant_count;
     END IF;

     -- Check 3: zero objects owned by the role (defense-in-depth — DROP ROLE
     -- on an owner would fail anyway, but raise a clearer message).
     SELECT count(*) INTO v_owned_count
     FROM pg_class c JOIN pg_roles r ON c.relowner = r.oid
     WHERE r.rolname = 'dashboard_user1';

     IF v_owned_count > 0 THEN
       RAISE EXCEPTION 'dashboard_user1 owns % objects — HALT', v_owned_count;
     END IF;

     -- All three defense-in-depth checks passed; safe to drop.
     DROP ROLE dashboard_user1;
     RAISE NOTICE 'dashboard_user1 dropped (was vestigial)';
   END $$;
   ```

9. **Write `0037-cutover-rollback.sql`** — symmetric reverse with idempotency guards:
   ```sql
   BEGIN;
   DO $$ BEGIN
     IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'research_app') THEN
       ALTER ROLE research_app RENAME TO app_user1;
     END IF;
     IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'research_ro') THEN
       ALTER ROLE research_ro RENAME TO ro_user1;
     END IF;
   END $$;
   COMMIT;
   ```
   Run only if rollback decision tree directs full rollback (per spec §"Rollback Plan §Decision tree").

10. **Write `0037-smoke-test.sh`** with concrete, named queries (no operator improvisation):

    For each `(role, role_purpose)` in `[(research_ro, "read-only SELECT"), (research_app, "CRUD")]`:
    - Generate IAM token: `aws rds generate-db-auth-token --hostname $RDS_ENDPOINT --port 5432 --username $role --region us-east-1`
    - Connect: `PGPASSWORD=$tok psql -h $RDS_ENDPOINT -U $role -d $DB --set=sslmode=require -v ON_ERROR_STOP=1`
    - Identity check: `SELECT current_user, 1 AS ok` — expect a single row with `current_user = $role`.
    - Read check (both roles): `SELECT count(*) FROM lava_corpus.corpus LIMIT 1` — expect a row with a non-negative integer (does NOT need rows to exist; just proves SELECT permission).

    For `research_app` only, ALSO run a write check using a transactional INSERT that is GUARANTEED to roll back regardless of operator error:
    ```sql
    BEGIN;
    INSERT INTO lava_pipeline.org_provenance (ein, seed_status, updated_at)
      VALUES ('ZZ-SMOKETEST-37', 'completed', NOW())
      ON CONFLICT (ein) DO NOTHING;
    -- Force rollback unconditionally with a deliberate error:
    ROLLBACK;
    ```
    Then verify (in a new connection): `SELECT count(*) FROM lava_pipeline.org_provenance WHERE ein = 'ZZ-SMOKETEST-37'` — expect `0`. This proves:
    (a) `research_app` has INSERT permission on `lava_pipeline.org_provenance` (otherwise the INSERT would fail).
    (b) The ROLLBACK was honored (no smoke-test row leaked into production data).
    (c) The session is connected as the right role with the right grants.

    The script exits 0 ONLY if all checks pass. Any failure → exit non-zero with a one-line description of which check failed.

    **Why `lava_pipeline.org_provenance` and EIN `ZZ-SMOKETEST-37`?** It's a real production table (so we test real grants), it has INSERT permission for `research_app` per `migration_011_990_index_automation.sql`, and the EIN format `ZZ-...` is invalid (real EINs are 9 digits) — even if the rollback somehow fails, downstream code that filters by valid EIN format will ignore the smoke row.

### Phase 2 Acceptance

- All 10 files created under `locard/operations/0037-*` (the 9 originally listed plus `0037-cutover-dashboard-user-drop.sql`).
- `bash -n locard/operations/*.sh` (syntax check) passes for each shell script.
- **Pre-rename-checks SQL caveat**: the file contains queries with two privilege tiers. Steps 1, 3, 4a, 4d run as any role (validate against plain Postgres ≥14). Steps 2, 4b, 4c require master/`rds_superuser` access; on plain Postgres they require running as the bootstrap superuser. Acceptance: `psql -v ON_ERROR_STOP=1 -f locard/operations/0037-pre-rename-checks.sql` against a fresh local Postgres run as `postgres` superuser must complete without error. (RDS execution at cutover time has the same effective privilege level.)
- The runbook references each helper script by relative path; no orphan or broken references.
- IAM policy JSONs validate against the AWS IAM policy schema (use `aws iam validate-policy-document` if available, otherwise manual review).
- All 5 SQL/shell scripts that mutate state are demonstrably idempotent: re-running each one twice in succession against the same DB produces no error and no second-effect (verified in scratch DB).

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
   # Spec AC #8 requires only CREATE ROLE — no rds_iam grant. If any
   # migration depends on rds_iam membership, the migration fails here
   # and that's a real bootstrap-script gap to surface.
   psql "host=localhost port=5499 user=postgres dbname=lava_test" \
     -c "CREATE ROLE research_app; CREATE ROLE research_ro;"
   for f in lavandula/migrations/rds/{001,002,003,004,005,006,007,008,009,010,011,012,013,014}_*.sql; do
     psql "host=localhost port=5499 user=postgres dbname=lava_test" \
       -v ON_ERROR_STOP=1 -f "$f" \
       || { echo "FAIL: $f"; exit 1; }
   done
   docker stop pg37
   ```
   All 14 migrations must apply successfully. If a migration requires `rds_iam` (an AWS-RDS-only role that doesn't exist on plain Postgres), the builder records this as a bootstrap-script gap in the PR and adds `CREATE ROLE rds_iam;` to the scratch setup. Do not silently pre-grant — surface the issue.

3. **Exercise helper scripts end-to-end against scratch DB**. Syntax-checking is not enough; the scripts must actually run against a real Postgres instance. Specifically:
   - Run `0037-snapshot-pre.sh` against the scratch DB (with the renamed roles created) — verify all 7 `.before.txt` files are produced with expected content.
   - Run `0037-cutover-rename.sql` TWICE against the scratch DB (first time renames; second time is a no-op due to idempotency guards). Confirm second run produces no error.
   - Run `0037-snapshot-post.sh` against the scratch DB — verify all 7 `.after.txt` files are produced.
   - Run `0037-parity-diff.sh` — expect PASS on all 7 dimensions (renames preserve grants, default ACLs, ownership, memberships).
   - Run `0037-cutover-rollback.sql` — verify it reverses the rename. Run a SECOND time — verify it's a no-op.
   - Run `0037-cutover-dashboard-user-drop.sql` against scratch DB where `dashboard_user1` doesn't exist — verify it raises NOTICE and exits 0 (no-op branch).
   - (Optional but recommended) Create a fake `dashboard_user1` in scratch DB with grants, run drop SQL — verify it RAISEs EXCEPTION (HALT branch).

4. **Document results** in the PR description: paste the test summary lines, the migration loop's "all OK" output, and the helper-script exercise output. The reviewer should be able to read the PR description and see all checks passed without re-running them.

### Phase 3 Acceptance

- `pytest` exit code 0 for both test invocations.
- Scratch-DB migration loop applies all 14 migrations to completion (exit 0 from the for-loop).
- All 6 helper-script exercises pass against scratch DB:
  - snapshot-pre/snapshot-post produce all 7 expected files
  - cutover-rename idempotent (second run no-op)
  - parity-diff PASSes all 7 dimensions
  - cutover-rollback reverses cleanly and is itself idempotent
  - dashboard-user-drop is a no-op when role absent; RAISEs when role has grants/memberships
- PR description contains the test output summary, migration validation output, and helper-script exercise output.

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

1. **Builder merges PR** after operator confirms Phase 4 acceptance. Ordering matters: live cutover MUST happen first, source-tree edits MUST be merged within the same maintenance window.

2. **Source-tree handling on rollback** (per spec §"Rollback Plan §Source-tree handling during rollback"). If Phase 4 triggers full rollback, the builder applies one of two policies based on rollback duration:
   - **Short rollback (<4 hours, retry scheduled in same maintenance window)**: leave PR/edits in place if already merged; merge if not yet merged. Runtime reads from SSM, so source-tree leading production is harmless during a brief window.
   - **Extended rollback (overnight or longer, no retry scheduled)**: `git revert` the merged PR. Reason: a fresh-environment build (new dev EC2, new scratch DB) would create the new role names while production runs old names — silent divergence. Revert restores source-tree truth to match production.

   The builder records which policy was applied and why in the cutover log.

3. **Builder writes lessons-learned entry** in `locard/resources/lessons-learned.md` if any deviations from the runbook occurred during cutover. Examples worth recording:
   - IAM propagation took longer/shorter than expected
   - Connection drain required `pg_terminate_backend` (sessions didn't close cleanly)
   - Privilege-parity diff revealed unexpected differences (and how they were resolved)
   - `dashboard_user1` branch hit any state other than "absent"
   - Scratch-DB migration validation surfaced a missing `rds_iam` dependency

4. **Builder writes review document** at `locard/reviews/0037-rds-role-rename.md` summarizing:
   - Outage duration (actual vs estimated 3-10 min)
   - All 6 AC items from spec verified
   - Any changes made during execution (deviations from plan)
   - Sign-off: builder + operator

5. **Operator updates project status** in `locard/projectlist.md` from `planned` → `implementing` → `implemented` → `committed` → `integrated` as the work progresses.

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

## Consultation Log

- **Codex (plan-review)**: REQUEST_CHANGES — 6 findings: (1) idempotency required only in prose runbook, not in helper SQL/scripts — added DO/IF EXISTS guards to every state-mutating SQL; (2) `dashboard_user1` HALT branch had only a comment in cutover-rename.sql — moved to its own file `0037-cutover-dashboard-user-drop.sql`; (3) parity-diff iterated vague "4 dimensions" — expanded to enumerate all 7 produced files (3 grants + 1 default ACL + 2 ownership + 1 memberships); (4) Phase 3 scratch DB pre-granted `rds_iam` not in spec AC #8 — removed; if a migration depends on `rds_iam`, surface it as a real bootstrap-script gap; (5) Phase 2 acceptance for pre-rename-checks.sql said "any Postgres ≥14 runs cleanly" but step 2 needs `rds_superuser` — distinguished privileged vs non-privileged execution context; (6) Phase 5 missing the spec's short-vs-extended source-tree rollback policy — added
- **Gemini (plan-review)**: Rate-limited (API quota exhausted)

All findings addressed in revision 2.

- **Codex (red-team-plan)**: REQUEST_CHANGES — 5 findings: (1) HIGH: `0037-cutover-dashboard-user-drop.sql` checked memberships but not GRANTs — expanded to 3 defense-in-depth checks (zero non-`rds_iam` memberships, zero object-privilege grants across `information_schema.table_privileges`/`routine_privileges`/`usage_privileges`, zero owned objects); each RAISEs EXCEPTION with count if fails; (2) HIGH: smoke-test write path underspecified — named exact INSERT into `lava_pipeline.org_provenance` with EIN `ZZ-SMOKETEST-37` (invalid format so downstream code rejects even if rollback fails); (3) MEDIUM: snapshot scripts used single IAM_USER but `pg_default_acl` and `pg_class.relowner` require master/superuser — clarified scripts must connect as RDS master via password auth, not IAM; (4) MEDIUM: Phase 1 grep scope mismatched between step 2 (`migrations/+tests/`) and acceptance (`lavandula/` tree-wide) — aligned both; (5) MEDIUM: runbook self-containment — required `dashboard_user1` decision table, privileged-execution role choice, and smoke-test write path to be COPIED into the runbook, not pointers to spec
- **Gemini (red-team-plan)**: Rate-limited (API quota exhausted)

Plan also added end-to-end helper-script exercise to Phase 3 (not just `bash -n` syntax checks) including double-run idempotency proof.

All findings addressed in revision 3.

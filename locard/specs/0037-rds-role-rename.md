# Spec 0037: Rename RDS Roles to Project-Prefixed Names

## Problem Statement

A second project will share the existing RDS Postgres instance. Current Lavandula roles use generic names — `app_user1` (CRUD) and `ro_user1` (read-only) — that conflict with any sensible role naming the new project would choose for its own CRUD/RO pair. We need to namespace Lavandula's roles so each project owns a clearly-scoped role-pair under a recognizable prefix, and the new project can stand up its own role-pair without collision.

Schemas already carry a project prefix (`lava_corpus`, `lava_dashboard`, `lava_pipeline`) so they need no rename — Postgres schemas already provide the namespace they were designed for. **Only the role names need fixing.**

## Goals

### Must Have

1. **Project-prefixed role names.** `app_user1` → `lavandula_app`, `ro_user1` → `lavandula_ro`. The new project will independently create `<newproj>_app`, `<newproj>_ro` under its own SSM path; this spec does not provision or touch the new project's roles.

2. **Zero production-code change in the abstraction layer.** Usernames already flow through SSM keys (`rds-app-user`, `rds-ro-user`). The KEYS stay the same; only the VALUES change. This means `lavandula/common/db.py` and `lavandula/dashboard/dashboard/settings.py` are NOT edited.

3. **All grants, ownerships, and default-privilege rules survive the rename.** Postgres ties these to role OIDs, not names — `ALTER ROLE … RENAME TO …` preserves them automatically. Spec validates this in the acceptance criteria; no manual re-granting required.

4. **IAM authentication continues to work post-rename.** RDS IAM auth requires (a) the role has `rds_iam` membership and (b) the calling principal has `rds-db:connect` on a resource ARN that contains the username. The `rds_iam` membership follows the rename automatically; the IAM resource ARN does not. The IAM policy attached to the EC2 instance profile must be updated as part of this work.

5. **Coordinated cutover with brief outage.** The dashboard and any long-running CLI processes hold username strings in-memory (via `lru_cache` on `get_secret`). They must be restarted after SSM is updated. The rename window is a small operator-coordinated outage (minutes), not a long migration.

6. **Source-tree consistency.** The 6 source files that hardcode the old role names (4 SQL migrations + 2 test files) are updated so that fresh database rebuilds, new migration runs, and the test suite all reference the renamed roles.

### Should Have

7. **Idempotent runbook.** Each step in the runbook can be re-run without harm. If the operator is interrupted partway, restarting the runbook from the top either no-ops past the completed steps or fails fast with a clear diagnostic.

8. **Verified rollback path.** If anything fails, the operator can `ALTER ROLE … RENAME TO …` back to the old name and restore the previous SSM values within minutes.

### Out of Scope

- **Schema rename.** The `lava_*` schemas already carry a project prefix and are out of scope. See Spec 0037 background analysis (this document, §"Why not rename schemas").
- **Provisioning the new project's roles.** The new project gets its own role-pair under its own SSM path. That work belongs to that project's own spec.
- **Password rotation.** IAM auth is in use; no password to rotate. (See §"Pre-rename checks" for the password-encryption verification step that confirms this.)
- **Cross-project access patterns.** Whether the new project's roles can read Lavandula tables (or vice-versa) is a separate policy decision and is explicitly NOT addressed here. After this rename, both projects' roles remain isolated as today.
- **A schema-name abstraction layer.** Adding `_SCHEMA = get_secret("rds-schema")` indirection across ~170 SQL strings is a separate refactor (Option C in the inventory analysis) and is NOT part of this work.

## Background / Current State

### Current roles

```
app_user1   -- CRUD on lava_corpus, lava_pipeline, lava_dashboard; member of rds_iam
ro_user1    -- SELECT on lava_corpus tables; member of rds_iam
postgres    -- AWS-managed superuser (untouched by this work)
```

There is no separate `dashboard_user1` despite that name appearing colloquially — the dashboard reuses `app_user1`. Confirmed by inventory: zero references to `dashboard_user1` in code or migrations.

### How usernames flow today

```
SSM (/cloud2.lavandulagroup.com/rds-app-user = "app_user1")
   ↓
get_secret("rds-app-user") → "app_user1"   [lru_cache'd, per-process]
   ↓
make_app_engine() / Django settings.py → SQLAlchemy/psycopg2 connect
   ↓
IAMTokenManager.token() with DBUsername="app_user1" → 15-min auth token
   ↓
Connect to RDS as app_user1
```

The username is read at process start, cached in `lru_cache` for the process lifetime, and used for both the connection string AND the IAM token's `DBUsername` field. Changing the SSM value alone is not sufficient — running processes will keep using the cached old value until restart.

### Inventory of source-tree references

Files that hardcode `app_user1` or `ro_user1` (must be edited as part of this work):

| File | References | Reason |
|---|---|---|
| `lavandula/migrations/rds/001_initial_schema.sql` | 8 | GRANT + default-privilege statements |
| `lavandula/migrations/rds/002_attribution_helper.sql` | 1 | GRANT EXECUTE |
| `lavandula/migrations/rds/010_990_people_filing_index.sql` | 5 | GRANT statements |
| `lavandula/migrations/rds/migration_011_990_index_automation.sql` | 7 | GRANT statements |
| `lavandula/common/tests/conftest.py` | 2 | Fixture role-presence assertion |
| `lavandula/common/tests/unit/test_db_adapter_0013.py` | 11 | Hardcoded test strings |

Files that mention old names in narrative prose (lower priority; update for historical accuracy or leave with a note):

| File | Treatment |
|---|---|
| `locard/specs/0013-rds-postgres-migration.md` | Add a footnote: "Roles renamed to `lavandula_app`/`lavandula_ro` per Spec 0037 (2026-05-XX)." Do not rewrite history. |
| `locard/specs/0017-retire-sqlite.md` | Same. |
| `locard/plans/0013-phase2-backfill.md` | Same. |
| `locard/plans/0017-retire-sqlite.md` | Same. |

### Why not rename schemas

170 references to `lava_corpus` exist across 30+ source files. Schema names are NOT abstracted: they appear inline in SQL string literals (`"FROM lava_corpus.corpus"`), as module-level constants (`_SCHEMA = "lava_corpus"`), and as Django Meta `db_table` attributes. There IS an `rds-schema` SSM key, but it only controls `SET search_path` — it does not drive query construction. A schema rename would require ~170 file edits with high risk of missed references (SQL strings are not type-checked).

The `lava_*` prefix already namespaces the schemas. The collision risk is on role names alone. Schemas are explicitly out of scope.

## Proposed Naming

| Old | New | Justification |
|---|---|---|
| `app_user1` | `lavandula_app` | Matches the package directory (`lavandula/`) and conceptually aligns with the `lava_*` schema prefix. |
| `ro_user1` | `lavandula_ro` | Same. The `_ro` suffix is standard PG convention for read-only roles. |

The numeric `1` suffix is dropped — it implied a sequence (`app_user2`?) that never existed.

**Decision pending:** Operator confirms `lavandula` as the prefix at spec-approval time. Alternative: `lava` (matches the schemas exactly but loses the package-name match). Recommendation: `lavandula`.

## Migration Strategy

### Pre-rename checks (operator runs before any change)

**Operator privilege assumption.** Steps 1, 3, 4 below run as any role with `rds_iam` access. Step 2 reads `pg_authid` and requires the RDS instance master role (the one created at instance provisioning) or a member of `rds_superuser`. If the operator does not have that access, **skip step 2** — IAM auth does not use stored passwords, so password algorithm is informational only. Mark the skip in the runbook log.

```sql
-- 1. Determine the universe of roles to act on. dashboard_user1 may or may
--    not exist; behavior branches below based on what this query returns.
SELECT rolname FROM pg_roles WHERE rolname IN ('app_user1','ro_user1','dashboard_user1');

-- 2. (OPTIONAL — requires master/rds_superuser) Confirm SCRAM password
--    encryption. Rename invalidates MD5-hashed passwords (MD5 includes the
--    username); SCRAM is unaffected. IAM auth doesn't use stored passwords,
--    so this check is purely informational.
SELECT rolname,
       CASE
         WHEN rolpassword IS NULL THEN '(no stored password — IAM-only)'
         WHEN rolpassword LIKE 'SCRAM-%' THEN 'SCRAM (rename-safe)'
         WHEN rolpassword LIKE 'md5%' THEN 'MD5 (rename invalidates password)'
         ELSE 'unknown'
       END AS password_status
FROM pg_authid
WHERE rolname IN ('app_user1','ro_user1','dashboard_user1');

-- 3. Confirm rds_iam membership (must follow the rename automatically).
SELECT r.rolname, m.rolname AS member_of
FROM pg_roles r JOIN pg_auth_members am ON r.oid = am.member
JOIN pg_roles m ON am.roleid = m.oid
WHERE r.rolname IN ('app_user1','ro_user1','dashboard_user1') AND m.rolname = 'rds_iam';

-- 4. Snapshot grants AND memberships (for post-rename verification).
--    Save the output of each to a file; compare post-rename diffs must show
--    only role-name substitutions, never added/removed privileges.
\dp lava_corpus.*           > /tmp/grants_corpus.before.txt
\dp lava_pipeline.*         > /tmp/grants_pipeline.before.txt
\dp lava_dashboard.*        > /tmp/grants_dashboard.before.txt
SELECT r.rolname, m.rolname AS member_of
FROM pg_roles r JOIN pg_auth_members am ON r.oid = am.member
JOIN pg_roles m ON am.roleid = m.oid
WHERE r.rolname IN ('app_user1','ro_user1','dashboard_user1')
ORDER BY r.rolname, m.rolname; -- > /tmp/memberships.before.txt
```

### `dashboard_user1` decision rule (concrete, executed at pre-check time)

The query in step 1 returns one of three states. The runbook acts on the result without further deliberation:

| Step 1 returns | Action |
|---|---|
| Only `app_user1, ro_user1` | **Skip dashboard_user1 entirely.** Do not create, drop, or rename. Confirms the inventory finding. |
| Includes `dashboard_user1` AND it has GRANTs in step 4 snapshots | **Rename:** `ALTER ROLE dashboard_user1 RENAME TO lavandula_dashboard;` Add to SSM as `rds-dashboard-user` (new key) and update IAM resource ARN. **This expands scope** — operator must explicitly approve this branch before proceeding. |
| Includes `dashboard_user1` BUT it has zero GRANTs and zero memberships beyond `rds_iam` | **Drop:** `DROP ROLE dashboard_user1;` (vestigial — confirmed unused in code inventory). |

The runbook records which branch was taken in its execution log.

### Cutover sequence (operator-coordinated, idempotent)

Each step has an explicit precondition check. If the precondition shows the step is already done, the step is a no-op. If it shows mixed state, the runbook halts with a clear diagnostic — the operator must investigate, not auto-proceed.

```
T-1   IAM ADDITIVE OVERLAP (do this BEFORE the outage window).
      Edit the EC2 instance-profile policy to ALLOW BOTH old and new ARNs:
        arn:aws:rds-db:us-east-1:ACCT:dbuser/DB-RES-ID/app_user1
        arn:aws:rds-db:us-east-1:ACCT:dbuser/DB-RES-ID/ro_user1
        arn:aws:rds-db:us-east-1:ACCT:dbuser/DB-RES-ID/lavandula_app   ← new
        arn:aws:rds-db:us-east-1:ACCT:dbuser/DB-RES-ID/lavandula_ro    ← new
      Wait ≥5 minutes for IAM propagation.
      Verify: aws sts get-caller-identity (confirm role)
              aws iam simulate-principal-policy ...
              (or generate-db-auth-token --db-user lavandula_app and confirm
               token is issued — this only checks IAM, not the role exists yet)
      This step is reversible at any time by removing the new ARNs again.

T+0   PRECONDITION: confirm rename has not already happened.
        SELECT rolname FROM pg_roles
        WHERE rolname IN ('app_user1','ro_user1','lavandula_app','lavandula_ro');
      Expected: ('app_user1','ro_user1') only. If 'lavandula_app' present,
      jump to T+2 (rename already done). If both pairs present, HALT — mixed
      state requires manual investigation.

      Stop: dashboard (systemctl stop lavandula-dashboard)
      Stop: any running CLI processes (crawler, classifier, etc.) — confirm
            via `ps -ef | grep python3` and `af status`.
      Wait 30 seconds for connections to drain naturally.
      Forcibly terminate any sessions still owned by the old roles:
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE usename IN ('app_user1','ro_user1')
          AND pid <> pg_backend_pid();
      Re-check pg_stat_activity is clean before proceeding.

T+1   PRECONDITION: app_user1 and ro_user1 still exist (from T+0 query).
      Execute rename in a single transaction:
        BEGIN;
        ALTER ROLE app_user1 RENAME TO lavandula_app;
        ALTER ROLE ro_user1  RENAME TO lavandula_ro;
        -- (conditional, if dashboard_user1 branch chosen at pre-check)
        -- ALTER ROLE dashboard_user1 RENAME TO lavandula_dashboard;
        -- OR
        -- DROP ROLE dashboard_user1;
        COMMIT;
      Verify:
        SELECT rolname FROM pg_roles WHERE rolname LIKE 'lavandula\_%' ESCAPE '\';

T+2   PRECONDITION: read SSM current values; if already lavandula_*, skip.
        OLD_APP=$(aws ssm get-parameter --name /cloud2.lavandulagroup.com/rds-app-user --with-decryption --query Parameter.Value --output text)
        if [[ "$OLD_APP" != "lavandula_app" ]]; then
          aws ssm put-parameter --name /cloud2.lavandulagroup.com/rds-app-user \
            --type SecureString --value lavandula_app --overwrite
        fi
      Same for rds-ro-user.

T+3   No action — additive overlap was applied at T-1.
      (Old ARNs remain; they will be removed at T+6.)

T+4   PRECONDITION: psql connect test as the new roles works (see T+5 below).
      Start: dashboard (systemctl start lavandula-dashboard)
      Watch: journalctl -u lavandula-dashboard -f
      Wait up to 60s; if first connection fails with auth error, this is most
      likely IAM propagation lag — see Rollback Plan §"Decision tree".
      Hit a health endpoint to confirm DB connectivity.

T+5   Smoke tests (each must pass before T+6):
        - Dashboard home page loads
        - One read query (org list)
        - One write query (create a noop Job, then cancel it)
        - psql via IAM token as lavandula_ro: SELECT 1 FROM lava_corpus.corpus LIMIT 1
        - psql via IAM token as lavandula_app: same query

T+6   POST-CUTOVER CLEANUP (do this AFTER ≥30 minutes of stable operation).
      Remove old ARNs from the IAM policy:
        DELETE arn:aws:rds-db:...:dbuser/DB-RES-ID/app_user1
        DELETE arn:aws:rds-db:...:dbuser/DB-RES-ID/ro_user1
      Verify: an attempt to generate an IAM token for app_user1 should still
      succeed (token generation is local), but actual rds-db:connect with
      the old ARN must be denied.
      Source-tree edits committed; PR merged.
```

Estimated outage window (T+0 through T+5): **3–10 minutes**.
Total elapsed including IAM warm-up (T-1) and cleanup (T+6): hours, but only the cutover window is service-impacting.

### Why additive IAM overlap

An at-once swap of IAM resource ARNs creates a window where:
- The role is renamed (T+1) but the IAM policy still allows only the OLD name → all new connections fail until T+3 propagates.
- IAM propagation is non-deterministic (typically <1min, occasionally up to several minutes).

Additive overlap eliminates the IAM-propagation race entirely: at T-1 the policy allows BOTH names, and IAM is given as much time as the operator wants to settle. The actual cutover window only depends on PG (instant) and SSM (instant). At T+6, the now-orphan old ARNs are removed cleanly.

### Source-tree edits (committed in same PR as the runbook)

The 6 source files listed in §"Inventory" are edited via mechanical find/replace:

```
app_user1   →  lavandula_app
ro_user1    →  lavandula_ro
```

These edits do not affect the existing RDS database (the migration files have already been applied and won't re-run). Their purpose is fresh-database correctness: any new RDS instance built from `lavandula/migrations/rds/` will create the new role names directly.

Migrations 001, 002, 010, 011 do NOT need a new migration file (014 → 015) because the migrations create roles that are *expected* to already exist (they only GRANT to them). The `CREATE ROLE` statements live outside the migration system, in the original RDS bootstrap. **However**, if any environment re-applies these migrations against a fresh database, the GRANT-to-nonexistent-role would fail. This is a latent issue today and a known limitation; it is not introduced by this rename.

### Test-suite edits

`lavandula/common/tests/conftest.py` and `lavandula/common/tests/unit/test_db_adapter_0013.py` reference the names as fixture data and assertion strings. Mechanical find/replace, then run the test suite to confirm green.

## Code Changes (final list)

| File | Change |
|---|---|
| `lavandula/migrations/rds/001_initial_schema.sql` | s/app_user1/lavandula_app/g, s/ro_user1/lavandula_ro/g |
| `lavandula/migrations/rds/002_attribution_helper.sql` | Same |
| `lavandula/migrations/rds/010_990_people_filing_index.sql` | Same |
| `lavandula/migrations/rds/migration_011_990_index_automation.sql` | Same |
| `lavandula/common/tests/conftest.py` | Same |
| `lavandula/common/tests/unit/test_db_adapter_0013.py` | Same |
| `locard/operations/0037-role-rename-runbook.md` | NEW — operator-facing runbook copy of §"Cutover sequence" with environment-specific values filled in (account ID, DB resource ID). |

NOT edited (intentional):
- `lavandula/common/db.py` — uses SSM keys; values change in SSM, not in code.
- `lavandula/dashboard/dashboard/settings.py` — same.
- Any application code that issues queries — schemas are unchanged.

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| IAM policy not updated → all auth fails post-rename | Medium | High (full outage) | Make IAM policy edit a numbered step in the runbook with explicit verification (`aws sts get-caller-identity` + a test connect). Block the dashboard restart on this step. |
| `lru_cache` retains old SSM value in some forgotten daemon | Low | Medium (silent stale auth) | `ps -ef` audit before T+1; explicit list of services to stop in the runbook. |
| Active connection still using old role at rename time | Low | Low (existing session keeps working; no harm) | PG allows the rename with active sessions. The rename takes effect for *new* connections only, which is the desired behavior. |
| Stored MD5 password (vestigial) gets invalidated | Very Low | Low (IAM is the auth path) | Pre-rename check confirms password algorithm. If MD5, document but proceed — IAM auth doesn't depend on it. |
| Fresh DB rebuild fails because migration 001 expected old role names | Low | Medium (blocks new env) | Source files updated in same PR; verify by running the migration suite against a scratch RDS or local Postgres. |
| Test suite has hidden references not caught by grep (e.g., parameterized fixtures) | Low | Low (CI catches it) | Run full test suite in the PR before approval. |
| Rollback needed mid-cutover | Low | Low | `ALTER ROLE … RENAME TO …` is symmetric. SSM value rollback is `put-parameter --overwrite` with the old value. IAM policy rollback is one CloudFormation/console edit. Pre-cutover snapshot of `\du` output captures the previous state. |

## Acceptance Criteria

The work is complete when ALL of the following hold:

1. `\du` on RDS shows `lavandula_app` and `lavandula_ro`; no role named `app_user1` or `ro_user1` exists.
2. SSM parameter `/cloud2.lavandulagroup.com/rds-app-user` returns `lavandula_app`; `rds-ro-user` returns `lavandula_ro`.
3. EC2 instance profile IAM policy `rds-db:connect` resource ARNs reference `lavandula_app` and `lavandula_ro`. Old ARNs (`.../app_user1`, `.../ro_user1`) have been removed at T+6 (no overlap remains in steady state).
4. Dashboard is running and serving requests; `/orgs/`, `/jobs/`, and at least one write endpoint succeed.
5. Direct psql connect as `lavandula_ro` and `lavandula_app` (via IAM token) both succeed.
6. **Privilege parity (no regression).** The pre-rename grant snapshots (§"Pre-rename checks" step 4) compared against post-rename snapshots show ONLY role-name substitutions — no added, removed, or modified privileges, and no added or removed role memberships. A `diff` of the two snapshot sets, after sed-substituting the old names for the new, must be empty.
7. The 6 source files in §"Code Changes" have zero remaining `app_user1` or `ro_user1` references (verified by `grep -rn`).
8. **Targeted test verification** (NOT full suite — narrowed per-Codex feedback). The following must pass:
   - `pytest lavandula/common/tests/unit/test_db_adapter_0013.py` (touched fixtures + assertions).
   - `pytest lavandula/common/tests/conftest.py`-using tests in `lavandula/common/tests/` (fixture validity).
   - **Bootstrap + migration validation:** Against a scratch Postgres (local or ephemeral RDS), manually `CREATE ROLE lavandula_app; CREATE ROLE lavandula_ro;` then run all 14 migrations in order to completion. Confirms the SQL files reference the new names consistently.
9. Runbook `locard/operations/0037-role-rename-runbook.md` is committed alongside the source-tree PR.
10. Lessons-learned added to `locard/resources/lessons-learned.md` if anything unexpected was hit during cutover.

## Rollback Plan

### Decision tree by failure point

The right action depends on WHERE the failure happened. Rollback is symmetric but is not always the best first move — sometimes waiting or retrying is correct.

| Failure point | First action | If first action fails |
|---|---|---|
| **T-1** IAM additive overlap fails to apply | Investigate IAM permission. The old policy is unchanged; no service impact. No rollback needed. | Abort cutover; reschedule. |
| **T+0** Cannot stop services or terminate sessions cleanly | Diagnose stuck process; do not proceed to T+1 with active old-name connections. The rename works but cached usernames cause silent issues post-restart. | Abort cutover; reschedule. |
| **T+1** `ALTER ROLE` fails (lock, permission, role-not-found) | Read the error. If "role does not exist" — rename already happened (idempotent re-run). If "is being used" — drain again, retry. If permission denied — abort, escalate. No rollback needed at this point because nothing was renamed. | Abort. No rollback needed. |
| **T+1** Both renames succeed but transaction commit fails (extremely rare) | Re-check `pg_roles`. PG renames are atomic within the transaction. | If renames partially applied, manually `ALTER ROLE … RENAME TO …` to converge. |
| **T+2** SSM `put-parameter` fails | Retry up to 3 times. If still failing, revert via `ALTER ROLE … RENAME TO app_user1` etc. so the system is consistent (old name everywhere). | Full rollback (rename back, no SSM change since none succeeded). |
| **T+4** Dashboard fails to start with auth error | Check journalctl for specifics. Most common cause: IAM propagation lag (despite T-1 warm-up, edge case). **Wait 5 minutes, retry start.** If still failing after 10 minutes, run a manual `aws rds generate-db-auth-token --db-user lavandula_app` + `psql` to isolate. | If isolated to IAM/role: full rollback. If isolated to dashboard config: investigate code; revert role rename only if dashboard issue cannot be diagnosed quickly. |
| **T+5** Smoke test fails on read query | Likely IAM/grant issue. Compare pre/post grant snapshots (AC #6). If snapshots match, IAM propagation issue — wait 5min and retry. | Full rollback. |
| **T+5** Smoke test fails on write query but read succeeds | Targeted GRANT regression — investigate `\dp` on the affected table. Should not happen if AC #6 passes. | Full rollback. |

### Full-rollback procedure

```sql
-- Stop services first (same as T+0)
systemctl stop lavandula-dashboard
-- Drain leftover sessions
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE usename IN ('lavandula_app','lavandula_ro')
  AND pid <> pg_backend_pid();
-- Reverse rename
BEGIN;
ALTER ROLE lavandula_app RENAME TO app_user1;
ALTER ROLE lavandula_ro  RENAME TO ro_user1;
COMMIT;
```

```bash
aws ssm put-parameter --name /cloud2.lavandulagroup.com/rds-app-user \
    --type SecureString --value app_user1 --overwrite
aws ssm put-parameter --name /cloud2.lavandulagroup.com/rds-ro-user \
    --type SecureString --value ro_user1  --overwrite
systemctl start lavandula-dashboard
```

IAM policy stays in additive-overlap state during rollback (both old and new ARNs allowed) — no IAM action needed for rollback. Old ARNs continue to authorize, which is what we want.

Source-tree edits: leave in (they're forward-only and don't break the live system) OR `git revert` if the PR has merged. Prefer leaving them — they document the intent and a future re-attempt only needs the cutover, not new edits.

Total rollback time: ~3 minutes from decision-to-rollback to dashboard-back-up.

## Open Questions / Decisions

1. **Prefix value (BLOCKING — must be resolved before approval).** Recommended: `lavandula`. Alternative: `lava` (matches schema prefix exactly). Operator decides at spec approval. This affects every source-file edit, every SSM value, every IAM ARN, and the runbook. **No builder work begins until the operator picks one.** Throughout this spec, `lavandula_*` is used as the placeholder; if the operator picks `lava_*`, the spec is updated globally before plan-writing begins.

2. **Vestigial `dashboard_user1` — RESOLVED.** Decision rule is now concrete (§"`dashboard_user1` decision rule"). The pre-check answers it deterministically: if absent, no-op; if present without grants, drop; if present with grants, rename and expand scope (operator approval required for that branch).

3. **Source-tree edits in same PR or separate?** Recommend **same PR** so the source tree never disagrees with the live database for more than the cutover window. The PR merges *after* the live cutover (so reviewers can see the runbook executed cleanly).

4. **Should this work create a `015_role_rename_documentation.sql` migration?** No. Migrations write to `lava_corpus.schema_version` to track schema evolution, but role renames are not schema events. Document the rename in the runbook + git log; do not pollute the migration sequence.

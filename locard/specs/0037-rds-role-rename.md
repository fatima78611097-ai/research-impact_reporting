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

```sql
-- 1. Confirm no dashboard_user1 exists. If it does, decide whether to drop it
--    (likely vestigial) or rename it consistent with the others.
SELECT rolname FROM pg_roles WHERE rolname IN ('app_user1','ro_user1','dashboard_user1');

-- 2. Confirm SCRAM password encryption (rename invalidates MD5-hashed passwords
--    because MD5 includes the username; SCRAM is unaffected). IAM auth doesn't
--    use stored passwords, but if any fallback password ever was set this matters.
SELECT rolname,
       CASE
         WHEN rolpassword IS NULL THEN '(no stored password — IAM-only)'
         WHEN rolpassword LIKE 'SCRAM-%' THEN 'SCRAM (rename-safe)'
         WHEN rolpassword LIKE 'md5%' THEN 'MD5 (rename invalidates password)'
         ELSE 'unknown'
       END AS password_status
FROM pg_authid
WHERE rolname IN ('app_user1','ro_user1');

-- 3. Confirm rds_iam membership (must follow the rename automatically).
SELECT r.rolname, m.rolname AS member_of
FROM pg_roles r JOIN pg_auth_members am ON r.oid = am.member
JOIN pg_roles m ON am.roleid = m.oid
WHERE r.rolname IN ('app_user1','ro_user1') AND m.rolname = 'rds_iam';

-- 4. Snapshot grants (for post-rename verification).
\dp lava_corpus.*
\dp lava_pipeline.*
\dp lava_dashboard.*
```

### Cutover sequence (operator-coordinated)

```
T+0   Stop: dashboard (systemctl stop lavandula-dashboard)
      Stop: any running CLI processes (crawler, classifier, etc.) — confirm
            via `ps -ef | grep python3` and `af status`.
      Confirm no active connections held by app_user1/ro_user1:
            SELECT pid, usename, state FROM pg_stat_activity
            WHERE usename IN ('app_user1','ro_user1');

T+1   ALTER ROLE app_user1 RENAME TO lavandula_app;
      ALTER ROLE ro_user1  RENAME TO lavandula_ro;

T+2   Update SSM values:
        aws ssm put-parameter --name /cloud2.lavandulagroup.com/rds-app-user \
            --type SecureString --value lavandula_app --overwrite
        aws ssm put-parameter --name /cloud2.lavandulagroup.com/rds-ro-user \
            --type SecureString --value lavandula_ro  --overwrite

T+3   Update IAM policy on EC2 instance profile (cloud2_lavandulagroup):
        Replace resource ARN suffixes:
          arn:aws:rds-db:us-east-1:ACCT:dbuser/DB-RES-ID/app_user1
          arn:aws:rds-db:us-east-1:ACCT:dbuser/DB-RES-ID/ro_user1
        with:
          arn:aws:rds-db:us-east-1:ACCT:dbuser/DB-RES-ID/lavandula_app
          arn:aws:rds-db:us-east-1:ACCT:dbuser/DB-RES-ID/lavandula_ro
      IAM propagation is typically <1min but can be up to ~5min.

T+4   Start: dashboard (systemctl start lavandula-dashboard)
      Watch journalctl -u lavandula-dashboard -f for connect errors.
      Hit /healthz (or equivalent) to confirm DB connectivity.

T+5   Smoke tests:
        - Dashboard home page loads
        - One read query (org list)
        - One write query (e.g., create a noop Job, then cancel it)
        - psql as lavandula_ro: SELECT 1 FROM lava_corpus.corpus LIMIT 1
        - psql as lavandula_app: same query
```

Estimated outage: 3–10 minutes including verification.

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
3. EC2 instance profile IAM policy `rds-db:connect` resource ARNs reference `lavandula_app` and `lavandula_ro` (no references to `app_user1` or `ro_user1` remain).
4. Dashboard is running and serving requests; `/orgs/`, `/jobs/`, and at least one write endpoint succeed.
5. Direct psql connect as `lavandula_ro` and `lavandula_app` (via IAM token) both succeed.
6. Each grant snapshot taken pre-rename matches the post-rename grant snapshot, with only the role name differing. (i.e., no privilege regression.)
7. The 6 source files in §"Code Changes" have zero remaining `app_user1` or `ro_user1` references (verified by `grep -rn`).
8. Full test suite passes in the PR (no hidden references).
9. Runbook `locard/operations/0037-role-rename-runbook.md` is committed.
10. Lessons-learned added to `locard/resources/lessons-learned.md` if anything unexpected was hit during cutover.

## Rollback Plan

If at any step T+1 through T+5 something fails, rollback is symmetric:

```sql
ALTER ROLE lavandula_app RENAME TO app_user1;
ALTER ROLE lavandula_ro  RENAME TO ro_user1;
```

```bash
aws ssm put-parameter --name /cloud2.lavandulagroup.com/rds-app-user \
    --type SecureString --value app_user1 --overwrite
aws ssm put-parameter --name /cloud2.lavandulagroup.com/rds-ro-user \
    --type SecureString --value ro_user1  --overwrite
```

IAM policy: revert the resource ARN edit (keep the prior version cached locally before the change).

Restart dashboard. Total rollback time: ~3 minutes.

The source-tree edits can stay in (they are forward-only and don't break anything operationally) or be reverted via `git revert` of the PR — whichever is cleaner given where the rollback was triggered.

## Open Questions / Decisions

1. **Prefix value.** Recommended: `lavandula`. Alternative: `lava` (matches schema prefix exactly). Operator decides at spec approval. *(Affects all 6 source-file edits and the SSM values.)*

2. **Vestigial `dashboard_user1`.** If `\du` shows it exists, decide: drop it (`DROP ROLE`) or rename it for symmetry (`lavandula_dashboard`)? Recommend **drop** if it has no GRANTs and no `pg_authid.rolconfig` entries. *(Pre-rename check answers this.)*

3. **Source-tree edits in same PR or separate?** Recommend **same PR** so the source tree never disagrees with the live database for more than the cutover window. The PR merges *after* the live cutover (so reviewers can see the runbook executed cleanly).

4. **Should this work create a `015_role_rename_documentation.sql` migration?** No. Migrations write to `lava_corpus.schema_version` to track schema evolution, but role renames are not schema events. Document the rename in the runbook + git log; do not pollute the migration sequence.

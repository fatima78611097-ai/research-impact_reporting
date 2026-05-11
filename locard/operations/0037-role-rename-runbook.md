# Runbook: Spec 0037 — Rename RDS Roles

**Purpose**: Rename `app_user1` → `research_app` and `ro_user1` → `research_ro` on the shared RDS instance.

**Estimated outage**: 3–10 minutes (T+0 through T+5). Total elapsed including IAM warm-up and cleanup: hours, but only the cutover window is service-impacting.

**Prerequisites**:
- Operator has master/`rds_superuser` access to the RDS instance and `aws` CLI configured with permissions to edit IAM policies and SSM parameters.
- No other applications/repos connect to this shared RDS using the role names `app_user1`/`ro_user1` directly (all consumers go through SSM).
- **Scheduled tasks**: Run `crontab -l` and `systemctl list-timers --all`. Confirm the cutover window does not overlap any scheduled job (e.g., `load_990_index` at 03:00 UTC). Disable affected cron entries or systemd timers for the duration of the cutover window. Re-enable after T+5 confirms success.

---

## Environment Variables

Set these before starting. All scripts in `locard/operations/0037-*` read from these.

```bash
export RDS_ENDPOINT="<your-rds-endpoint>"
export DB="<your-database-name>"
export MASTER_USER="postgres"
export MASTER_PW="<master-password-from-ssm>"
export REGION="us-east-1"
# Fill in your AWS account ID and DB resource ID for IAM policy edits:
export ACCT="<aws-account-id>"
export DB_RES_ID="<rds-db-resource-id>"
```

---

## Pre-Rename Checks

Run **before scheduling the maintenance window**. These checks are non-destructive.

### Step P1: Run pre-rename checks

```bash
PGPASSWORD="$MASTER_PW" psql -h "$RDS_ENDPOINT" -U "$MASTER_USER" -d "$DB" \
  --set=sslmode=require -f locard/operations/0037-pre-rename-checks.sql
```

### Step P2: `dashboard_user1` decision

Examine the Step 1 output from `0037-pre-rename-checks.sql`:

| Step 1 returns | Action |
|---|---|
| Only `app_user1`, `ro_user1` | **Skip dashboard_user1 entirely.** Do not create, drop, or rename. This is the expected case. |
| Includes `dashboard_user1` BUT it has zero table GRANTs (Step 4a), zero function GRANTs (Step 4e), zero USAGE GRANTs (Step 4e), zero owned objects (Step 4e), and zero memberships beyond `rds_iam` (Step 3) | **Drop:** Run `psql -f locard/operations/0037-cutover-dashboard-user-drop.sql` at T+1 (after the rename). |
| Includes `dashboard_user1` AND any of: table GRANTs (Step 4a), function GRANTs (Step 4e), USAGE GRANTs (Step 4e), owned objects (Step 4e), or non-`rds_iam` memberships (Step 3) | **HALT.** Do NOT proceed with the cutover. Record the finding and write a follow-up spec. |

**Record which branch was taken:** ____________

### Step P3: Capture pre-rename snapshots

```bash
bash locard/operations/0037-snapshot-pre.sh
```

Verify all 8 files are produced:
```bash
ls -la /tmp/0037_*.before.txt
# Expected: 8 files (grants_corpus, grants_pipeline, grants_dashboard,
#   routine_privileges, default_acl, ownership_objects, ownership_schemas, memberships)
```

---

## T-1: IAM Additive Overlap (BEFORE the outage window)

**Goal**: Allow both old and new role ARNs in the IAM policy so there is no IAM propagation race during cutover.

### Action

1. Edit the EC2 instance-profile policy (`cloud2_lavandulagroup`) to use the contents of `locard/operations/0037-iam-policy-overlay.json`. Replace `ACCT` with your AWS account ID and `DB-RES-ID` with the RDS database resource ID.

2. Wait **≥5 minutes** for IAM propagation.

3. Verify IAM allows the new ARN (the role doesn't exist yet, so we expect a specific Postgres error, NOT an IAM error):

```bash
TOK=$(aws rds generate-db-auth-token --hostname "$RDS_ENDPOINT" \
      --port 5432 --username research_app --region us-east-1)
PGPASSWORD="$TOK" psql -h "$RDS_ENDPOINT" -U research_app -d "$DB" \
  --set=sslmode=require -c "SELECT 1" 2>&1 | tee /tmp/iam_warmup.log
```

**Expected output**: `FATAL: role "research_app" does not exist`
- This proves IAM is allowing the `rds-db:connect` call through.

**If output contains** `PAM authentication failed` or `permission denied to use this connection method`:
- IAM has NOT propagated yet. Wait another 2 minutes and retry.
- Do **NOT** proceed to T+0 until this check passes.

### Failure handling
This step is reversible at any time by removing the new ARNs from the IAM policy. No service impact — old ARNs still work.

---

## T+0: Stop Services and Drain Connections

**Precondition**: Confirm rename has not already happened.

```sql
SELECT rolname FROM pg_roles
WHERE rolname IN ('app_user1', 'ro_user1', 'research_app', 'research_ro');
```

Interpret the result:

| Query returns | Meaning | Action |
|---|---|---|
| Only `app_user1`, `ro_user1` | Normal pre-rename state | Proceed to T+0 actions below |
| Only `research_app`, `research_ro` | Rename already complete | Jump to T+2 (SSM update) |
| All four roles present | Mixed state — collision | **HALT.** Investigate per spec §Rollback Decision Tree |
| Any other mix (e.g., `research_app` + `ro_user1`) | Partial rename from a prior aborted run | Run T+1 anyway — the cutover SQL is idempotent and will complete the missing rename. Verify with the post-T+1 SELECT |

### Action

```bash
# Stop the dashboard and orchestrator
systemctl stop lavandula-dashboard
systemctl stop lavandula-orchestrator

# Stop any running CLI processes (crawler, classifier, etc.)
ps -ef | grep python3
af status
# Kill any lavandula processes still running

# Wait 30 seconds for connections to drain
sleep 30

# Forcibly terminate remaining sessions
PGPASSWORD="$MASTER_PW" psql -h "$RDS_ENDPOINT" -U "$MASTER_USER" -d "$DB" \
  --set=sslmode=require -c "
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE usename IN ('app_user1', 'ro_user1')
  AND pid <> pg_backend_pid();"

# Confirm clean
PGPASSWORD="$MASTER_PW" psql -h "$RDS_ENDPOINT" -U "$MASTER_USER" -d "$DB" \
  --set=sslmode=require -c "
SELECT pid, usename, state, query
FROM pg_stat_activity
WHERE usename IN ('app_user1', 'ro_user1');"
# Expected: 0 rows
```

### Failure handling
If services cannot stop or sessions won't terminate: diagnose the stuck process. Do NOT proceed to T+1 with active old-name connections. The rename works, but cached usernames cause silent issues post-restart. If unresolvable, abort cutover and reschedule.

---

## T+1: Execute Rename

**Precondition**: `app_user1` and `ro_user1` still exist (confirmed at T+0). No active sessions using those roles.

### Action

```bash
PGPASSWORD="$MASTER_PW" psql -h "$RDS_ENDPOINT" -U "$MASTER_USER" -d "$DB" \
  --set=sslmode=require -v ON_ERROR_STOP=1 \
  -f locard/operations/0037-cutover-rename.sql
```

**Expected output**:
```
NOTICE:  Renamed app_user1 → research_app
NOTICE:  Renamed ro_user1 → research_ro
 rolname
--------------
 research_app
 research_ro
(2 rows)
```

If pre-check Step P2 determined `dashboard_user1` should be DROPPED, also run:
```bash
PGPASSWORD="$MASTER_PW" psql -h "$RDS_ENDPOINT" -U "$MASTER_USER" -d "$DB" \
  --set=sslmode=require -v ON_ERROR_STOP=1 \
  -f locard/operations/0037-cutover-dashboard-user-drop.sql
```

### Failure handling

| Error | Action |
|---|---|
| `role "app_user1" does not exist` | Rename already happened (idempotent). Check `pg_roles` for `research_app` — if present, proceed to T+2. |
| `role is being used by other sessions` | Drain again (repeat T+0 termination), retry. |
| `permission denied` | Abort. Escalate. No rollback needed (nothing was renamed). |
| Transaction commit fails | Re-check `pg_roles`. PG renames are atomic within the transaction. If partial: manually converge with `ALTER ROLE`. |

---

## T+2: Update SSM Parameters

**Precondition**: Read current SSM values. If already `research_*`, skip.

```bash
OLD_APP=$(aws ssm get-parameter --name /cloud2.lavandulagroup.com/rds-app-user \
  --with-decryption --query Parameter.Value --output text)
echo "Current rds-app-user: $OLD_APP"

if [[ "$OLD_APP" != "research_app" ]]; then
  aws ssm put-parameter --name /cloud2.lavandulagroup.com/rds-app-user \
    --type SecureString --value research_app --overwrite
  echo "Updated rds-app-user → research_app"
else
  echo "rds-app-user already set to research_app — no-op"
fi

OLD_RO=$(aws ssm get-parameter --name /cloud2.lavandulagroup.com/rds-ro-user \
  --with-decryption --query Parameter.Value --output text)
echo "Current rds-ro-user: $OLD_RO"

if [[ "$OLD_RO" != "research_ro" ]]; then
  aws ssm put-parameter --name /cloud2.lavandulagroup.com/rds-ro-user \
    --type SecureString --value research_ro --overwrite
  echo "Updated rds-ro-user → research_ro"
else
  echo "rds-ro-user already set to research_ro — no-op"
fi
```

### Failure handling
If `put-parameter` fails: retry up to 3 times. If still failing, restore **both** SSM values to old names first (even if one already succeeded — idempotent), then revert the DB rename:
```bash
# 1. Restore SSM to old values (prevents split-brain if one update succeeded)
aws ssm put-parameter --name /cloud2.lavandulagroup.com/rds-app-user \
    --type SecureString --value app_user1 --overwrite
aws ssm put-parameter --name /cloud2.lavandulagroup.com/rds-ro-user \
    --type SecureString --value ro_user1 --overwrite

# 2. Revert DB rename
PGPASSWORD="$MASTER_PW" psql -h "$RDS_ENDPOINT" -U "$MASTER_USER" -d "$DB" \
  --set=sslmode=require -v ON_ERROR_STOP=1 \
  -f locard/operations/0037-cutover-rollback.sql
```
This ensures SSM and DB are consistent (old name everywhere) before restarting services.

---

## T+3: No Action

Additive IAM overlap was applied at T-1. Old ARNs remain; they will be removed at T+6.

---

## T+4: Verify IAM Connect + Restart Dashboard

**Precondition**: Actual IAM-authenticated psql connect as the new roles MUST succeed before starting the dashboard.

### Verify both roles

```bash
# research_app
TOK=$(aws rds generate-db-auth-token --hostname "$RDS_ENDPOINT" \
      --port 5432 --username research_app --region us-east-1)
PGPASSWORD="$TOK" psql -h "$RDS_ENDPOINT" -U research_app -d "$DB" \
  --set=sslmode=require -c "SELECT current_user, 1 AS ok"
# Expected: current_user = research_app, ok = 1

# research_ro
TOK=$(aws rds generate-db-auth-token --hostname "$RDS_ENDPOINT" \
      --port 5432 --username research_ro --region us-east-1)
PGPASSWORD="$TOK" psql -h "$RDS_ENDPOINT" -U research_ro -d "$DB" \
  --set=sslmode=require -c "SELECT current_user, 1 AS ok"
# Expected: current_user = research_ro, ok = 1
```

**If `PAM authentication failed`**: IAM propagation lag (despite T-1 warm-up). Wait 5 minutes, retry.
**If `role does not exist`**: Rename didn't apply — go back to T+1.
**If `permission denied for ...`**: Grants didn't follow OID — investigate. If unresolvable after 10 minutes, run full rollback.

### Restart services

```bash
systemctl start lavandula-dashboard
systemctl start lavandula-orchestrator
journalctl -u lavandula-dashboard -u lavandula-orchestrator -f
# Watch for 60 seconds. If auth error: likely IAM propagation lag — wait 5 min, retry start.
```

Hit a health endpoint to confirm DB connectivity.

---

## T+5: Smoke Tests

```bash
bash locard/operations/0037-smoke-test.sh
```

Additionally verify via browser:
- Dashboard home page loads
- Org list (`/orgs/`) loads
- One write operation (create a noop Job, then cancel it)

### Failure handling

| Symptom | Likely cause | Action |
|---|---|---|
| Read query fails | IAM/grant issue | Compare pre/post snapshots (AC #6). If snapshots match: IAM propagation — wait 5 min, retry. If not: full rollback. |
| Write query fails but read succeeds | Targeted GRANT regression | Investigate `\dp` on the affected table. Run full rollback if unresolvable. |

---

## Wait Period

Wait **≥30 minutes** of stable operation before proceeding to T+6.

During this time, run the post-rename snapshots and parity diff:

```bash
bash locard/operations/0037-snapshot-post.sh

# If dashboard_user1 was DROPPED at T+1, use --dashboard-dropped to filter
# its rows from the before-snapshot (intentional deletion, not a regression):
bash locard/operations/0037-parity-diff.sh              # normal case
bash locard/operations/0037-parity-diff.sh --dashboard-dropped  # if dashboard_user1 was dropped
# Expected: PASS on all 8 dimensions
```

If ANY dimension shows FAIL, investigate before removing old ARNs. A FAIL means privilege regression — potential grounds for full rollback.

---

## T+6: Remove Old IAM ARNs (Post-Cutover Cleanup)

**Only after ≥30 minutes of stable operation and parity-diff PASSing all 8 dimensions.**

### Action

Replace the EC2 instance-profile policy with the contents of `locard/operations/0037-iam-policy-final.json`. Replace `ACCT` and `DB-RES-ID` with your values.

### Verify

```bash
# Token generation is local and never fails — we need to test actual connect.
TOK=$(aws rds generate-db-auth-token --hostname "$RDS_ENDPOINT" \
      --port 5432 --username app_user1 --region us-east-1)
PGPASSWORD="$TOK" psql -h "$RDS_ENDPOINT" -U app_user1 -d "$DB" \
  --set=sslmode=require -c "SELECT 1" 2>&1 | tee /tmp/iam_cleanup_verify.log
# Expected: "PAM authentication failed for user \"app_user1\""
# This proves IAM no longer allows the old ARN.
```

---

## Full Rollback Procedure

Use if the rollback decision tree directs full rollback at any step.

```bash
# 1. Stop services
systemctl stop lavandula-dashboard
systemctl stop lavandula-orchestrator

# 2. Drain leftover sessions
PGPASSWORD="$MASTER_PW" psql -h "$RDS_ENDPOINT" -U "$MASTER_USER" -d "$DB" \
  --set=sslmode=require -c "
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE usename IN ('research_app', 'research_ro')
  AND pid <> pg_backend_pid();"

# 3. Reverse rename
PGPASSWORD="$MASTER_PW" psql -h "$RDS_ENDPOINT" -U "$MASTER_USER" -d "$DB" \
  --set=sslmode=require -v ON_ERROR_STOP=1 \
  -f locard/operations/0037-cutover-rollback.sql

# 4. Restore SSM values
aws ssm put-parameter --name /cloud2.lavandulagroup.com/rds-app-user \
    --type SecureString --value app_user1 --overwrite
aws ssm put-parameter --name /cloud2.lavandulagroup.com/rds-ro-user \
    --type SecureString --value ro_user1 --overwrite

# 5. Restart (IAM policy stays in overlap — old ARNs still allowed)
systemctl start lavandula-dashboard
systemctl start lavandula-orchestrator
```

**Total rollback time**: ~3 minutes from decision-to-rollback to dashboard-back-up.

### Source-tree handling during rollback

- **Short rollback (<4 hours, retry scheduled)**: Leave source-tree edits in place. Runtime reads from SSM; source-tree leading production is harmless during a brief window.
- **Extended rollback (overnight or longer, no retry)**: `git revert` the source-tree PR. Prevents fresh-environment builds from diverging.

---

## Execution Log

Record timestamps and decisions as you execute:

| Step | Time | Result | Notes |
|------|------|--------|-------|
| P1 Pre-checks | | | |
| P2 dashboard_user1 decision | | Branch taken: | |
| P3 Snapshots | | 8 files: Y/N | |
| T-1 IAM overlay | | Propagation confirmed: Y/N | |
| T+0 Stop services | | Sessions drained: Y/N | |
| T+1 Rename | | research_app + research_ro: Y/N | |
| T+2 SSM update | | Both params updated: Y/N | |
| T+4 IAM connect verify | | Both roles connect: Y/N | |
| T+4 Dashboard restart | | Health OK: Y/N | |
| T+5 Smoke tests | | All pass: Y/N | |
| Parity diff | | All 8 PASS: Y/N | |
| T+6 Remove old ARNs | | Old ARN denied: Y/N | |

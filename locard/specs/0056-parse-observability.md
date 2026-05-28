# Spec 0056: Parse Run Observability (Log Shipping + Exit Reason)

**Status:** Draft
**Author:** Architect
**Created:** 2026-05-28
**Dependencies:** 0054 (Parse Dashboard)

## Problem Statement

When a parse run terminates unexpectedly, the only detailed log lives on the GPU instance at `/var/log/docling-worker.log`. When the instance is terminated (by the orchestrator after completion, or by AWS on spot reclaim), the log is destroyed. This makes post-mortem diagnosis impossible.

**Concrete incident:** Run 15 (`P-all-24h_max`) processed 909 of 11,072 eligible documents and then the worker reported completion. The orchestrator terminated the instance. We could not determine whether the worker exited because:
- `fetch_work_batch` returned empty (code bug or transient DB issue)
- Spot termination was detected (contradicted by Capacity Manager showing 0 interruptions)
- An unhandled error occurred

The investigation consumed significant time and reached no definitive conclusion.

## Goals

1. **Worker logs survive instance termination** — ship the worker log to a durable store (S3) before the instance is terminated, so post-mortem analysis always has the raw log available.
2. **Exit reason at a glance** — record WHY a run ended in the `parse_runs` table, visible in the dashboard without needing to dig through logs.
3. **No new IAM permissions required** — use S3 (already permitted by the `cloud2_lavandulagroup` IAM role) rather than CloudWatch Logs (which would require IAM policy changes). CloudWatch can be added later as an enhancement.
4. **Minimal worker changes** — the worker already logs structured JSON to stdout. Changes should be limited to recording the exit reason and ensuring the log is shipped.

## Non-Goals

- Real-time log streaming (tail -f equivalent) — S3 upload happens at run end, not continuously
- CloudWatch Logs integration (requires IAM policy update — future enhancement)
- Alerting or notification on run failures (separate concern)
- Changes to the worker's processing logic or error handling
- Dashboard log viewer UI (the log file URL is enough for now)

## IAM Prerequisites

The GPU instances use the `cloud2_lavandulagroup` IAM role. This role already grants:
- `s3:GetObject` on `lavandula-nonprofit-collaterals` (used for PDF downloads and worker-code.tar.gz)

**Required addition:** `s3:PutObject` on `s3://lavandula-nonprofit-collaterals/logs/parse/*` must be confirmed or added to the role policy. This is needed for both the orchestrator-driven log pull (SSM command runs `aws s3 cp` on the GPU instance) and the worker's `atexit` fallback.

The AWS CLI is pre-installed on the GPU AMI (used by SSM agent and S3 downloads). No additional tooling is needed.

## Technical Design

### Component 1: Exit Reason in `parse_runs`

Add an `exit_reason` column to `lava_parse.parse_runs` that records why the worker stopped.

**Schema change (DDL — operator applies manually):**

```sql
ALTER TABLE lava_parse.parse_runs
  ADD COLUMN exit_reason TEXT;

COMMENT ON COLUMN lava_parse.parse_runs.exit_reason IS
  'Why the run ended: empty_batch, spot_termination, max_docs, max_hours, error, cancelled, unknown';
```

**Exit reason values:**

| Value | Set by | Meaning |
|-------|--------|---------|
| `empty_batch` | Worker | `fetch_work_batch` returned no rows — worker believes all work is done |
| `spot_termination` | Worker | Worker detected EC2 spot termination notice via instance metadata |
| `max_docs` | Worker | `--max-docs` safety cap reached |
| `max_hours` | Orchestrator | Orchestrator terminated because `--max-hours` elapsed |
| `cancelled` | Orchestrator | Orchestrator received SIGTERM (user stop via dashboard) |
| `error` | Worker | Worker's top-level try/except caught an unhandled exception before exit |
| `unknown` | Nobody | Default — worker exited without setting a reason (crash, OOM kill, legacy code) |

**Write precedence:** The worker writes `exit_reason` via `finish_run()`. The orchestrator may OVERRIDE it with `max_hours` or `cancelled` via `set_exit_reason()`. Orchestrator writes win because they represent an external decision to stop the run, regardless of the worker's internal state. The column is nullable; `NULL` means the run predates this feature (legacy) and is displayed as "N/A" in the dashboard.

**Crash semantics:** If the worker crashes before calling `finish_run()`, `exit_reason` remains `NULL` and `finished_at` remains `NULL`. The orchestrator detects this via instance state (terminated/shutting-down) and sets `exit_reason = 'error'` directly. The `finished_at` is set by the orchestrator in this case. This means `exit_reason = 'unknown'` should never appear for new runs — it exists only as the `finish_run()` default for defense-in-depth.

**Worker changes (`lavandula/parse/worker.py`):**

The worker sets `exit_reason` before calling `finish_run`:

```python
# In _run_loop, at each break point:
if not batch:
    exit_reason = "empty_batch"
    logger.info("no more eligible documents")
    break

if _spot_termination_pending():
    exit_reason = "spot_termination"
    logger.warning("spot termination notice, exiting")
    break

if max_docs and stats["total"] >= max_docs:
    exit_reason = "max_docs"
    break

# After the loop:
db.finish_run(conn, args.run_id, stats, exit_reason=exit_reason)
```

**DB changes (`lavandula/parse/db.py`):**

```python
def finish_run(conn, run_id: int, stats: dict, exit_reason: str = "unknown") -> None:
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE lava_parse.parse_runs
                   SET finished_at = NOW(), stats_json = %s, exit_reason = %s
                   WHERE id = %s""",
                (json.dumps(stats), exit_reason, run_id),
            )
```

**Orchestrator changes (`parse_documents.py`):**

When the orchestrator terminates a run (max_hours, cancelled), it writes the exit_reason directly:

```python
# Max hours reached:
db.set_exit_reason(conn, run_id, "max_hours")

# SIGTERM received:
db.set_exit_reason(conn, run_id, "cancelled")
```

When the orchestrator sees "Worker reported completion," it reads the exit_reason and logs it:

```python
status = db.get_run_status(conn, run_tag)
if status and status.get("finished_at"):
    reason = status.get("exit_reason", "unknown")
    self.stdout.write(f"Worker reported completion (reason: {reason}).\n")
```

**Dashboard changes:**

The parse progress partial displays the exit reason when a run is finished:
- `empty_batch` → "Completed — no remaining work"
- `spot_termination` → "Stopped — spot instance reclaimed"
- `max_docs` → "Stopped — document limit reached"
- `max_hours` → "Stopped — time limit reached"
- `cancelled` → "Cancelled by operator"
- `error` → "Failed — check logs"
- `unknown` → "Completed (reason unknown)"

### Component 2: Log Shipping to S3

After the worker finishes (or on crash via signal handler), upload the worker log to S3 so it persists.

**S3 location:**

```
s3://lavandula-nonprofit-collaterals/logs/parse/
  {run_tag}/{instance_id}/worker.log
```

Example: `s3://lavandula-nonprofit-collaterals/logs/parse/P-all-24h_max/i-011f2b0beef2444d1/worker.log`

**run_tag sanitization:** The `run_tag` is user-provided (dashboard form) and is interpolated into a shell command (`aws s3 cp ... /{run_tag}/...`). To prevent command injection, `run_tag` MUST be validated at creation time (in `ParseJobCreateView`) to contain only `[a-zA-Z0-9_-]`. This validation already exists in the form's `clean_run_tag()` method but must be confirmed or hardened. The `_pull_worker_log` method must also reject any `run_tag` not matching this pattern as a defense-in-depth measure.

**S3 access control:** The `lavandula-nonprofit-collaterals` bucket is private (no public access). Read/write is restricted to the `cloud2_lavandulagroup` IAM role. No additional access policy changes are needed for the `logs/parse/` prefix.

**Log lifecycle:** Add an S3 lifecycle rule to expire objects under `logs/parse/` after 90 days. This limits long-term storage of potentially sensitive log content (error messages may contain file paths or org identifiers). The lifecycle rule is applied by the operator via the S3 console or CLI, not by code.

**Log shipping hierarchy (two layers, clear ownership):**

1. **Primary: Orchestrator-driven pull via SSM** — the orchestrator sends an SSM command to upload the log BEFORE terminating the instance. This is the authoritative mechanism. It works for all normal exits (empty_batch, max_docs, worker completion) because the instance is still running when the orchestrator decides to terminate.
2. **Fallback: Worker `atexit` self-ship** — the worker registers an `atexit` handler that uploads the log on exit. This covers the case where the worker exits on its own (spot termination detected) before the orchestrator can pull. It is best-effort and may fail on hard crashes.

If both mechanisms succeed, the S3 key is the same so the second write is a no-op overwrite. No conflict.

**Implementation — orchestrator-driven (primary):**

The orchestrator pulls the log via SSM BEFORE terminating the instance.

```python
# In the orchestrator, before _safe_terminate:
self._pull_worker_log(ssm, instance_id, run_tag)
self._safe_terminate(ec2, instance_id)
```

`_pull_worker_log` sends an SSM command to upload the log:

```python
import re

_SAFE_TAG_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

def _pull_worker_log(self, ssm, instance_id, run_tag):
    """Upload worker log to S3 before terminating the instance."""
    if not _SAFE_TAG_RE.match(run_tag):
        self.stderr.write(f"Warning: refusing to ship log — unsafe run_tag: {run_tag!r}\n")
        return
    try:
        ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [
                f"aws s3 cp /var/log/docling-worker.log "
                f"s3://lavandula-nonprofit-collaterals/logs/parse/{run_tag}/{instance_id}/worker.log"
            ]},
        )
        # Brief wait for the upload to complete
        time.sleep(5)
    except Exception:
        self.stderr.write(f"Warning: could not pull worker log from {instance_id}\n")
```

**Fallback — worker self-ship:**

As a belt-and-suspenders measure, the worker also attempts to upload its log on exit via `atexit`:

```python
import atexit

def _ship_log_on_exit(run_tag, instance_id):
    """Best-effort log upload on worker exit."""
    try:
        import boto3
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.upload_file(
            "/var/log/docling-worker.log",
            "lavandula-nonprofit-collaterals",
            f"logs/parse/{run_tag}/{instance_id}/worker.log",
        )
    except Exception:
        pass  # Best effort — orchestrator pull is the primary mechanism

atexit.register(_ship_log_on_exit, args.run_tag, args.worker_id or "unknown")
```

**Log lifecycle:** Parse logs in S3 accumulate. A lifecycle rule can be added later to expire them after 30 days, but this is not part of this spec.

### Component 3: Orchestrator Log Enhancement

The orchestrator log on cloud2 already persists, but its exit messages are ambiguous. Improve them:

**Current:** `"Worker reported completion.\n"` — no distinction between "all work done" and "worker gave up"

**New:** `"Worker reported completion (reason: empty_batch). 10,180 docs remain.\n"` — includes exit reason AND remaining count.

The orchestrator already calls `get_eligible_count` in its spot-reclaim branch. Add it to the normal completion branch too, using the SAME priority and ntee_filter parameters that the worker used (passed to the orchestrator at run start and stored in `config_json`):

```python
if status and status.get("finished_at"):
    reason = status.get("exit_reason", "unknown")
    remaining = db.get_eligible_count(conn, priority, ntee_filter=ntee_filter)
    self.stdout.write(
        f"Worker reported completion (reason: {reason}). "
        f"{remaining:,} docs remain.\n"
    )
    if remaining > 0 and reason == "empty_batch":
        self.stderr.write(
            f"WARNING: Worker reported empty_batch but {remaining:,} docs "
            f"are still eligible. Possible query issue.\n"
        )
```

The `priority` and `ntee_filter` are the same variables used throughout `_execute_run` — they originate from the Job's `config_json` and are passed identically to both the worker (via SSM command args) and `get_eligible_count`. No filter mismatch is possible.

This would have immediately flagged the run 15 issue.

## Migration

**Single operational path:** The operator runs the DDL file `lavandula/migrations/parse/004_exit_reason.sql` manually against RDS, the same as all `lava_parse` schema changes. No Django migration is involved because `parse_runs` is not a Django model — it is accessed via raw psycopg2 from both the worker and the orchestrator.

**DDL file (`lavandula/migrations/parse/004_exit_reason.sql`):**

```sql
-- Spec 0056: Parse observability — exit reason tracking
ALTER TABLE lava_parse.parse_runs ADD COLUMN exit_reason TEXT;
COMMENT ON COLUMN lava_parse.parse_runs.exit_reason IS
  'Why the run ended: empty_batch, spot_termination, max_docs, max_hours, error, cancelled, unknown';

-- Grant to docling_writer (worker writes exit_reason via finish_run)
GRANT UPDATE (exit_reason) ON lava_parse.parse_runs TO docling_writer;
```

**Rollback:** `ALTER TABLE lava_parse.parse_runs DROP COLUMN exit_reason;`

## Files Changed

| File | Change |
|------|--------|
| `lavandula/parse/db.py` | Add `exit_reason` param to `finish_run`, add `set_exit_reason` function |
| `lavandula/parse/worker.py` | Track exit reason at each break point, pass to `finish_run`, add `atexit` log shipper |
| `lavandula/dashboard/pipeline/management/commands/parse_documents.py` | Pull worker log before termination, log exit reason + remaining count |
| `lavandula/dashboard/pipeline/templates/pipeline/partials/parse_progress.html` | Display exit reason in completed run UI |
| `lavandula/migrations/parse/004_exit_reason.sql` | DDL for the column addition |

## Testing

1. **Unit test:** `finish_run` writes `exit_reason` correctly
2. **Unit test:** Worker sets correct exit_reason for each break condition
3. **Unit test:** `set_exit_reason` (orchestrator path) overrides worker-set value
4. **Unit test:** Dashboard template renders all exit_reason values correctly, including `NULL` (legacy runs) as "N/A"
5. **Integration test:** Start a parse run with `--max-docs 5`, verify exit_reason is `max_docs` in `parse_runs`
6. **Integration test:** Verify `_pull_worker_log` handles SSM failure gracefully (logs warning, does not crash orchestrator)
7. **Manual test:** After a real run, verify worker log exists in S3 at the expected path
8. **Manual test:** Dashboard shows human-readable exit reason for completed runs

## Traps to Avoid

1. **SSM command timing** — The `_pull_worker_log` SSM command is async. The orchestrator must wait briefly for the upload to complete before terminating the instance. A 5-second sleep is sufficient for a small log file.
2. **Spot reclaim race** — If AWS reclaims the instance before the orchestrator can pull the log, the SSM command fails silently. The `atexit` handler in the worker is the fallback, but it also races against the 2-minute spot warning. Accept that some logs may be lost on hard spot reclaims — the `exit_reason` column is the guaranteed minimum.
3. **Log file size** — Worker logs are ~50KB for a 500-doc batch. Even a 10,000-doc run produces < 1MB. S3 upload is fast.
4. **Backward compatibility** — The `exit_reason` column is nullable. Old runs (before this change) will have `NULL`, which the dashboard should display as "—" or "N/A", not as an error.
5. **run_tag injection** — The `run_tag` is interpolated into shell commands (both SSM worker start and log pull). Validate it matches `^[a-zA-Z0-9_-]+$` at creation time (form validation) AND at use time (defense-in-depth in `_pull_worker_log`). Never trust the value without checking.

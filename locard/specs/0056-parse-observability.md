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

| Value | Meaning |
|-------|---------|
| `empty_batch` | `fetch_work_batch` returned no rows — worker believes all work is done |
| `spot_termination` | Worker detected EC2 spot termination notice via instance metadata |
| `max_docs` | `--max-docs` safety cap reached |
| `max_hours` | Orchestrator terminated because `--max-hours` elapsed |
| `cancelled` | Orchestrator received SIGTERM (user stop) |
| `error` | Worker crashed with unhandled exception |
| `unknown` | Worker exited without setting a reason (legacy code, crash before exit handler) |

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

**Implementation — orchestrator-driven (preferred):**

The orchestrator pulls the log via SSM BEFORE terminating the instance. This is more reliable than having the worker self-ship (which fails on hard crashes or spot reclaims with < 2s warning).

```python
# In the orchestrator, before _safe_terminate:
self._pull_worker_log(ssm, instance_id, run_tag)
self._safe_terminate(ec2, instance_id)
```

`_pull_worker_log` sends an SSM command to upload the log:

```python
def _pull_worker_log(self, ssm, instance_id, run_tag):
    """Upload worker log to S3 before terminating the instance."""
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

The orchestrator already calls `get_eligible_count` in its spot-reclaim branch. Add it to the normal completion branch too:

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

This would have immediately flagged the run 15 issue.

## Migration

**DDL (operator applies manually):**

```sql
-- 0056: Parse observability
ALTER TABLE lava_parse.parse_runs ADD COLUMN exit_reason TEXT;
COMMENT ON COLUMN lava_parse.parse_runs.exit_reason IS
  'Why the run ended: empty_batch, spot_termination, max_docs, max_hours, error, cancelled, unknown';
```

**No Django migration needed** — `parse_runs` is accessed via raw psycopg2, not Django ORM.

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
3. **Integration test:** Start a parse run with `--max-docs 5`, verify exit_reason is `max_docs` in `parse_runs`
4. **Manual test:** After a real run, verify worker log exists in S3 at the expected path
5. **Manual test:** Dashboard shows human-readable exit reason for completed runs

## Traps to Avoid

1. **SSM command timing** — The `_pull_worker_log` SSM command is async. The orchestrator must wait briefly for the upload to complete before terminating the instance. A 5-second sleep is sufficient for a small log file.
2. **Spot reclaim race** — If AWS reclaims the instance before the orchestrator can pull the log, the SSM command fails silently. The `atexit` handler in the worker is the fallback, but it also races against the 2-minute spot warning. Accept that some logs may be lost on hard spot reclaims — the `exit_reason` column is the guaranteed minimum.
3. **Log file size** — Worker logs are ~50KB for a 500-doc batch. Even a 10,000-doc run produces < 1MB. S3 upload is fast.
4. **Backward compatibility** — The `exit_reason` column is nullable. Old runs (before this change) will have `NULL`, which the dashboard should display as "—" or "N/A", not as an error.

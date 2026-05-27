# Plan 0055: Multi-Instance Parse (SKIP LOCKED Work Claiming)

**Spec:** `locard/specs/0055-multi-instance-parse.md`
**Created:** 2026-05-27

## Overview

Enable 1–4 concurrent GPU workers for Docling parsing. Workers self-coordinate via PostgreSQL `SKIP LOCKED` on a new `lava_parse.work_queue` table. The orchestrator launches/monitors N instances in a slot-based model. Dashboard shows per-instance health and aggregate progress.

## Files to Modify

| File | Change |
|------|--------|
| `lavandula/parse/db.py` | New functions: `populate_work_queue`, `fetch_work_batch` (SKIP LOCKED), `complete_work_item`, `reclaim_stale_claims`, `get_queue_progress`, `cleanup_work_queue`. Rename old `fetch_work_batch` → `fetch_work_batch_legacy`. |
| `lavandula/parse/worker.py` | Add `--worker-id` arg, set `app.worker_id` session var, use new `fetch_work_batch` when `--worker-id` is provided, call `complete_work_item` per doc, remove advisory lock when in queue mode. |
| `lavandula/dashboard/pipeline/management/commands/parse_documents.py` | Add `--workers` arg, slot-based instance management, queue population, multi-instance poll loop, stale detection per instance, SIGTERM terminates all. |
| `lavandula/dashboard/pipeline/forms.py` | Add `workers` field (1–4) to `ParseRunForm`. |
| `lavandula/dashboard/pipeline/views.py` | Pass `workers` to orchestrator config, update progress display to query `work_queue`, add per-instance status, update dry run estimates. |
| `lavandula/dashboard/pipeline/templates/pipeline/parse.html` | Add workers field, per-instance status cards, fleet summary. |
| `lavandula/dashboard/pipeline/templates/pipeline/partials/parse_progress.html` | Per-instance cards + aggregate progress from work_queue. |
| `lavandula/dashboard/pipeline/orchestrator.py` | Pass `workers` through `COMMAND_MAP` / `build_argv()`. |
| `lavandula/dashboard/pipeline/tests/test_parse.py` | New tests for SKIP LOCKED, queue lifecycle, multi-instance orchestration, dashboard. |

## Files NOT to Modify

- `lavandula/parse/chunking.py` — Docling processing logic unchanged
- `lavandula/parse/config.py` — No config changes needed
- `lavandula/dashboard/pipeline/models.py` — No new models (Job model unchanged)
- `lavandula/dashboard/pipeline/stages.py` — Parse stage registration unchanged

## Implementation Steps

### Step 1: Database Layer — Work Queue Functions (`lavandula/parse/db.py`)

**What:** Add all new DB functions for the work queue. This is the foundation everything else builds on.

**1a. Rename `fetch_work_batch` → `fetch_work_batch_legacy`**

Rename the existing function at line 48. Keep the same signature and behavior. This preserves backward compatibility for any non-dashboard CLI usage.

**1b. Add `populate_work_queue(conn, run_id, priority_filter, ntee_filter)`**

Bulk-insert eligible docs into `work_queue` for a given `run_id`. Uses the same NOT IN / classification / NTEE logic as the old `fetch_work_batch`, but as an INSERT...SELECT:

```python
def populate_work_queue(conn, run_id: int, priority_filter: list[str], ntee_filter: str | None = None) -> int:
    """Materialize eligible docs into work_queue. Returns count inserted."""
    ntee_join = "JOIN lava_corpus.nonprofits_seed ns ON c.source_org_ein = ns.ein" if ntee_filter else ""
    ntee_where = "AND ns.ntee_code LIKE %(ntee)s" if ntee_filter else ""
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO lava_parse.work_queue (run_id, content_sha256, source_org_ein)
                SELECT %(run_id)s, c.content_sha256, c.source_org_ein
                FROM lava_corpus.corpus c
                {ntee_join}
                WHERE c.content_sha256 NOT IN (
                    SELECT content_sha256 FROM lava_parse.documents
                )
                  AND c.classification = ANY(%(priority)s)
                  {ntee_where}
                ON CONFLICT (run_id, content_sha256) DO NOTHING
                """,
                {"run_id": run_id, "priority": priority_filter, "ntee": ntee_filter},
            )
            return cur.rowcount
```

**1c. Add `fetch_work_batch(conn, run_id, batch_size, worker_id)`**

The SKIP LOCKED CTE from the spec. Atomic claim+update in one statement.

```python
def fetch_work_batch(conn, run_id: int, batch_size: int, worker_id: str) -> list[dict]:
    with conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                WITH claimed AS (
                    SELECT id, content_sha256, source_org_ein
                    FROM lava_parse.work_queue
                    WHERE run_id = %(run_id)s AND claimed_by IS NULL
                    ORDER BY id
                    LIMIT %(limit)s
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE lava_parse.work_queue wq
                SET claimed_by = %(worker_id)s, claimed_at = NOW()
                FROM claimed
                WHERE wq.id = claimed.id
                RETURNING wq.content_sha256, wq.source_org_ein
                """,
                {"run_id": run_id, "limit": batch_size, "worker_id": worker_id},
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]
```

**1d. Add `complete_work_item(conn, run_id, content_sha256, error=None)`**

Mark a work queue item as completed. `error` is a short classification string (max 200 chars), truncated if longer.

```python
def complete_work_item(conn, run_id: int, content_sha256: str, error: str | None = None):
    if error and len(error) > 200:
        error = error[:200]
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE lava_parse.work_queue
                SET completed_at = NOW(), error = %(error)s
                WHERE run_id = %(run_id)s AND content_sha256 = %(sha)s
                """,
                {"run_id": run_id, "sha": content_sha256, "error": error},
            )
```

**1e. Add `reclaim_stale_claims(conn, run_id, worker_id)`**

Reset claims for a terminated worker so they can be picked up by a replacement.

```python
def reclaim_stale_claims(conn, run_id: int, worker_id: str) -> int:
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE lava_parse.work_queue
                SET claimed_by = NULL, claimed_at = NULL
                WHERE run_id = %(run_id)s AND claimed_by = %(worker)s AND completed_at IS NULL
                """,
                {"run_id": run_id, "worker": worker_id},
            )
            return cur.rowcount
```

**1f. Add `get_queue_progress(conn, run_id)`**

Returns aggregate progress for the orchestrator poll loop.

```python
def get_queue_progress(conn, run_id: int) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE claimed_by IS NOT NULL) as claimed,
                COUNT(*) FILTER (WHERE completed_at IS NOT NULL AND error IS NULL) as completed,
                COUNT(*) FILTER (WHERE completed_at IS NOT NULL AND error IS NOT NULL) as errored
            FROM lava_parse.work_queue
            WHERE run_id = %(run_id)s
            """,
            {"run_id": run_id},
        )
        row = cur.fetchone()
        return {"total": row[0], "claimed": row[1], "completed": row[2], "errored": row[3]}
```

**1g. Add `get_per_worker_stats(conn, run_id)`**

Returns per-instance throughput for dashboard display.

```python
def get_per_worker_stats(conn, run_id: int) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            """
            SELECT claimed_by,
                   COUNT(*) FILTER (WHERE completed_at IS NOT NULL) as completed,
                   COUNT(*) FILTER (WHERE completed_at IS NULL) as in_progress,
                   MAX(completed_at) as last_activity,
                   EXTRACT(EPOCH FROM MAX(completed_at) - MIN(claimed_at)) as active_seconds
            FROM lava_parse.work_queue
            WHERE run_id = %(run_id)s AND claimed_by IS NOT NULL
            GROUP BY claimed_by
            """,
            {"run_id": run_id},
        )
        return [dict(r) for r in cur.fetchall()]
```

**1h. Add `cleanup_work_queue(conn, run_id)`**

Delete queue rows after run completion.

```python
def cleanup_work_queue(conn, run_id: int) -> int:
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM lava_parse.work_queue WHERE run_id = %(run_id)s",
                {"run_id": run_id},
            )
            return cur.rowcount
```

**1i. Add `reclaim_all_stale_claims(conn, run_id)`**

For run resume: reclaim all claims from previous attempt.

```python
def reclaim_all_stale_claims(conn, run_id: int) -> int:
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE lava_parse.work_queue
                SET claimed_by = NULL, claimed_at = NULL
                WHERE run_id = %(run_id)s AND claimed_by IS NOT NULL AND completed_at IS NULL
                """,
                {"run_id": run_id},
            )
            return cur.rowcount
```

### Step 2: Worker Changes (`lavandula/parse/worker.py`)

**What:** Add `--worker-id` argument, set RLS session variable, use new SKIP LOCKED path when `--worker-id` is provided.

**2a. Add `--worker-id` to `parse_args()`** (line 338)

```python
parser.add_argument("--worker-id", default=None,
                    help="EC2 instance ID (enables SKIP LOCKED queue mode)")
```

**2b. Validate `--worker-id` format in `main()`** (after line 43)

```python
import re
if args.worker_id and not re.match(r'^i-[0-9a-f]+$', args.worker_id):
    logger.error("invalid worker-id format", extra={"worker_id": args.worker_id})
    sys.exit(1)
```

**2c. Set RLS session variable in `_connect()`** or after connection

After connecting, if `worker_id` is provided:
```python
if args.worker_id:
    with conn.cursor() as cur:
        cur.execute("SET app.worker_id = %s", (args.worker_id,))
```

**2d. Branch in `main()`: queue mode vs legacy mode**

When `--worker-id` is provided, skip the advisory lock and use the new `_run_loop_queue()`. When not provided, use the existing `_run_loop()` with advisory lock (backward compat).

```python
def main() -> None:
    args = parse_args()
    _setup_logging()
    # ... validation ...
    conn = _connect(args)

    if args.worker_id:
        # Queue mode: SKIP LOCKED, no advisory lock
        _run_loop_queue(conn, args)
    else:
        # Legacy mode: advisory lock
        if not db.acquire_worker_lock(conn):
            logger.error("another worker is already running")
            sys.exit(1)
        try:
            _run_loop(conn, args)
        finally:
            db.release_worker_lock(conn)

    conn.close()
    logger.info("worker finished")
```

**2e. Add `_run_loop_queue(conn, args)` function**

Similar to existing `_run_loop()` but:
- Calls `db.fetch_work_batch(conn, args.run_id, args.batch_size, args.worker_id)` instead of `db.fetch_work_batch_legacy(...)`
- Calls `db.complete_work_item(conn, args.run_id, sha, error=None)` after each successful `insert_document()`
- Calls `db.complete_work_item(conn, args.run_id, sha, error="corrupt_pdf")` (or similar short string) on `PermanentError`
- Does NOT call `complete_work_item` on `TransientError` (doc remains claimable)
- Still calls `db.update_run_stats()` and `db.finish_run()` as before
- Still checks `_spot_termination_pending()`

The processing per-document (download, parse, insert) is identical — extract the inner loop body into a helper if needed to avoid duplication, but keep it minimal.

### Step 3: Orchestrator — Multi-Instance Management (`parse_documents.py`)

**What:** Add `--workers` arg, slot-based instance management, queue population, multi-instance poll loop.

**3a. Add `--workers` argument** (in `add_arguments`, after line 66)

```python
parser.add_argument("--workers", type=int, default=1,
                    help="Number of concurrent GPU instances (1-4)")
```

**3b. Add validation** (in `handle`, after line 79)

```python
if not (1 <= options["workers"] <= 4):
    raise CommandError("--workers must be 1-4")
```

**3c. Add `_populate_work_queue()` method**

Call `db.populate_work_queue()`. On resume (run_tag already exists with unfinished run), call `db.reclaim_all_stale_claims()` instead of re-populating.

**3d. Refactor `_execute_run()` for multi-instance**

Replace the current single-instance flow (lines 223–405) with:

1. Create/resume parse_run (existing)
2. Populate work queue (or reclaim stale on resume)
3. Get queue size as eligible_count
4. Initialize slot list: `slots = [{"instance_id": None, "status": "pending", "relaunch_count": 0} for _ in range(workers)]`
5. Launch N instances concurrently (iterate slots, call `_launch_with_capacity_retry` for each)
6. Multi-instance poll loop (see below)
7. Terminate all, cleanup queue, finalize Job

**3e. Multi-instance poll loop**

```python
while True:
    time.sleep(POLL_INTERVAL_SECONDS)

    if self._shutdown_requested:
        self._terminate_all(ec2, slots)
        final_status = "cancelled"
        break

    if elapsed >= max_seconds:
        self._terminate_all(ec2, slots)
        break

    # Check each slot
    for slot in slots:
        if slot["status"] != "running":
            continue
        iid = slot["instance_id"]
        state = self._get_instance_state(ec2, iid)

        if state in ("terminated", "shutting-down"):
            # Reclaim stale claims for this instance
            db.reclaim_stale_claims(conn, run_id, iid)
            # Check remaining work
            progress = db.get_queue_progress(conn, run_id)
            remaining = progress["total"] - progress["completed"] - progress["errored"]
            if remaining == 0:
                slot["status"] = "done"
                continue
            # Relaunch
            slot["relaunch_count"] += 1
            if slot["relaunch_count"] > MAX_RELAUNCH_ATTEMPTS:
                slot["status"] = "capacity_exhausted"
                continue
            new_id = self._launch_with_capacity_retry(...)
            if new_id:
                slot["instance_id"] = new_id
            else:
                slot["status"] = "capacity_exhausted"
            continue

        # Heartbeat check: worker stale on running instance
        if slot["status"] == "running":
            last_activity = self._get_worker_last_activity(conn, run_id, iid)
            if last_activity and (time.time() - last_activity.timestamp()) > HEARTBEAT_STALE_MINUTES * 60:
                self._safe_terminate(ec2, iid)
                db.reclaim_stale_claims(conn, run_id, iid)
                # Relaunch logic (same as above)
                ...

    # Aggregate progress
    progress = db.get_queue_progress(conn, run_id)
    self._update_job_progress(job_id, progress["completed"], progress["total"])

    # All work done?
    if progress["completed"] + progress["errored"] >= progress["total"]:
        break

    # All slots dead?
    active = [s for s in slots if s["status"] == "running"]
    if not active:
        final_status = "failed"
        break
```

**3f. Add `_terminate_all(ec2, slots)` method**

Iterate all slots, call `_safe_terminate` for each running instance.

**3g. Add `_get_worker_last_activity(conn, run_id, worker_id)` method**

```python
def _get_worker_last_activity(self, conn, run_id, worker_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT MAX(completed_at)
            FROM lava_parse.work_queue
            WHERE run_id = %s AND claimed_by = %s AND completed_at IS NOT NULL
            """,
            (run_id, worker_id),
        )
        row = cur.fetchone()
        return row[0] if row else None
```

**3h. Update `_start_worker()` to pass `--worker-id`** (line 599)

Add `--worker-id {instance_id}` to the worker command string.

**3i. Update instance tagging**

Change `_launch_instance()` to use `docling-worker-{run_tag}-{slot_index}` as the Name tag.

**3j. Update `parse_runs.instance_ids`**

After launching instances, update `parse_runs` with the full instance ID list:
```python
with conn:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE lava_parse.parse_runs SET instance_id = %s, instance_ids = %s WHERE id = %s",
            (slots[0]["instance_id"], [s["instance_id"] for s in slots if s["instance_id"]], run_id),
        )
```

**3k. Update dry run to show multi-worker estimates**

When `--dry-run` and `--workers > 1`:
```
Estimated GPU time: ~45h (total compute)
  1 worker:  ~45h wall time, ~$27 (spot)
  2 workers: ~23h wall time, ~$27 (spot)
  4 workers: ~12h wall time, ~$27 (spot)
```

**3l. Cleanup on completion**

After the run loop exits:
```python
self._terminate_all(ec2, slots)
db.cleanup_work_queue(conn, run_id)
```

### Step 4: Orchestrator Integration (`orchestrator.py`)

**What:** Pass `workers` through the COMMAND_MAP / build_argv() path.

**4a. Update COMMAND_MAP "parse" entry**

Add `workers` parameter spec:
```python
"workers": ParamSpec(type="integer", cli_flag="--workers"),
```

**4b. Verify `build_argv()`** handles integer type correctly (should already work).

### Step 5: Dashboard Form (`forms.py`)

**What:** Add `workers` field to `ParseRunForm`.

```python
workers = forms.IntegerField(
    initial=1, min_value=1, max_value=4,
    widget=forms.NumberInput(attrs={"class": _SELECT}),
    label="Workers",
    help_text="Concurrent GPU instances (1-4)",
)
```

### Step 6: Dashboard Views (`views.py`)

**What:** Update ParseView, ParseJobCreateView, ParseProgressPartial.

**6a. Update `ParseJobCreateView._dry_run()`**

Show multi-worker time estimates (1/2/4 workers). Read `workers` from form data.

**6b. Update `ParseJobCreateView._launch()`**

Pass `workers` value into the job config.

**6c. Update `ParseProgressPartial`**

Query `db.get_queue_progress()` and `db.get_per_worker_stats()` for running parse jobs. Return per-instance status + aggregate progress to the template.

Connection for these queries: use a raw psycopg2 connection (same pattern as existing eligible count queries) since these query `lava_parse` schema.

**6d. Update `ParseView._reconcile_parse_jobs()`**

No change needed — reconciliation logic uses Job status and PID liveness, not instance count.

### Step 7: Dashboard Templates

**7a. `parse.html` — Add workers field to the form**

Add a number input for workers (1–4) in the launch form, between instance type and batch size.

**7b. `partials/parse_progress.html` — Per-instance status**

When a parse job is running and has multi-instance data:
- Show fleet summary: "3/4 workers active"
- Per-instance cards: instance ID (truncated), status badge (running/terminated/relaunching), docs completed, throughput (docs/hr)
- Aggregate progress bar uses work_queue completed/total

When `workers = 1`, display is identical to current (no fleet cards).

### Step 8: Tests (`test_parse.py`)

**8a. Unit: SKIP LOCKED claim no overlap**

Create a work_queue with 10 items. In two concurrent transactions (using threading + SQLite won't work for SKIP LOCKED — use `unittest.mock` to simulate the DB behavior, or mark as PostgreSQL-only integration test with `@skipUnless`).

For SQLite test environment: mock `db.fetch_work_batch` to simulate the CTE behavior. Test that the mock correctly partitions work with no overlap.

**8b. Unit: populate_work_queue**

Test that eligible docs are inserted, duplicates handled via ON CONFLICT, count returned correctly.

**8c. Unit: reclaim_stale_claims**

Test that only uncompleted claims for the specified worker_id are reset.

**8d. Unit: complete_work_item**

Test completed_at set, error truncated to 200 chars.

**8e. Unit: get_queue_progress**

Test aggregate counts (total, claimed, completed, errored).

**8f. Integration: --workers 1 regression**

Mock the EC2/SSM layer. Verify single-worker path populates queue, launches one instance, polls, completes. Functionally identical to pre-change behavior.

**8g. Integration: multi-worker launch**

Mock EC2. Request 4 workers. Verify 4 `run_instances` calls with unique Name tags containing slot indices.

**8h. Integration: spot reclaim + per-slot relaunch**

Mock one instance as "terminated" after 2 polls. Verify: stale claims reclaimed, new instance launched for that slot, other slots unaffected.

**8i. Integration: partial capacity**

Mock 2 of 4 launches raising `InsufficientInstanceCapacity`. Verify run proceeds with 2 workers.

**8j. Integration: SIGTERM terminates all**

Trigger `_shutdown_requested`. Verify `_safe_terminate` called for all running instances.

**8k. Dashboard: workers field**

Test form accepts 1–4, rejects 0 and 5.

**8l. Dashboard: dry run multi-estimate**

Test dry run output includes time estimates for 1/2/4 workers.

**8m. Dashboard: aggregate progress from work_queue**

Test progress partial queries work_queue and returns correct completed/total.

**8n. Concurrency: stale reclaim race**

Mock scenario: instance A terminated → reclaim → instance B claims from same pool. Verify no duplicate claims (mocked SKIP LOCKED behavior).

### Step 9: Migration DDL (Documentation Only)

The builder does NOT run DDL. Instead, include the migration SQL in the PR description and in a file `lavandula/migrations/0055_work_queue.sql` for the operator to run manually.

Contents: the full DDL from the spec's Migration section (CREATE TABLE, CREATE INDEX, ALTER TABLE, GRANT, RLS policies).

## Acceptance Criteria Mapping

| AC# | Spec Requirement | Plan Step |
|-----|-----------------|-----------|
| 1 | `--workers N` flag | Step 3a, 3b |
| 2 | Work queue table DDL | Step 9 |
| 3 | Queue population | Step 1b, 3c |
| 4 | SKIP LOCKED fetch | Step 1c |
| 5 | N instances launched | Step 3d, 3e |
| 6 | No double-processing | Step 1c (CTE), Step 8a |
| 7 | Per-slot relaunch | Step 3e |
| 8 | Stale claim recovery | Step 1e, 3e |
| 9 | Aggregate Job progress | Step 3e, 1f |
| 10 | Dashboard workers field | Step 5 |
| 11 | Per-instance status | Step 6c, 7b |
| 12 | Dry run multi-estimate | Step 3k, 6a |
| 13 | --workers 1 backward compat | Step 2d, 3d |
| 14 | SIGTERM all | Step 3f |
| 15 | Max hours all | Step 3e |
| 16 | Partial launch | Step 3d, 3e |
| 17 | 0 eligible = no launch | Step 3d |

## Security Hardening

1. **Error strings to `work_queue.error`** — Always use short classification strings (`"corrupt_pdf"`, `"docling_timeout"`, `"empty_parse"`), never raw `str(e)` or stack traces. `complete_work_item()` truncates at 200 chars.
2. **No `logger.exception()` to DB** — Detailed stack traces go to `/var/log/docling-worker.log` on the GPU instance, not to RLS-visible DB columns.
3. **Worker argument validation** — `--worker-id` must match `^i-[0-9a-f]+$`. `--run-id` must be a positive integer. Invalid values → immediate exit before any DB operations.
4. **RLS session variable** — Worker sets `SET app.worker_id = %s` via parameterized query (no injection risk). The RLS policy uses `current_setting('app.worker_id', true)` which returns NULL if unset (safe default — no rows match NULL).

## Consultation Log

(Pending — will be populated during review cycle)

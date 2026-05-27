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

Rename the existing function at line 48. Keep the same signature and behavior. This preserves backward compatibility for the worker's legacy mode (when `--worker-id` is not provided).

**Call site audit:** `fetch_work_batch` is called in exactly two places:
1. `lavandula/parse/worker.py:84` — `_run_loop()` function. This becomes `fetch_work_batch_legacy()` (legacy mode path).
2. No other callers (verified via `grep -rn "fetch_work_batch" lavandula/`).

The new `fetch_work_batch()` (SKIP LOCKED) is called only from `_run_loop_queue()` (new queue mode path). The two paths are mutually exclusive (selected by `--worker-id` presence), so there is no risk of accidental breakage.

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

**3c. Add `_populate_or_resume_queue()` method**

Queue population has two modes. The orchestrator detects which by checking existing queue state:

```python
def _populate_or_resume_queue(self, conn, run_id, priority, ntee_filter):
    """Populate work queue or resume from existing queue."""
    # Check if queue already has rows for this run_id
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM lava_parse.work_queue WHERE run_id = %s",
            (run_id,),
        )
        existing = cur.fetchone()[0]

    if existing > 0:
        # Resume: reclaim stale claims from previous orchestrator session
        reclaimed = db.reclaim_all_stale_claims(conn, run_id)
        self.stdout.write(f"Resuming: {existing} queue items, {reclaimed} stale claims reclaimed\n")
        return existing
    else:
        # Fresh run (or resume where previous attempt failed before populating)
        count = db.populate_work_queue(conn, run_id, priority, ntee_filter)
        self.stdout.write(f"Populated work queue: {count} items\n")
        return count
```

This handles the edge case Gemini identified: a resumed run where the previous attempt crashed before queue population still gets a fresh populate.

**3d. Refactor `_execute_run()` for multi-instance**

Replace the current single-instance flow (lines 223–405) with:

1. Create/resume parse_run (existing `create_parse_run()`)
2. Populate or resume queue (call `_populate_or_resume_queue()`)
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
        # State machine for stale detection:
        #   1. Instance state == "running" (EC2 says it's up)
        #   2. Worker has claimed work (claimed_by = iid exists in work_queue)
        #   3. No completed_at update in HEARTBEAT_STALE_MINUTES
        #   4. Unclaimed work still exists (queue not exhausted)
        # All 4 conditions must be true → terminate instance + reclaim + relaunch.
        # If condition 4 is false (no unclaimed work), the worker may be
        # processing its final batch — stale heartbeat is expected. Don't terminate.
        if slot["status"] == "running":
            last_activity = self._get_worker_last_activity(conn, run_id, iid)
            progress = db.get_queue_progress(conn, run_id)
            unclaimed = progress["total"] - progress["claimed"]
            has_claimed_work = any(...)  # worker has incomplete claims
            stale = (
                last_activity is not None
                and (time.time() - last_activity.timestamp()) > HEARTBEAT_STALE_MINUTES * 60
                and unclaimed > 0  # other work exists, so stalling is not "finished"
            )
            if stale:
                self.stdout.write(f"Worker stale on {iid}. Terminating.\n")
                self._safe_terminate(ec2, iid)
                db.reclaim_stale_claims(conn, run_id, iid)
                slot["relaunch_count"] += 1
                if slot["relaunch_count"] > MAX_RELAUNCH_ATTEMPTS:
                    slot["status"] = "capacity_exhausted"
                else:
                    new_id = self._launch_with_capacity_retry(...)
                    if new_id:
                        slot["instance_id"] = new_id
                    else:
                        slot["status"] = "capacity_exhausted"

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

**Caching:** The progress partial is polled via HTMX every 10 seconds. The `get_queue_progress()` and `get_per_worker_stats()` queries hit the work_queue table which may have 10K–100K rows. To avoid excessive DB load:
- Cache the per-worker stats in Django's cache framework with a 10-second TTL (matches poll interval)
- The aggregate progress query (single COUNT with filters) is lightweight and doesn't need caching
- Use `cache_key = f"parse_progress_{run_id}"` to avoid stale data across runs

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

**8a. SKIP LOCKED claim no overlap (PostgreSQL integration test)**

This test MUST run against a real PostgreSQL database to validate the `FOR UPDATE SKIP LOCKED` guarantee. It cannot be meaningfully tested with SQLite or mocks.

Mark with `@skipUnless(connection.vendor == 'postgresql', 'SKIP LOCKED requires PostgreSQL')`.

Test approach:
1. Insert 20 work_queue rows for a test run_id
2. Open two concurrent psycopg2 connections (not Django ORM)
3. In connection A: call `fetch_work_batch(conn_a, run_id, 10, "worker-a")` inside a transaction (do NOT commit yet)
4. In connection B: call `fetch_work_batch(conn_b, run_id, 10, "worker-b")` — should return the OTHER 10 rows
5. Commit both
6. Assert: union of A's batch + B's batch = all 20 rows, intersection = empty

This is the single most important test in this spec — it validates the core no-overlap guarantee.

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

### Step 9: Migration DDL

The builder creates `lavandula/migrations/0055_work_queue.sql` containing the full DDL from the spec's Migration section: CREATE TABLE, CREATE INDEX, ALTER TABLE (instance_ids), GRANT, and RLS policies. The builder does NOT run this DDL — the operator applies it manually on RDS before deployment.

Additionally, add a Django migration file `lavandula/dashboard/pipeline/migrations/0011_parse_runs_instance_ids.py` that is a **no-op stub** documenting the `parse_runs.instance_ids` column addition. Since `parse_runs` lives in the `lava_parse` schema (not managed by Django), this migration exists solely for documentation and to keep the migration sequence consistent. The actual schema change is in the SQL file above.

```python
# 0011_parse_runs_instance_ids.py
class Migration(migrations.Migration):
    dependencies = [('pipeline', '0010_add_parse_phase')]
    operations = [
        migrations.RunSQL(
            sql=migrations.RunSQL.noop,
            reverse_sql=migrations.RunSQL.noop,
            state_operations=[],
        ),
    ]
```

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

### Round 1 — Plan Review (2026-05-27)

**Codex (REQUEST_CHANGES, MEDIUM confidence):** 6 findings.
1. `parse_runs.instance_ids` migration not assigned to a concrete step → **Fixed:** Step 9 now creates both SQL migration file and Django no-op stub migration.
2. Queue populate/resume semantics ambiguous → **Fixed:** Step 3c rewritten as `_populate_or_resume_queue()` with explicit check: if queue rows exist → reclaim stale; if empty → populate fresh.
3. Worker changes broader than "minimal" — call site audit needed → **Fixed:** Step 1a now includes explicit call site audit (exactly 1 caller: `worker.py:84`).
4. Heartbeat/stale detection algorithm needs tighter state machine → **Fixed:** Step 3e stale detection now has 4 explicit conditions, including "unclaimed work still exists" guard to prevent premature termination of a worker processing its final batch.
5. Need real PostgreSQL integration test for SKIP LOCKED → **Fixed:** Step 8a rewritten as PostgreSQL-only integration test with two concurrent connections.
6. Dashboard work_queue query volume/caching unspecified → **Fixed:** Step 6c now specifies 10-second cache TTL for per-worker stats.

**Gemini (COMMENT, HIGH confidence):** 1 clarification.
1. Queue population on resume when queue is empty → **Fixed:** Same as Codex #2 above — `_populate_or_resume_queue()` handles this case.

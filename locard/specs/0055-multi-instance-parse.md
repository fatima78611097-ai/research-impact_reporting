# Spec 0055: Multi-Instance Parse (SKIP LOCKED Work Claiming)

**Status:** Draft
**Author:** Architect
**Created:** 2026-05-27
**Dependencies:** 0054 (Parse Dashboard)

## Problem Statement

Docling GPU parsing currently runs on a single g6.2xlarge spot instance. A single NTEE major category (P — Human Services) has 11,072 unparsed annual/impact documents requiring ~45 hours of GPU time. The full corpus across all verticals will exceed 100K documents. At single-instance throughput (~250 docs/hour), processing the full backlog would take 400+ hours (17 days of continuous operation).

The current architecture cannot scale horizontally because:

1. **No work claiming** — `fetch_work_batch()` uses `NOT IN (SELECT content_sha256 FROM lava_parse.documents)` with `ORDER BY` and `LIMIT`. Two workers running the same query get the same batch and double-parse the same documents.
2. **Single-holder advisory locks** — `DOCLING_PARSE_WORKER` and `DOCLING_PARSE_ORCHESTRATOR` are `pg_try_advisory_lock` calls that prevent a second worker or orchestrator from running at all.
3. **Single instance tracking** — `parse_runs.instance_id` stores one instance ID. The orchestrator poll loop manages one instance lifecycle (launch → poll → relaunch on spot reclaim → terminate).
4. **Dashboard assumes one instance** — the progress display shows a single instance's state and a single progress bar.

## Goals

1. **N concurrent GPU workers** — launch 1–4 spot instances simultaneously, each running the Docling worker independently. Workers self-coordinate through the database with no direct inter-process communication.
2. **SKIP LOCKED work claiming** — replace the current `NOT IN` batch query with a `FOR UPDATE SKIP LOCKED` pattern using a lightweight work queue table. Each worker atomically claims a batch that no other worker can see.
3. **Orchestrator manages N instances** — the `parse_documents` management command launches N instances, monitors all of them in its poll loop, relaunches individually on spot reclaims, and terminates all when the run completes or max hours is reached.
4. **Dashboard shows fleet status** — the parse dashboard displays per-instance health (running/terminated/spot-reclaimed), per-instance throughput, and aggregate progress across all workers.
5. **Graceful degradation** — if some instances fail to launch (capacity), the run proceeds with however many launched successfully. If all fail, the run fails.
6. **Backward compatible** — `--workers 1` (the default) behaves identically to the current single-instance flow. No change to default behavior.

## Non-Goals

- Auto-scaling based on queue depth (fixed N for the run)
- Cross-AZ instance distribution optimization (use the single configured subnet)
- Worker-to-worker communication (all coordination via DB)
- Changes to Docling processing logic or PDF handling
- Multi-region support
- Priority queuing between concurrent runs (only one run at a time, enforced by existing Job system)
- Changes to the parse worker's internal batch processing loop (it already processes one batch at a time; we only change how it claims the batch)

## Hard Constraints (DO NOT VIOLATE)

1. **Use the existing `Job` model.** One parse run = one Job record, regardless of how many instances it uses. Do NOT create per-instance Job records.
2. **Use the existing `parse_runs` table.** Extend it with instance tracking (see Technical Design). Do NOT create new Django models for instance management.
3. **Work claiming lives in `lava_parse` schema.** The new work queue table is a parse-infrastructure table, not a dashboard table. The worker (which runs outside Django on the GPU instance) must be able to read/write it with raw psycopg2.
4. **No new advisory locks for per-worker coordination.** `SKIP LOCKED` replaces the need for advisory locks in work distribution. The orchestrator lock (`DOCLING_PARSE_ORCHESTRATOR`) remains to prevent concurrent orchestrator processes.
5. **The worker code (`lavandula/parse/worker.py`) changes are minimal.** The worker calls `fetch_work_batch()` → processes → repeats. The change is inside `fetch_work_batch()` (use SKIP LOCKED instead of NOT IN). The worker's processing loop, error handling, and stats reporting stay the same.
6. **parse_documents.py remains one management command.** Do NOT split into separate commands for orchestration vs. instance management. The single command manages N instances in its poll loop.

## Technical Design

### Work Queue Table: `lava_parse.work_queue`

A lightweight table that the orchestrator populates at run start and workers claim from using `SKIP LOCKED`.

```sql
CREATE TABLE lava_parse.work_queue (
    id              BIGSERIAL PRIMARY KEY,
    run_id          INTEGER NOT NULL REFERENCES lava_parse.parse_runs(id),
    content_sha256  TEXT NOT NULL,
    source_org_ein  TEXT NOT NULL,
    claimed_by      TEXT,          -- instance_id of the worker that claimed this row
    claimed_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    error           TEXT,
    UNIQUE (run_id, content_sha256)
);

CREATE INDEX idx_work_queue_unclaimed ON lava_parse.work_queue (run_id)
    WHERE claimed_by IS NULL;
```

**Why a queue table instead of modifying `fetch_work_batch` in-place?**

The current query (`NOT IN (SELECT content_sha256 FROM documents)`) is a set-difference operation — it computes eligible docs on every call. You can't `SELECT ... FOR UPDATE SKIP LOCKED` on rows that don't exist yet. A queue table materializes the work set upfront, giving us real rows to lock.

### Lifecycle

```
1. Orchestrator starts
2. Orchestrator queries eligible docs (existing NOT IN query)
3. Orchestrator bulk-inserts eligible docs into work_queue for this run_id
4. Orchestrator launches N spot instances, each running the worker
5. Each worker:
   a. Claims a batch: SELECT ... FROM work_queue WHERE run_id = %s AND claimed_by IS NULL
      FOR UPDATE SKIP LOCKED LIMIT %s
   b. UPDATE claimed_by = %s, claimed_at = NOW() for claimed rows
   c. Downloads PDFs, parses with Docling
   d. Writes results to documents/sections/tables (existing insert_document)
   e. UPDATE completed_at = NOW() for completed rows (or error for failures)
   f. Repeats from (a) until no unclaimed rows remain
6. Orchestrator polls:
   - Checks work_queue progress (COUNT completed vs total)
   - Checks each instance state (running/terminated)
   - Relaunches individual instances on spot reclaim
   - Reports aggregate progress to Job
7. Run completes when work_queue is fully processed or max_hours reached
8. Orchestrator terminates all instances and cleans up
```

### Worker Changes (`lavandula/parse/worker.py` + `db.py`)

**`db.fetch_work_batch()` — new implementation:**

```python
def fetch_work_batch(
    conn, run_id: int, batch_size: int, worker_id: str
) -> list[dict]:
    """Claim next batch of unclaimed work items using SKIP LOCKED."""
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

**Key properties:**
- Atomic claim+update in one statement (CTE)
- `SKIP LOCKED` means concurrent workers never block each other and never claim the same rows
- `ORDER BY id` gives deterministic, non-overlapping batches
- Worker calls this with its own `instance_id` as `worker_id`

**`db.complete_work_item()` — new function:**

```python
def complete_work_item(conn, run_id: int, content_sha256: str, error: str | None = None):
    """Mark a work queue item as completed (or errored)."""
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

**Worker changes:**
- Accept `--run-id` and `--worker-id` arguments (passed by orchestrator via SSM)
- Call new `fetch_work_batch(conn, run_id, batch_size, worker_id)` instead of old signature
- Call `complete_work_item()` after each document (success or permanent error)
- Still writes to `documents/sections/tables` via existing `insert_document()` — no change there

**Backward compatibility:** The old `fetch_work_batch()` signature (with priority_filter, ntee_filter, etc.) is kept as `fetch_work_batch_legacy()` for any non-dashboard CLI usage. The worker detects which mode based on whether `--run-id` is provided.

### Orchestrator Changes (`parse_documents.py`)

**New CLI argument:**
```
--workers N    Number of concurrent GPU instances (default: 1, max: 4)
```

**Queue population at run start:**
```python
def _populate_work_queue(self, conn, run_id, priority, ntee_filter):
    """Materialize eligible docs into work_queue for this run."""
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO lava_parse.work_queue (run_id, content_sha256, source_org_ein)
                SELECT c.content_sha256, c.source_org_ein
                FROM lava_corpus.corpus c
                JOIN lava_corpus.nonprofits_seed ns ON c.source_org_ein = ns.ein
                WHERE c.content_sha256 NOT IN (
                    SELECT content_sha256 FROM lava_parse.documents
                )
                  AND c.classification = ANY(%(priority)s)
                  AND ns.ntee_code LIKE %(ntee)s
                ON CONFLICT (run_id, content_sha256) DO NOTHING
                """,
                {"priority": priority, "ntee": ntee_filter, "run_id": run_id},
            )
            return cur.rowcount
```

**Multi-instance poll loop:**
```python
# Instead of tracking one instance_id, track a dict:
# instances = {instance_id: {"status": "running", "launched_at": time.time()}}

while True:
    # Check each instance
    for iid in list(instances):
        state = self._get_instance_state(ec2, iid)
        if state in ("terminated", "shutting-down"):
            # Attempt relaunch for this slot
            new_id = self._launch_with_capacity_retry(...)
            if new_id:
                instances[new_id] = {"status": "running", ...}
            del instances[iid]

    # Check aggregate progress from work_queue
    progress = self._get_queue_progress(conn, run_id)
    # progress = {total, claimed, completed, errored}

    if progress["completed"] + progress["errored"] >= progress["total"]:
        break  # All work done

    if elapsed >= max_seconds:
        break  # Time limit

    # Update Job progress
    self._update_job_progress(job_id, progress["completed"], progress["total"])
```

**Instance tagging:** Each instance gets a unique tag: `docling-worker-{run_tag}-{slot}` where slot is 0, 1, 2, 3. This prevents instances from different slots interfering with each other during `_find_instance` lookups.

### parse_runs Table Extension

Add a column to track multiple instance IDs:

```sql
ALTER TABLE lava_parse.parse_runs ADD COLUMN instance_ids TEXT[];
-- Replaces the single instance_id column for multi-instance runs
-- Single-instance runs still populate instance_id for backward compat
```

### Dashboard Changes (`views.py` + templates)

**Form:** Add "Workers" field (integer, 1–4, default 1).

**Progress display:**
- Aggregate progress bar (from work_queue completed/total, not parse_runs.stats_json)
- Per-instance status cards showing: instance ID, state (running/terminated/relaunching), docs claimed, docs completed, throughput (docs/min)
- Fleet summary: N instances running, N spot-reclaimed, total throughput

**Dry run output:** Add estimated time with N workers:
```
Dry run: 11,072 eligible docs, ~332,160 pages
  1 worker: ~45h GPU time, ~$27 (spot)
  2 workers: ~23h wall time, ~$27 (spot)
  4 workers: ~12h wall time, ~$27 (spot)
```
Note: total GPU cost is the same regardless of worker count — you're just parallelizing the wall-clock time.

### State Ownership (Extended from Spec 0054)

```
work_queue      → work distribution truth (who claimed what, what's done)
parse_runs      → run-level progress truth (aggregate stats)
Job             → lifecycle truth (running/completed/failed/cancelled)
EC2 instances   → ephemeral (cached 30s, any can be replaced)
```

## Edge Cases

1. **Partial launch** — Request 4 workers but only 2 get capacity. Run proceeds with 2. Dashboard shows "2/4 workers active."
2. **All instances spot-reclaimed simultaneously** — Orchestrator relaunches up to MAX_RELAUNCH_ATTEMPTS per slot. If all slots exhausted, run fails.
3. **Worker crashes mid-batch** — Claimed but uncompleted rows remain in work_queue. Orchestrator detects via heartbeat staleness, terminates instance, launches replacement. The replacement worker claims NEW unclaimed rows (stale claimed rows are NOT automatically reclaimed — see Stale Claim Recovery).
4. **Stale claim recovery** — If a worker claims rows but dies before completing them, those rows have `claimed_by` set but no `completed_at`. The orchestrator reclaims these when it detects the worker's instance is terminated: `UPDATE work_queue SET claimed_by = NULL, claimed_at = NULL WHERE claimed_by = %s AND completed_at IS NULL`. This allows a replacement worker to pick them up.
5. **Run stopped from dashboard** — SIGTERM to orchestrator. Orchestrator terminates all instances. Uncompleted work remains in queue. A future run with the same filters will re-process them (they're not in `documents` table, so they're still eligible).
6. **Max hours reached** — Orchestrator terminates all instances. Partial progress is preserved in `documents` table. Next run picks up where this one left off.
7. **Duplicate work_queue entries** — `ON CONFLICT (run_id, content_sha256) DO NOTHING` prevents duplicates during queue population. If a run is resumed, existing unclaimed entries are reused.
8. **Zero eligible docs** — Queue population inserts 0 rows. Orchestrator reports 0 eligible, does not launch any instances.
9. **Worker finishes all its claimed work** — It calls `fetch_work_batch` again, gets more unclaimed rows. When no unclaimed rows remain, it exits cleanly.
10. **Single worker mode (--workers 1)** — SKIP LOCKED still works correctly with one worker (no contention, just claims everything sequentially). Functionally identical to current behavior.

## Acceptance Criteria

1. `--workers N` flag accepted by `parse_documents` command (N = 1–4)
2. Work queue table created via SQL migration (operator-applied DDL)
3. Orchestrator populates work_queue from eligible docs at run start
4. `fetch_work_batch` uses `FOR UPDATE SKIP LOCKED` on work_queue
5. N spot instances launched concurrently (up to requested count, subject to capacity)
6. Each worker independently claims and processes batches with no double-processing
7. Orchestrator polls all instances and relaunches individually on spot reclaim
8. Stale claims recovered when a worker's instance is confirmed terminated
9. Job progress reflects aggregate completed/total across all workers
10. Dashboard form has "Workers" field (1–4, default 1)
11. Dashboard progress shows per-instance status and aggregate throughput
12. Dry run shows estimated wall-clock time for 1/2/4 workers
13. `--workers 1` behaves identically to current single-instance flow
14. SIGTERM terminates all running instances (not just one)
15. Max hours terminates all running instances
16. Partial launch (fewer workers than requested) proceeds with available capacity
17. Run with 0 eligible docs does not launch any instances

## Traps to Avoid

1. **Do NOT use advisory locks for per-worker coordination.** `SKIP LOCKED` is the coordination mechanism. Advisory locks are global and would serialize workers, defeating the purpose.
2. **Do NOT modify `insert_document()`.** It's an atomic per-document transaction with a unique key (`content_sha256`). Two workers will never insert the same document because SKIP LOCKED prevents them from claiming the same work item.
3. **Do NOT create per-instance Job records.** One run = one Job. Instance count is an internal implementation detail of that Job.
4. **Do NOT populate the work queue per-worker.** The orchestrator populates once for the entire run. Workers only claim from it.
5. **Do NOT auto-reclaim stale work on a timer.** Only reclaim when the orchestrator confirms the worker's instance is terminated. Timer-based reclaim risks duplicate processing if a slow worker is still alive.
6. **Do NOT change the worker's stats_json reporting to parse_runs.** Each worker still updates parse_runs.stats_json independently. The orchestrator aggregates progress from work_queue (completed count), not from stats_json. stats_json remains useful for per-worker throughput monitoring.
7. **Do NOT launch instances in different AZs/subnets.** The current SUBNET_ID is a single subnet. Multi-AZ would require VPC/networking changes out of scope.

## Testing Requirements

1. **Unit: SKIP LOCKED claim** — Two concurrent transactions claim batches from the same queue; verify zero overlap
2. **Unit: Queue population** — Eligible docs correctly materialized; duplicates handled via ON CONFLICT
3. **Unit: Stale claim recovery** — Reclaim marks rows as unclaimed only for terminated instance
4. **Unit: Complete work item** — Sets completed_at and error correctly
5. **Integration: Single worker** — `--workers 1` processes all docs (regression test)
6. **Integration: Multi-worker launch** — N instances launched with unique tags
7. **Integration: Spot reclaim + relaunch** — One instance terminated, replacement launched, work continues
8. **Integration: Partial capacity** — Request 4, get 2, run proceeds
9. **Integration: SIGTERM all** — All instances terminated on shutdown
10. **Integration: Max hours all** — All instances terminated when time limit reached
11. **Dashboard: Workers field** — Form accepts 1–4, passes to orchestrator
12. **Dashboard: Per-instance status** — Each instance shown with state and throughput
13. **Dashboard: Aggregate progress** — Total completed/total shown correctly
14. **Dashboard: Dry run multi-estimate** — Shows wall-clock time for 1/2/4 workers

## Migration (Operator-Applied DDL)

```sql
-- Run on RDS as superuser/migration role

CREATE TABLE lava_parse.work_queue (
    id              BIGSERIAL PRIMARY KEY,
    run_id          INTEGER NOT NULL REFERENCES lava_parse.parse_runs(id),
    content_sha256  TEXT NOT NULL,
    source_org_ein  TEXT NOT NULL,
    claimed_by      TEXT,
    claimed_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    error           TEXT,
    UNIQUE (run_id, content_sha256)
);

CREATE INDEX idx_work_queue_unclaimed ON lava_parse.work_queue (run_id)
    WHERE claimed_by IS NULL;

ALTER TABLE lava_parse.parse_runs ADD COLUMN instance_ids TEXT[];

-- Grant to existing roles
GRANT SELECT, INSERT, UPDATE, DELETE ON lava_parse.work_queue TO docling_writer;
GRANT USAGE, SELECT ON SEQUENCE lava_parse.work_queue_id_seq TO docling_writer;
GRANT SELECT ON lava_parse.work_queue TO dashboard_reader;
```

## Consultation Log

(Pending — will be populated during review cycle)

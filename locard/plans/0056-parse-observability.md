# Plan 0056 — Parse Orchestrator Reliability & Observability

- **Project:** 0056   **Spec:** `locard/specs/0056-parse-observability.md` (specified)
- **Status:** conceived (initial draft)
- **Author:** Architect, 2026-05-30

> Builder-executable plan. Heartbeat thread, smart relaunch budget, death classification, exit reason, log shipping, dashboard integration. Worker-side changes require tarball rebuild + deploy.

## Key decisions

1. **Heartbeat table, not a column on parse_runs.** A separate `worker_heartbeats` table with `(instance_id, run_id)` PK. Cleaner for multi-worker runs (each worker has its own row). Orchestrator polls it per slot.
2. **Heartbeat thread is a daemon `threading.Thread`** that wakes every 60s. Shares `docs_completed` and `current_doc_sha` with the main loop via a simple `threading.Lock`. The heartbeat thread does NOT touch Docling or the work queue — it only writes to `worker_heartbeats`.
3. **Per-slot failure counters are in-memory** in the orchestrator's `_slots` dict. Reset to 0 on any successful completion from that slot. If orchestrator restarts, counters reset — this is fail-safe (allows relaunches, doesn't suppress them).
4. **exit_reason cross-check:** when worker reports `empty_batch`, orchestrator verifies `get_eligible_count() > 0`. If mismatch → WARNING + relaunch (don't trust the worker's claim).
5. **Log shipping is SSM-primary, atexit-fallback.** Orchestrator pulls via SSM before terminating. Worker's atexit is belt-and-suspenders.
6. **DDL is operator-run.** Two migrations: heartbeat table + exit_reason column.
7. **Worker tarball rebuild required.** Heartbeat thread + exit_reason + atexit log shipper are worker-side. Must rebuild and deploy `worker-code.tar.gz` to S3.

## Phase 1 — Heartbeat: worker side

**`lavandula/parse/worker.py`:**
- Add `HeartbeatThread` class (daemon thread):
  - Constructor takes `engine`, `instance_id`, `run_id`, `interval=60`
  - Shared state: `docs_completed: int`, `current_doc_sha: str | None` (protected by `threading.Lock`)
  - `run()`: loop sleeping `interval` seconds, then `UPDATE lava_parse.worker_heartbeats SET last_heartbeat = now(), docs_completed = :n, current_doc_sha = :sha WHERE instance_id = :iid AND run_id = :rid`. If no row affected, INSERT (upsert pattern).
  - Catches all DB exceptions (log warning, never crash)
- In `_run_loop_queue()`: start heartbeat thread before the main loop. After each doc completes, update shared state (`docs_completed += 1`, `current_doc_sha = next_sha`).
- Heartbeat thread stops automatically when main thread exits (daemon=True).

**`lavandula/parse/db.py`:**
- Add `upsert_heartbeat(conn, instance_id, run_id, docs_completed, current_doc_sha)` — INSERT ... ON CONFLICT UPDATE.

**Tests:** heartbeat thread fires on schedule (mock sleep); shared state updates correctly; DB failure doesn't crash thread; thread dies with main process.

**Acceptance:** heartbeat writes every 60s independent of doc processing speed.

## Phase 2 — Heartbeat: orchestrator side (stale detection rewrite)

**`lavandula/dashboard/pipeline/management/commands/parse_documents.py`:**
- Replace `_get_worker_last_activity()` / `_get_worker_open_claim_age()` with `_get_heartbeat_age(instance_id, run_id)` — queries `worker_heartbeats.last_heartbeat`.
- New stale rule: `heartbeat_age > HEARTBEAT_STALE_MINUTES` (default 5 min).
- Fallback: if no `worker_heartbeats` row exists (legacy worker), use the old completion-based check with 30-min timeout.
- Remove `HEARTBEAT_STALE_MINUTES=20` constant, replace with `HEARTBEAT_STALE_MINUTES=5`.

**Tests:** recent heartbeat → not stale; stale heartbeat → stale; no heartbeat row → fallback check with 30-min threshold; heartbeat exactly at boundary.

**Acceptance:** a worker grinding a 600s doc with heartbeats firing is never marked stale.

## Phase 3 — Smart relaunch budget

**`parse_documents.py`:**
- Add `consecutive_failures` counter to each slot in `self._slots` dict (default 0).
- On worker death: increment `consecutive_failures` for that slot.
- On successful completion from a slot (any doc): reset `consecutive_failures` to 0.
- Relaunch decision: if `consecutive_failures >= MAX_CONSECUTIVE_FAILURES` (default 3) → mark slot `abandoned`, log reason, don't relaunch.
- Remove the global `MAX_RELAUNCH_ATTEMPTS` counter entirely.
- Run ends when all slots are either `abandoned` or `completed` (not when a global counter is exhausted).

**Tests:** counter resets on progress; counter increments on death; slot abandoned at 3; other slots unaffected; run ends when all abandoned.

**Acceptance:** a 15-hour run with occasional spot churn never exhausts relaunches as long as each slot makes progress.

## Phase 4 — Death classification

**`parse_documents.py`:**
- Implement the detection ordering from spec §3.4:
  1. Check `exit_reason` in `parse_runs` → graceful exit, don't relaunch
  2. Check EC2 instance state via `describe_instances`:
     - `shutting-down` or `terminated` → check spot interruption (instance metadata via SSM or `describe_spot_instance_requests`) → `spot_reclaim` or `crash_or_oom`
  3. Check heartbeat age → `hang` if stale
- Map classification to relaunch policy per spec table.
- Log the classification for each death event.

**Spot detection simplified:** check if instance `InstanceLifecycle == 'spot'` and state is terminated without an orchestrator-initiated termination → likely spot reclaim. Don't need instance metadata query (SSM may fail on a terminated instance). If unsure, classify as `crash_or_oom` — fail-safe (relaunches).

**Tests:** mock EC2 responses for each classification; spot reclaim detected; crash detected; hang detected; graceful exit skipped.

**Acceptance:** each worker death is classified and logged before relaunch decision.

## Phase 5 — Exit reason tracking

**`lavandula/parse/worker.py`:**
- Track `exit_reason` string through the main loop. Set at each break point:
  - `empty_batch` when `fetch_work_batch` returns no rows
  - `spot_termination` when spot notice detected
  - `max_docs` when doc cap reached
  - `error` in the top-level catch-all
- Pass to `db.finish_run(conn, run_id, stats, exit_reason=exit_reason)`.

**`lavandula/parse/db.py`:**
- Add `exit_reason` param to `finish_run()` (default `"unknown"`).
- Add `set_exit_reason(conn, run_id, reason)` for orchestrator overrides.
- Add `get_exit_reason(conn, run_id)` for orchestrator reads.

**`parse_documents.py`:**
- On max_hours: `set_exit_reason(conn, run_id, "max_hours")`
- On cancelled (SIGTERM): `set_exit_reason(conn, run_id, "cancelled")`
- On worker completion: read exit_reason, log with remaining count, cross-check `empty_batch` vs `get_eligible_count`.

**Tests:** each exit path sets correct reason; orchestrator override wins; cross-check fires warning on mismatch.

**Acceptance:** every run has an exit_reason; dashboard displays it.

## Phase 6 — Log shipping

**`lavandula/parse/worker.py`:**
- Add `atexit.register(_ship_log_on_exit, run_tag, instance_id)` in `main()`.
- `_ship_log_on_exit` uploads `/var/log/docling-worker.log` to S3 via boto3. Best-effort (bare except, pass).

**`parse_documents.py`:**
- Add `_pull_worker_log(ssm, instance_id, run_tag)` — SSM command to `aws s3 cp` the log. Called before `_safe_terminate`.
- Validate `run_tag` matches `^[a-zA-Z0-9_-]+$` before interpolation. Reject and log warning if invalid.
- Wait 5s after SSM command for upload to complete.
- Store S3 log path in `parse_runs.stats_json` for dashboard link.

**Tests:** run_tag validation rejects injection; SSM failure handled gracefully (warning, not crash); log path stored in stats_json.

**Acceptance:** worker log available in S3 after normal run completion.

## Phase 7 — Migration SQL (operator-run)

`lavandula/migrations/parse/0056_reliability.sql`:

```sql
BEGIN;

CREATE TABLE IF NOT EXISTS lava_parse.worker_heartbeats (
    instance_id     TEXT NOT NULL,
    run_id          INTEGER NOT NULL,
    last_heartbeat  TIMESTAMPTZ NOT NULL DEFAULT now(),
    docs_completed  INTEGER DEFAULT 0,
    current_doc_sha TEXT,
    PRIMARY KEY (instance_id, run_id)
);

ALTER TABLE lava_parse.parse_runs
    ADD COLUMN IF NOT EXISTS exit_reason TEXT;

GRANT SELECT, INSERT, UPDATE ON lava_parse.worker_heartbeats TO docling_writer;
GRANT SELECT ON lava_parse.worker_heartbeats TO research_app;
GRANT UPDATE (exit_reason) ON lava_parse.parse_runs TO docling_writer;

COMMIT;
```

Rollback included. Operator applies manually.

## Phase 8 — Dashboard enhancements

**`lavandula/dashboard/pipeline/templates/pipeline/` (parse progress partial):**
- Display exit_reason as human-readable label on completed/failed runs
- Show S3 log link (presigned URL or path) on runs where log was shipped
- Per-worker heartbeat age and current doc in the parse progress view
- Per-slot relaunch counter in the worker status table

**`views.py`:**
- Query `worker_heartbeats` for heartbeat status display
- Generate presigned S3 URL for log download link

**Tests:** view renders exit reasons correctly; NULL exit_reason shows "—"; log link present when log exists.

## Phase 9 — Worker tarball rebuild + deploy

After phases 1, 5, 6 are complete:
1. Rebuild `worker-code.tar.gz` with updated `worker.py` + `db.py`
2. Upload to `s3://lavandula-nonprofit-collaterals/deploy/worker-code.tar.gz`
3. Back up old tarball to `deploy/backups/worker-code.{timestamp}.tar.gz`

**This is the deployment step.** The orchestrator already deploys the tarball to GPU instances at launch. New launches pick up the new code automatically.

## Build sequence

Phase 1 (heartbeat worker) + Phase 5 (exit reason worker) + Phase 6 (log shipping worker) → Phase 9 (tarball) → Phase 7 (migration, hand to operator) → Phase 2 (stale rewrite) + Phase 3 (relaunch budget) + Phase 4 (death classification) → Phase 8 (dashboard).

Worker-side phases (1, 5, 6) can proceed in parallel. Orchestrator-side phases (2, 3, 4) can proceed in parallel after migration. Dashboard (8) is last.

## Dependencies & handoffs

- **Operator:** applies Phase 7 migration SQL to RDS
- **Worker tarball:** Phase 9 rebuilds and deploys — coordinate timing (don't deploy mid-run)
- **0058 (Parse Performance):** per-doc timeout pairs with heartbeat but is independent scope

## Risks / traps

- Do NOT remove B1 stale detection — replace the signal (heartbeat), not the mechanism
- Do NOT use a global relaunch counter — per-slot or the fleet erodes
- Heartbeat thread must be `daemon=True` — or it prevents clean worker exit
- SSM log pull before termination — wait 5s for upload
- Worker tarball deploy while a run is active = disaster — check first
- `exit_reason` cross-check is critical — without it, a buggy worker claiming `empty_batch` silently kills the run

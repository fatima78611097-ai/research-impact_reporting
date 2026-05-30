# Spec 0056 — Parse Orchestrator Reliability & Observability

- **Project:** 0056
- **Status:** conceived (multi-agent review + red-team incorporated)
- **Depends on:** 0054 (Parse Dashboard), 0055 (Multi-Instance Parse)
- **Author:** Architect, 2026-05-30

> **Scale gate:** this spec must land before the next full-corpus or national parse run. Run 31 proved the current control plane bleeds out near the finish — not a compute failure, a control-plane failure.

---

## 1. Problem & Motivation

The parse orchestrator has three reliability failures and one observability gap, all exposed by run 31 (8,077 docs, 3 workers, 15.5 hours):

### 1.1 Relaunch budget erosion (THE blocker)

`MAX_RELAUNCH_ATTEMPTS=3` is a **hard per-run cap that never resets**. Over a long run, normal spot churn + stale-detection false positives erode the fleet 3→2→1→0. Run 31 ended `status=failed` at 97% (7,782 ok / 78 err / 217 incomplete) — not because compute failed, but because the control plane refused to replace workers.

The safety nets worked: B4 preserved the queue, B5 terminated all instances, honest `failed` status. The erosion is the bug.

### 1.2 False-positive stale detection

The orchestrator infers worker liveness from `MAX(completed_at)` in the work queue. A worker grinding a slow doc (parse times up to 646s observed) is indistinguishable from a hung/dead worker. Run 31: B1 stale-detection fired twice in ~15 min (slots 0 and 2, each after ~21 min with no completion vs `HEARTBEAT_STALE_MINUTES=20`), relaunching workers that may have been healthy-but-slow. Each false relaunch costs ~6 min model warmup + reclaimed/redone batch.

Cannot remove B1 — a doc that HANGS Docling (no exception) still needs B1 to terminate. Need to make it accurate.

### 1.3 Capacity-death vs bug-death

When a worker dies, the orchestrator doesn't know why. Spot reclaim, OOM kill, Docling crash, and network failure all look the same: "instance gone." The relaunch decision should differ:
- Spot reclaim → always relaunch (normal cloud behavior)
- OOM/crash → relaunch but track; 3 consecutive crashes on the same slot = stop relaunching that slot
- Network/DB issue → don't relaunch, the problem is upstream

### 1.4 Worker logs lost on termination

When a GPU instance is terminated, the worker log at `/var/log/docling-worker.log` is destroyed. Post-mortem diagnosis is impossible. Run 15 investigation consumed significant time and reached no conclusion.

## 2. Scope

**0056 delivers:**
1. **Worker heartbeat** — periodic timestamp independent of doc completions, so the orchestrator can distinguish "hung" from "slow"
2. **Smart relaunch budget** — resets on progress, not a hard per-run cap
3. **Death classification** — spot reclaim vs crash vs hang, with different relaunch policies
4. **Exit reason tracking** — `exit_reason` column on `parse_runs`
5. **Log shipping** — worker log uploaded to S3 before instance termination
6. **Dashboard enhancements** — exit reason display, log link, heartbeat status

**Non-goals:**
- Real-time log streaming / CloudWatch (future enhancement)
- Per-doc timeout (0058 scope — pairs with heartbeat but is a separate concern)
- Changes to worker processing logic or error handling (0055 already shipped A1-A6)

## 3. Requirements

### 3.1 Worker heartbeat

The worker writes a heartbeat timestamp to the database periodically (every 60s), independent of doc completions. This gives the orchestrator a direct liveness signal.

**Implementation:** New table `lava_parse.worker_heartbeats`:
```sql
instance_id     TEXT NOT NULL,
run_id          INTEGER NOT NULL,
last_heartbeat  TIMESTAMPTZ NOT NULL DEFAULT now(),
docs_completed  INTEGER DEFAULT 0,
current_doc_sha TEXT,
PRIMARY KEY (instance_id, run_id)
```

The worker calls `UPDATE ... SET last_heartbeat = now(), docs_completed = N, current_doc_sha = :sha` every 60 seconds in its main loop (between doc processing, not during). If the worker is blocked inside `Docling.convert()`, the heartbeat thread (a separate daemon thread) sends the update.

**Heartbeat thread:** A lightweight daemon thread that wakes every 60s and writes the heartbeat. The main processing loop updates `docs_completed` and `current_doc_sha` in shared state; the heartbeat thread reads and writes them.

**What the heartbeat CAN and CANNOT detect (Codex review):**
- **CAN detect:** process crash (heartbeat stops), instance termination (heartbeat stops), OOM kill (heartbeat stops), network failure (heartbeat DB write fails → stale from orchestrator's perspective)
- **CAN detect (key case):** Docling `convert()` blocking the main thread for 600+ seconds. The heartbeat thread is a separate Python thread — it still fires even while the main thread is blocked in a C extension (Docling/PyTorch). The GIL releases during I/O and C extension calls. So a worker grinding a slow doc sends heartbeats → NOT stale. A worker stuck in an infinite loop in pure Python → GIL blocks the heartbeat thread → heartbeat stops → detected as stale after 5 min. This is the correct behavior: the infinite-loop case IS a hang.
- **CANNOT detect:** a Docling hang that holds the GIL indefinitely in pure Python without releasing (rare — most heavy work is in C/CUDA extensions that release the GIL). In this edge case, the heartbeat thread is also blocked, and the worker appears stale after 5 min → the orchestrator terminates and relaunches, which is the safe behavior (kill what might be hung).
- **Summary:** heartbeat distinguishes "slow but alive" (GIL-releasing, heartbeat fires) from "stuck or dead" (heartbeat stops). The remaining ambiguity (is it truly hung or just holding the GIL?) resolves toward termination, which is fail-safe.

### 3.2 Stale detection rewrite (B1 replacement)

Replace the current B1 stale detection (based on `MAX(completed_at)`) with heartbeat-based detection:

**Stale rule:** a worker is stale when `now() - last_heartbeat > HEARTBEAT_STALE_MINUTES` (default 5 minutes). This means:
- A worker grinding a 600s doc sends heartbeats every 60s → never stale
- A worker hung inside Docling (heartbeat thread still running) → heartbeat keeps firing → not stale until heartbeat thread also dies
- A worker whose process crashed → heartbeat stops → stale after 5 min
- A worker whose instance was terminated → heartbeat stops → stale after 5 min

**Fallback:** If the `worker_heartbeats` table has no row for an instance (legacy worker, pre-0056), fall back to the current completion-based check with a longer timeout (30 min instead of 20).

### 3.3 Smart relaunch budget

Replace `MAX_RELAUNCH_ATTEMPTS=3` (hard per-run cap, never resets) with a progress-aware budget:

**New rule:** each slot gets a **consecutive failure counter** that resets to 0 whenever the slot produces a successful completion. A slot is abandoned (no more relaunches) only when it accumulates `MAX_CONSECUTIVE_FAILURES=3` without any progress.

**Mechanics:**
- Slot launches worker → worker completes ≥1 doc → counter resets to 0
- Slot dies (any reason) → counter increments
- Counter reaches 3 → slot marked `abandoned`, no more relaunches for this slot
- Other slots continue independently — one bad slot doesn't kill the fleet
- The run ends when ALL slots are either `abandoned` or `completed`

**Per-slot vs per-run:** the current cap is per-run (3 total relaunches across all slots). The new cap is per-slot (3 consecutive failures per slot). A 3-worker run can sustain up to 9 relaunches total as long as each slot makes progress between failures.

### 3.4 Death classification

When a worker dies, classify the death before deciding whether to relaunch:

**Detection ordering (deterministic, Codex review):** the orchestrator checks in this exact sequence on each poll cycle. First match wins:

1. **Check exit_reason** — if `exit_reason` is set in `parse_runs`, the worker exited intentionally. Classification = `graceful_exit`. Don't relaunch.
2. **Check instance state** — if the EC2 instance is `terminated` or `shutting-down`:
   a. Check spot interruption notice (instance metadata or CloudTrail) → classification = `spot_reclaim`. Always relaunch.
   b. Otherwise → classification = `crash_or_oom`. Relaunch, increment counter.
3. **Check heartbeat** — if instance is `running`:
   a. Heartbeat exists and age < `HEARTBEAT_STALE_MINUTES` → worker is alive. No action.
   b. Heartbeat exists and age ≥ `HEARTBEAT_STALE_MINUTES` → classification = `hang`. Terminate + relaunch, increment counter.
   c. No heartbeat row (legacy worker) → fall back to completion-based check with 30-min timeout.

| Classification | Detection (per ordering above) | Relaunch policy |
|---|---|---|
| **Graceful exit** | exit_reason set by worker | Don't relaunch |
| **Spot reclaim** | Instance terminated + spot interruption | Always relaunch, increment counter |
| **Crash/OOM** | Instance terminated, no exit_reason | Relaunch, increment counter |
| **Hang** | Instance running, heartbeat stale | Terminate + relaunch, increment counter |

### 3.5 Exit reason tracking

Add `exit_reason` column to `lava_parse.parse_runs` (nullable TEXT). Values: `empty_batch | spot_termination | max_docs | max_hours | cancelled | error | unknown`.

Worker sets it before `finish_run()`. Orchestrator may override with `max_hours` or `cancelled`. Dashboard displays human-readable label.

(Detailed design preserved from the existing spec draft — §Component 1.)

### 3.6 Log shipping to S3

Two-layer approach:
1. **Primary:** Orchestrator pulls log via SSM before terminating instance
2. **Fallback:** Worker `atexit` handler self-ships log

S3 path: `s3://lavandula-nonprofit-collaterals/logs/parse/{run_tag}/{instance_id}/worker.log`

(Detailed design preserved from existing spec draft — §Component 2.)

### 3.7 Dashboard enhancements

- Exit reason displayed on completed/failed runs
- Log link (S3 URL) on completed runs where log was shipped
- Per-worker heartbeat status in the parse progress view (last heartbeat age, current doc)
- Relaunch counter per slot visible in the worker status table

## 4. Security Considerations

- **run_tag injection:** validated `^[a-zA-Z0-9_-]+$` at creation AND at use (defense-in-depth in `_pull_worker_log`). Never interpolated into shell commands without validation.
- **Heartbeat writes:** worker uses parameterized queries (psycopg2 `%s` bindings), never string interpolation. `current_doc_sha` is untrusted (comes from the work queue) but stored as data parameter.
- **S3 log shipping:** uses AWS SDK (`boto3`), not shell commands. No path injection risk. S3 bucket is private (no public access); logs accessible only via IAM role. Dashboard displays log link as an S3 presigned URL (time-limited) or operator copies from S3 directly. No raw S3 paths exposed to untrusted users (single-operator system, but defense-in-depth).
- **S3 log retention:** add lifecycle rule to expire `logs/parse/` objects after 90 days. Operator applies via S3 console.
- **Heartbeat thread:** daemon thread dies with the main process. No zombie risk. Shared state access uses a simple lock (not performance-critical at 60s intervals).
- **Worker identity for heartbeats (red-team HIGH — Gemini):** the `instance_id` is set by the orchestrator when it launches the worker via SSM (passed as a command-line argument). The worker cannot choose its own identity. A compromised worker could write heartbeats for another instance_id, but: (a) the worker runs on an isolated GPU instance with no access to other instances; (b) the DB role (`docling_writer`) has no cross-instance privilege; (c) this is a single-operator system with no untrusted workers. The risk is accepted as negligible.
- **exit_reason trust boundary (red-team — Codex):** a buggy worker could set `exit_reason = 'empty_batch'` and suppress relaunch. Mitigation: the orchestrator ALWAYS cross-checks exit_reason against remaining queue count. If `exit_reason = 'empty_batch'` but `get_eligible_count() > 0`, the orchestrator logs a WARNING and relaunches (overriding the worker's claim). This check already exists in the existing spec draft (§Component 3 of the original spec).
- **Relaunch state durability (red-team — Codex):** per-slot failure counters are stored in-memory in the orchestrator process. If the orchestrator restarts mid-run, counters reset to 0. This is acceptable: (a) orchestrator restarts mid-run are rare (operator-initiated, not automatic); (b) resetting counters is fail-safe (allows relaunches, doesn't suppress them); (c) the run can be manually stopped if a slot is genuinely broken. Plan-phase detail: if durability is needed, store counters in `parse_runs.stats_json`.
- **stderr/log sanitization:** worker logs may contain org EINs and file paths. Logs are stored in private S3, not exposed publicly. Dashboard log link is behind auth.

## 5. Failure & Error Scenarios

- **Heartbeat DB write fails** → log warning, skip this heartbeat, retry next cycle. Never crash the worker over a heartbeat failure.
- **Heartbeat thread dies** → main processing continues (the heartbeat is observability, not control flow). The worker will eventually be detected as stale and relaunched — acceptable degradation.
- **Log shipping fails** → log warning, continue with termination. The exit_reason column is the guaranteed minimum diagnostic.
- **All slots abandoned** → run ends with `status=failed`, queue preserved, resumable. Same graceful failure as current behavior.

## 6. Acceptance Criteria

1. Worker heartbeat updates every 60s, even during long doc processing
2. Stale detection uses heartbeat age, not completion time — a 600s doc does NOT trigger stale
3. Relaunch budget resets on progress — a run that processes 8,000 docs across 15 hours never runs out of relaunches due to occasional spot churn
4. A slot that crashes 3 times consecutively without progress is abandoned; other slots continue
5. Exit reason recorded for every run; dashboard displays human-readable label
6. Worker log available in S3 after run completion
7. No regression: existing parse runs (run 31 pattern) complete successfully with the new orchestrator

**Required test cases:**
- Heartbeat thread fires during simulated long doc processing
- Stale detection: recent heartbeat → not stale; old heartbeat → stale; no heartbeat row → fallback to completion-based
- Relaunch counter resets on progress; increments on death; abandoned at 3
- Death classification: spot reclaim vs crash vs hang
- Exit reason set correctly for each exit path
- Log shipping SSM failure handled gracefully
- run_tag validation rejects injection attempts

## 7. Traps to Avoid

- **Do NOT remove B1 stale detection** — Docling hangs (no exception) still need detection. Replace the signal, not the mechanism.
- **Do NOT use a global relaunch counter** — per-slot counters prevent one bad slot from killing the fleet.
- **SSM command timing** — log pull is async; wait 5s before terminating instance.
- **Heartbeat thread must be daemon** — or it prevents clean process exit.
- **Backward compat** — NULL exit_reason on legacy runs displays as "—", not an error.

## 8. Open Questions (plan phase)

- **Heartbeat interval** — 60s proposed. Could be 30s for faster detection at the cost of more DB writes. 
- **HEARTBEAT_STALE_MINUTES** — 5 min proposed. Must be > heartbeat interval × 2 (at least 2 missed heartbeats before declaring stale).
- **Spot reclaim detection** — instance metadata endpoint vs CloudWatch events vs instance state polling. Instance state polling is simplest but has a delay.
- **Worker tarball deployment** — the heartbeat thread is a worker-side change. Requires a new tarball build + deploy. Coordinate with any other pending worker changes.

# Spec 0043: Pipeline Control Panel

**Status**: Draft
**Author**: Architect
**Date**: 2026-05-14
**Dependencies**: Spec 0040 (V3 Job Queue)

## Problem

Pipeline operations currently require SSH access and manual commands:

- **Restarting services**: `ssh cloud1 sudo systemctl restart lavandula-orchestrator`
- **Cleaning orphan locks**: Multi-step SQL via Python one-liners
- **Bulk job cancellation**: One-by-one from the job detail page, or manual ORM updates
- **Pausing the queue**: No mechanism exists — the orchestrator runs continuously
- **Retrying failed jobs**: One-at-a-time from the job detail page
- **Checking host health**: SSH to each host and run `htop`/`systemctl status`

This friction is tolerable for a developer operator but blocks the system from being handed off or operated remotely. As the pipeline scales to Layer 2 (full document extraction, vocabulary analysis), the operational surface grows and manual SSH becomes a bottleneck.

## Goals

1. **Single-pane operations**: All routine operational tasks accessible from the dashboard without SSH.

2. **Job queue control**: Pause/resume the scheduler, bulk cancel jobs by filter (state, phase, status), bulk retry failed jobs, and clear the queue entirely.

3. **Service lifecycle management**: Start, stop, and restart pipeline services (orchestrator, dashboard) on any registered host from the dashboard.

4. **Orphan cleanup**: One-click detection and cleanup of orphaned advisory locks and stale running jobs (jobs marked "running" but whose process is dead).

5. **Host visibility**: Real-time view of host health (CPU, memory, disk), running processes, and service status.

6. **Audit trail**: All control panel actions logged with operator identity, timestamp, and parameters.

7. **Architecture-ready**: The control panel is designed to run on a dedicated dashboard host, separate from processing workers. No assumption that the dashboard shares a host with any worker.

## Non-Goals

- **Multi-user RBAC**: This is a single-operator system. No role-based access beyond `LoginRequiredMixin`.
- **Automated remediation**: The panel surfaces problems and provides one-click fixes. It does not auto-restart failed services or auto-retry failed jobs.
- **Monitoring/alerting integration**: No PagerDuty, Grafana, or CloudWatch integration. The existing watchdog (Spec 0036) handles alerting.
- **Infrastructure provisioning**: No creating/destroying EC2 instances, RDS changes, or S3 management.
- **Scheduler algorithm changes**: The control panel pauses/resumes the existing scheduler. It does not change scheduling logic.

## Technical Implementation

### Architecture: Host Command Agent

The dashboard cannot directly execute commands on remote hosts (no SSH keys, no shared process space). Instead, each host runs a **command agent** — a lightweight polling loop inside the existing orchestrator process that checks for pending commands.

```
Dashboard (cloud2)                    Worker (cloud1, cloud3, ...)
┌──────────────┐                     ┌──────────────────────┐
│ Control Panel │──INSERT──▶ DB ◀──POLL──│ Orchestrator         │
│ (Django view) │                     │   └─ Command Agent    │
│               │◀─────────READ──────│      (polls every 5s) │
└──────────────┘                     └──────────────────────┘
```

**Why polling, not SSH**: 
- No SSH key management or distribution
- Works through NAT/Tailscale without port forwarding
- Same pattern as the existing orchestrator (polls DB for eligible jobs)
- Command results stored in DB, visible from any host

### New Model: `HostCommand`

```python
class HostCommand(models.Model):
    host = models.CharField(max_length=100)  # target hostname
    command = models.CharField(max_length=50, choices=COMMAND_CHOICES)
    args_json = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, default="pending",
        choices=[("pending", "Pending"), ("running", "Running"),
                 ("completed", "Completed"), ("failed", "Failed")])
    result_text = models.TextField(blank=True, default="")
    requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
```

**Command types**:

| Command | Args | What it does |
|---------|------|-------------|
| `restart-orchestrator` | — | `systemctl restart lavandula-orchestrator` |
| `restart-dashboard` | — | `systemctl restart lavandula-dashboard` (if exists on host) |
| `report-status` | — | Collects service status, uptime, disk, running PIDs |
| `cleanup-locks` | `{"dry_run": bool}` | Identifies and releases orphaned advisory locks |
| `kill-process` | `{"pid": int, "signal": "TERM"|"KILL"}` | Send signal to a specific PID |

**Security constraint**: The command agent runs as the `ubuntu` user (same as systemd services). Service restart requires `sudo systemctl` — the `ubuntu` user must have passwordless sudo for these specific commands. This is already the case on both hosts.

**Command lifecycle rules**:
- **Claiming**: The agent claims a command by atomically updating `status='running', started_at=now()` with `WHERE status='pending' AND host=<my_hostname>`. The `select_for_update()` pattern prevents duplicate pickup after restart.
- **Timeout**: Commands stuck in `pending` for > 60 seconds or `running` for > 120 seconds are considered timed out. The dashboard shows "timed out" status. A timed-out command is never retried automatically — the operator must re-issue it.
- **Idempotency**: `restart-orchestrator` and `restart-dashboard` are inherently idempotent (restarting an already-running service is fine). `cleanup-locks` checks current state before acting. `kill-process` may fail silently if PID is gone (ProcessLookupError is expected, not an error).
- **Host identity**: `HostCommand.host` must match `Worker.hostname` exactly. The agent filters by `socket.gethostname()` which matches the hostname registered via `setup_worker`. The dashboard's host dropdown is populated from `Worker.objects.filter(is_active=True)` — no freeform input.

**Restart execution contract**: All restart commands are executed via `subprocess.Popen` (fire-and-forget, not blocking). The agent marks the command as `running`, spawns the subprocess, and returns control to the orchestrator loop immediately. Completion detection:
- For `restart-orchestrator`: The restarted orchestrator process picks up the still-`running` command on its first tick and marks it `completed` after confirming the service PID has changed.
- For `restart-dashboard`: The agent polls `systemctl is-active lavandula-dashboard` for up to 10 seconds after issuing the restart, then marks completed/failed.
- If the process dies before marking completion, the command stays `running` and times out after 120 seconds.

### Feature 1: Queue Pause/Resume

**Mechanism**: A `PipelineConfig` singleton row stores global operational state.

```python
class PipelineConfig(models.Model):
    queue_paused = models.BooleanField(default=False)
    paused_at = models.DateTimeField(null=True, blank=True)
    paused_by = models.CharField(max_length=100, blank=True, default="")
    
    class Meta:
        # Singleton: only one row allowed
        constraints = [
            models.CheckConstraint(check=models.Q(pk=1), name="singleton_config")
        ]
```

**Scheduler integration**: The orchestrator's main loop checks `PipelineConfig.queue_paused` at the top of each tick. If paused:
- No new jobs are scheduled or started
- Running jobs continue to completion (pause does not kill running work)
- The dashboard shows a prominent "QUEUE PAUSED" banner

**UI**: Toggle button on the control panel. Shows who paused and when.

### Feature 2: Bulk Job Operations

**Bulk Cancel**: Cancel all jobs matching a filter. Filter dimensions:
- Phase (e.g., all `extract-context` jobs)
- State (e.g., all jobs for TX)
- Status: defaults to **pending only**. A separate "Include running" checkbox enables cancelling running jobs (shows warning: "Running jobs will be sent SIGTERM. Partially completed work may need re-processing.")
- Host

Running job cancellation uses the existing `cancel_job()` which sends SIGTERM→SIGKILL locally. For jobs on remote hosts, cancellation sets `status='cancelled'` in DB — the orchestrator on that host detects the status change and kills the process on its next tick.

Executes `cancel_job()` for each matching job (which cascades to dependents). Shows confirmation with count before executing.

**Bulk Retry**: Retry all failed jobs matching a filter. Same filter dimensions. Creates new pending jobs via `retry_job()`.

**Clear Queue**: Cancel all pending jobs only — running jobs are never touched by Clear Queue. Confirmation required ("This will cancel N pending jobs"). The button label is "Clear All Pending" (not "Clear Queue") to avoid ambiguity.

**UI**: Filter bar with dropdowns + action buttons. Results shown as a summary ("Cancelled 12 jobs, 3 dependents cascade-cancelled").

### Feature 3: Orphan Detection & Cleanup

**Stale running jobs**: Jobs with `status='running'` where:
- `last_heartbeat` is older than 10 minutes, OR
- The host's Worker has `status='stale'` or `status='offline'`

**"Mark Failed" side effects**: When marking a stale job as failed:
1. Set `status='failed'`, `finished_at=now()`, `error_message='Marked failed by operator (stale)'`
2. Preserve existing `last_heartbeat` and `host` fields (for diagnostics)
3. Cascade-cancel all pending/scheduled dependent jobs (same as `cancel_job()` behavior)
4. Do NOT auto-retry — the operator decides whether to retry after investigating
5. **Race guard**: If the process is actually still alive (false positive), the status flip to `failed` is harmless — the process will finish its work but the Job record won't be updated to `completed` (the process checks status before final update). The operator can see this in the job's event log.

**Orphaned advisory locks**: Advisory locks held by DB connections where:
- The connection is idle (no active query)
- No matching running Job exists for that connection's PID

**UI**: "Health Check" panel showing:
- Count of stale running jobs (with "Mark Failed" button) — expandable to show job ID, state, phase, last heartbeat
- Count of orphaned locks (with "Release Locks" button) — expandable to show DB PID, client IP, lock ID, connection age
- Each item shown with details before action

**Implementation**: The lock cleanup uses `pg_terminate_backend()` on orphaned connections (same approach as manual cleanup, but with safety checks to exclude connections that belong to active running jobs).

**Authoritative ownership linkage**: There is no direct mapping between a Job's OS-level PID and its PostgreSQL backend PID — they are different processes (Python process vs DB connection). The safety check instead uses the **client IP address** from `pg_stat_activity` combined with the Job's `host` field (resolved to IP via the `Worker.ip_address` field):

**Safety check before terminating a connection**:
1. Get all `(pid, client_addr)` from `pg_locks JOIN pg_stat_activity WHERE locktype = 'advisory'`
2. Skip any connection with `state != 'idle'` (actively executing a query)
3. For each idle lock-holding connection, check: does any Job with `status='running'` exist on a host whose `Worker.ip_address` matches this connection's `client_addr`?
4. If YES — skip (this connection likely belongs to an active job process on that host)
5. If NO — safe to terminate (orphaned connection from a dead process)

This is conservative: it protects ALL connections from a host that has ANY running job, even if the specific connection is unrelated. This prevents the WI/VA incident. The trade-off is that orphaned locks on hosts with running jobs won't be cleaned up until all jobs on that host finish.

**Lock accumulation risk**: In theory, orphaned locks could accumulate if a host always has at least one running job. In practice, this is bounded: advisory locks are session-level and released when the DB connection closes. PostgreSQL's `idle_in_transaction_session_timeout` or connection pooler timeouts naturally clean up truly abandoned connections. The health check panel shows the current lock count so the operator can monitor accumulation. If it becomes a problem, a future enhancement could embed the Job ID in the advisory lock key, enabling per-lock ownership verification.

### Feature 4: Host Status Dashboard

**Data sources**:
- `Worker` model: hostname, status, last_heartbeat, cpu_pct, mem_pct, has_gpu
- `HostCommand` results: service status, disk usage, uptime
- `Job` counts: running/pending/failed per host

**UI**: Card per host showing:
- Hostname and display name
- Status badge (online/stale/offline)
- Last heartbeat (relative time)
- CPU / Memory bars
- Running job count
- Service status (orchestrator: running/stopped, dashboard: running/stopped/N/A)
- Action buttons: Restart Orchestrator, Restart Dashboard, Report Status

**Auto-refresh**: HTMX polling every 10 seconds.

**Status freshness**: CPU/memory values come from the Worker model's heartbeat (updated by the orchestrator every ~30 seconds). Service status comes from the latest `report-status` HostCommand result. Display rules:
- Worker heartbeat < 1 min old: show values as-is
- Worker heartbeat 1-5 min old: show values with "(stale)" suffix in gray
- Worker heartbeat > 5 min old or no heartbeat: show "—" for CPU/memory, status badge = "offline"
- No `report-status` result: service status shows "unknown" in gray (not "stopped")

### Feature 5: Service Management

Restart buttons trigger `HostCommand` creation. The control panel polls for command completion and shows the result.

**Flow**:
1. Operator clicks "Restart Orchestrator" on cloud1's card
2. Dashboard creates `HostCommand(host="cloud1", command="restart-orchestrator", status="pending")`
3. Cloud1's orchestrator agent picks up the command on next poll
4. Agent executes `sudo systemctl restart lavandula-orchestrator`
5. Agent updates `HostCommand.status = "completed"` with result text
6. Dashboard polls and shows "Orchestrator restarted successfully"

**Edge case — restarting the orchestrator restarts the agent**: The agent writes `status="running"` before executing the restart. If the restart succeeds, the new orchestrator process picks up the command (still "running") and marks it "completed" after verifying the service is up. If restart fails, the old process is gone — the command stays "running" and the dashboard shows a timeout warning after 30 seconds.

**Edge case — restarting the dashboard on the current host**: The dashboard writes the command, then the response never arrives (because gunicorn restarts). The operator sees the page reload. On reload, the control panel reads the command status to confirm success.

### Feature 6: Scheduler Config View

**Read-only view** of `scheduler_config.yaml` contents:
- Max concurrent heavy jobs
- CPU/memory ceilings
- Cool-down after failure
- Per-phase resource weights

**Not editable from the UI** for v1. The YAML file is hot-reloaded, so editing via SSH takes effect immediately. Future versions may add UI editing.

### URL Structure

All control panel URLs under `/dashboard/control/`:

| URL | View | Method |
|-----|------|--------|
| `control/` | ControlPanelView | GET |
| `control/queue/pause/` | QueuePauseView | POST |
| `control/queue/resume/` | QueueResumeView | POST |
| `control/jobs/bulk-cancel/` | BulkCancelView | POST |
| `control/jobs/bulk-retry/` | BulkRetryView | POST |
| `control/jobs/clear-queue/` | ClearQueueView | POST |
| `control/health/` | HealthCheckPartial | GET (HTMX) |
| `control/health/fix-stale/` | FixStaleJobsView | POST |
| `control/health/release-locks/` | ReleaseLocks View | POST |
| `control/hosts/` | HostStatusPartial | GET (HTMX) |
| `control/hosts/<hostname>/command/` | HostCommandView | POST |
| `control/hosts/<hostname>/status/` | HostCommandStatusPartial | GET (HTMX) |

### Navigation

Add "Control Panel" link to the dashboard navigation bar, after the existing pipeline page links. Use a gear icon or similar indicator.

### Page Layout

Single page with tabbed or stacked sections:

```
┌─────────────────────────────────────────────────┐
│ Control Panel                          [PAUSED] │
├─────────────────────────────────────────────────┤
│ Queue Control                                   │
│ [Pause Queue] [Resume Queue]                    │
│ Bulk: [Phase ▼] [State ▼] [Status ▼] [Host ▼]  │
│       [Cancel Selected] [Retry Failed]          │
│       [Clear All Pending]                       │
├─────────────────────────────────────────────────┤
│ Health Check                    auto-refresh 10s│
│ Stale Jobs: 2  [Mark Failed]                    │
│ Orphan Locks: 1  [Release]                      │
├─────────────────────────────────────────────────┤
│ Hosts                           auto-refresh 10s│
│ ┌─cloud2──────────┐ ┌─cloud1──────────┐        │
│ │ ● Online        │ │ ● Online        │        │
│ │ CPU: 23%  M: 45%│ │ CPU: 67%  M: 78%│        │
│ │ Jobs: 2 running │ │ Jobs: 1 running │        │
│ │ Orch: running   │ │ Orch: running   │        │
│ │ Dash: running   │ │ Dash: N/A       │        │
│ │ [Restart Orch]  │ │ [Restart Orch]  │        │
│ │ [Restart Dash]  │ │ [Report Status] │        │
│ └─────────────────┘ └─────────────────┘        │
├─────────────────────────────────────────────────┤
│ Scheduler Config (read-only)                    │
│ max_concurrent_heavy: 2                         │
│ memory_ceiling_pct: 75                          │
│ cpu_ceiling_pct: 90                             │
│ ...                                             │
└─────────────────────────────────────────────────┘
```

## Acceptance Criteria

1. Queue can be paused and resumed from the control panel. While paused, no new jobs start but running jobs complete normally.
2. Bulk cancel filters by phase, state, status, and host. Confirmation shows count before executing. Cascade cancellation applies.
3. Bulk retry filters by phase, state, and host (failed jobs only). Creates new pending jobs with dependency rewiring.
4. "Clear Queue" cancels all pending jobs with confirmation.
5. Health check detects stale running jobs (heartbeat > 10 min or host offline) and offers one-click "Mark Failed".
6. Health check detects orphaned advisory locks and offers one-click release with safety checks (never terminates connections belonging to active jobs).
7. Host cards show real-time status (CPU, memory, service state, job counts) with 10-second auto-refresh.
8. Service restart buttons create HostCommands that are executed by the target host's agent within one poll cycle (≤ 5 seconds).
9. All control panel actions are logged to `PipelineAuditLog` with operator identity, timestamp, action type, and parameters.
10. The control panel works from a dedicated dashboard host — no assumption that the dashboard shares a host with any worker.
11. A "QUEUE PAUSED" banner is visible on ALL dashboard pages (not just control panel) when the queue is paused.
12. The command agent polling loop is integrated into the existing orchestrator process (no new systemd service).

## Testing Strategy

1. **Queue pause/resume**: Pause queue → verify orchestrator skips scheduling → create pending job → verify it stays pending → resume → verify job becomes eligible
2. **Bulk cancel**: Create 5 pending jobs (3 extract TX, 2 reclassify CA) → bulk cancel phase=extract → verify 3 cancelled, 2 remain
3. **Bulk cancel cascade**: Create chain A→B→C → cancel A → verify B and C also cancelled
4. **Bulk retry**: Create 3 failed jobs → bulk retry → verify 3 new pending jobs with retry_of set
5. **Stale job detection**: Create job with status=running and last_heartbeat 15 min ago → health check shows it as stale
6. **Orphan lock detection**: Hold advisory lock on idle connection with no matching running job → health check detects it
7. **Host command lifecycle**: Create HostCommand → agent picks it up → status transitions pending→running→completed
8. **Audit logging**: Perform bulk cancel → verify PipelineAuditLog entry with action="bulk_cancel", parameters include filter criteria
9. **Pause banner**: Pause queue → load classifier v3 page → verify "QUEUE PAUSED" banner appears
10. **HTMX partials**: GET health check and host status endpoints → verify HTML fragments, not full pages
11. **Command claiming**: Create two HostCommands for same host → agent claims one at a time → second remains pending until first completes
12. **Restart recovery**: Create restart-orchestrator command → simulate process death → new process picks up running command and marks completed
13. **Offline host command**: Create command for offline host → dashboard shows timeout after 120 seconds, command stays pending
14. **Pause persists across restart**: Pause queue → restart orchestrator → verify queue still paused
15. **CSRF enforcement**: POST to any control panel endpoint without CSRF token → verify 403
16. **Lock cleanup false positive prevention**: Hold advisory lock from host with running job → cleanup should NOT terminate that connection

## Traps to Avoid

1. **Don't terminate active DB connections**: The orphan lock cleanup must cross-reference advisory lock PIDs against running job processes. The incident today (killing WI/VA extract jobs) happened because connections were terminated without checking whether they belonged to active jobs.

2. **Don't block the orchestrator loop**: The command agent polls inside the existing orchestrator tick. Commands must execute quickly (systemctl restart returns in <2s) or be backgrounded. A slow command must not delay job scheduling.

3. **Don't assume dashboard and orchestrator share a host**: Service restart on cloud1 requires the command to be executed BY cloud1's agent, not by the dashboard process on cloud2.

4. **Don't auto-resume on restart**: If the queue is paused and the orchestrator restarts, it must check `PipelineConfig.queue_paused` on startup. The pause state is in the database, not in memory.

5. **Don't cascade cancel running jobs in bulk**: Bulk cancel of pending jobs is safe. Bulk cancel of running jobs sends SIGTERM which can leave partial state. The UI should default to "pending only" and require explicit opt-in for cancelling running jobs.

6. **Don't skip confirmation for destructive actions**: Bulk cancel, clear queue, and release locks all require a confirmation step showing exactly what will be affected.

7. **Don't poll HostCommand results too aggressively**: The dashboard polls for command completion every 2 seconds for 30 seconds, then shows a timeout. Don't poll indefinitely.

8. **Don't restart the orchestrator that's executing the restart command synchronously**: The agent must background the restart and exit its current tick. The new orchestrator process inherits the pending command check.

### Failure-Path UX

Every destructive action has a defined failure display:

| Scenario | UI Behavior |
|----------|-------------|
| HostCommand times out (>120s running) | Card shows "Command timed out — host may be unreachable" in red |
| Host is offline (Worker.status='offline') | Restart buttons disabled with tooltip "Host offline" |
| `sudo` fails (permission denied) | HostCommand.result_text shows error, card shows "Failed: permission denied" |
| `pg_terminate_backend()` returns false | Lock shown as "Release failed — connection may have already closed" |
| Partial bulk cancel (some jobs changed status between confirmation and execution) | Summary shows "Cancelled 8 of 10 jobs (2 already completed)" |
| Bulk retry on job that already has a pending retry | Skip with note in summary: "Skipped 1 job (retry already exists)" |

## Security Considerations

- All control panel endpoints use existing `LoginRequiredMixin` (single-operator system)
- All POST endpoints include Django's built-in CSRF protection (`{% csrf_token %}` in forms, `CsrfViewMiddleware` in settings)
- `HostCommand` is the only vector for remote code execution — the command type is an enum, not freeform. The agent only executes whitelisted commands. `args_json` is validated per command type: `cleanup-locks` accepts only `{"dry_run": bool}`, `kill-process` accepts only `{"pid": int, "signal": "TERM"|"KILL"}`, all others accept no args
- `kill-process` command is restricted to PIDs owned by the `ubuntu` user (the agent runs as `ubuntu`)
- All actions logged to `PipelineAuditLog` with operator identity, source IP, action type, parameters, and outcome (success/failure/partial). Failed and denied attempts are also logged. Agent-side execution results (command output, errors) are logged via the `HostCommand.result_text` field.
- `HostCommand.result_text` is sanitized before storage: environment variables, file paths containing usernames, and any string matching AWS credential patterns are redacted. Output is truncated to 4KB max.
- No secrets or credentials exposed in the UI
- **Host identity**: The agent polls for commands using `socket.gethostname()`, which matches the hostname registered via `setup_worker`. This is a private VPC network — all hosts connect to RDS via private IP, no public internet exposure. Hostname spoofing would require VPC-level network access which implies the attacker already has IAM credentials (game over regardless). Accepted risk for a single-operator system. If the system grows to multi-operator, mTLS or per-agent tokens would be warranted.
- **Sudoers hardening**: The agent executes `sudo systemctl` only with hardcoded, exact service names. The sudoers configuration must use exact command paths with no wildcards:
  ```
  ubuntu ALL=(root) NOPASSWD: /usr/bin/systemctl restart lavandula-orchestrator
  ubuntu ALL=(root) NOPASSWD: /usr/bin/systemctl restart lavandula-dashboard
  ubuntu ALL=(root) NOPASSWD: /usr/bin/systemctl status lavandula-orchestrator
  ubuntu ALL=(root) NOPASSWD: /usr/bin/systemctl status lavandula-dashboard
  ```
  The agent constructs the command as a fixed list (`["sudo", "systemctl", "restart", service_name]`) where `service_name` is selected from a hardcoded enum — never from user input or `args_json`.
- **Django session security**: The deployment must configure `SESSION_COOKIE_SECURE=True`, `SESSION_COOKIE_HTTPONLY=True`, `CSRF_COOKIE_SECURE=True`. These are infrastructure settings, not application code changes.

## Consultation Log

### Red Team Security Review (2026-05-14)

**Gemini**: REQUEST_CHANGES. 1 CRITICAL, 3 HIGH, 2 MEDIUM, 1 LOW.

1. **CRITICAL — Host identity spoofing via hostname**: Agent polls by `socket.gethostname()`, no mutual auth.
   - **Disposition**: Accepted risk. Private VPC, no public exposure. Hostname spoofing requires IAM-level compromise. Documented in Security Considerations. Would revisit for multi-operator.

2. **HIGH — Sudoers wildcard risk**: Broad sudo config could allow arbitrary systemctl commands.
   - **Disposition**: Fixed. Added explicit sudoers rules — exact command paths, no wildcards. Agent uses hardcoded enum for service names, never user input.

3. **HIGH — Lock accumulation from conservative cleanup**: Hosts with running jobs never get locks cleaned.
   - **Disposition**: Acknowledged. Documented the bounded risk (session-level locks auto-release on connection close). Health check panel shows lock count for monitoring. Future enhancement: embed Job ID in lock key.

4. **HIGH — result_text could expose secrets**: Command output stored in DB and shown in UI.
   - **Disposition**: Fixed. Added sanitization requirement (redact env vars, AWS credential patterns, truncate to 4KB).

5. **MEDIUM — MFA for single operator account**: Single credential = full control.
   - **Disposition**: Out of scope for v1. Valid for future hardening if system grows.

6. **MEDIUM — Session cookie security settings**: Standard Django hardening not mentioned.
   - **Disposition**: Fixed. Added explicit requirement for SESSION_COOKIE_SECURE, HTTPONLY, CSRF_COOKIE_SECURE.

7. **LOW — Standard Django session security**: Same as MEDIUM #6.
   - **Disposition**: Addressed above.

### First Consultation (2026-05-14)

**Gemini**: APPROVE (HIGH confidence). No key issues.

**Codex**: REQUEST_CHANGES (HIGH confidence). 12 findings:
1. HostCommand lifecycle incomplete → **Fixed**: Added claiming semantics, timeout, idempotency rules.
2. Restart flow inconsistent → **Fixed**: Added explicit restart execution contract (fire-and-forget via Popen).
3. "Clear queue" ambiguous → **Fixed**: Renamed to "Clear All Pending", explicitly excludes running jobs.
4. Bulk cancel for running jobs vague → **Fixed**: Defaults to pending-only, explicit opt-in for running with warning.
5. Stale job "Mark Failed" underspecified → **Fixed**: Added 5-point side-effect list including race guard.
6. Orphan lock safety check not robust → **Fixed**: Changed to client_addr-based linkage with conservative skip-if-any-running-job rule.
7. Audit requirement incomplete → **Fixed**: Added agent-side logging, failed attempt logging, outcome field.
8. Host identity ambiguous → **Fixed**: Documented hostname source (setup_worker → socket.gethostname), dropdown from Worker model.
9. Security too light → **Fixed**: Added CSRF, args_json validation, sudoers hardening, session cookies.
10. Acceptance criteria not testable for host health → **Fixed**: Added status freshness rules (stale/offline thresholds, "unknown" vs "stopped").
11. Failure-path UX missing → **Fixed**: Added failure display table for 6 scenarios.
12. Testing strategy missing race/multi-host → **Fixed**: Added 6 additional test cases (claiming, restart recovery, offline timeout, pause persistence, CSRF, false positive prevention).

# Spec 0033: Multi-Host Job Distribution & Remote Workers

## Problem

The pipeline has outgrown single-host execution. National ingest across 50 states means running resolvers, crawlers, classifiers, and phone enrichment concurrently — but the bottleneck is no longer API rate limits, it's operational throughput: site blocks, crawl failures, retry backlogs, and the inability to parallelize work across hosts.

The orchestrator already has multi-host plumbing: `Job.host` field, hostname-filtered polling, per-host orphan recovery. But there's no way to:
- Register and monitor remote hosts from the dashboard
- Target a job at a specific remote host from the UI
- Detect when a remote orchestrator is dead
- Automatically distribute work across available hosts

Today, running on multiple hosts means manually SSH-ing into each machine, cloning the repo, configuring credentials, starting the orchestrator, and manually queuing jobs with the right hostname. The dashboard only shows jobs for `_get_hostname()` (the dashboard host), so remote work is invisible.

## Goals

1. **Worker registry** — dashboard tracks known hosts with their capabilities (GPU, memory tier), online status, and last heartbeat
2. **Host targeting** — queue jobs from the dashboard UI to any registered host, not just localhost
3. **Health monitoring** — detect stale orchestrators (heartbeat timeout), surface health on dashboard, auto-fail orphaned remote jobs
4. **Visibility** — dashboard shows all jobs across all hosts, with host column in job tables
5. **Host setup automation** — a single CLI command (`lavandula-worker setup`) that configures a fresh EC2 instance as a pipeline worker: installs dependencies, configures RDS/S3 access, registers with the dashboard, and starts the orchestrator

## Non-Goals

- Auto-scaling / elastic host provisioning (use AWS console to launch instances)
- Job migration between hosts (if a host dies, retry the job manually)
- Cross-host log streaming (use the job detail page's log viewer, which reads from the log file — remote logs would need a separate solution)
- Load-based job routing (operator decides which host gets which work)
- Changing the subprocess execution model (orchestrator still launches local subprocesses)

## Design

### 1. Worker Model

New Django model in `pipeline/models.py`:

```python
class Worker(models.Model):
    hostname = models.CharField(max_length=100, unique=True)
    display_name = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=[
        ("online", "Online"),
        ("offline", "Offline"),
        ("stale", "Stale"),
    ], default="offline")
    capabilities = models.JSONField(default=dict, blank=True)
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    registered_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        db_table = "workers"
```

**Capabilities JSON** — free-form but conventionally:
```json
{
    "gpu": true,
    "memory_gb": 16,
    "phases": ["resolve", "classify"],
    "notes": "g6 instance for GPU resolver"
}
```

The `phases` list is advisory — it tells the operator which phases this host is suited for. The orchestrator does not enforce it; any job assigned to a host will run regardless of `capabilities.phases`.

### 2. Orchestrator Heartbeat

Modify `run_orchestrator` to update the Worker row every poll cycle (10s):

```python
# In the main loop, after polling/starting jobs:
Worker.objects.update_or_create(
    hostname=self.hostname,
    defaults={
        "status": "online",
        "last_heartbeat": timezone.now(),
        "ip_address": self._get_ip(),
    },
)
```

On shutdown (SIGTERM/SIGINT handler), set `status="offline"`.

On startup, auto-register if no Worker row exists. If one exists, update to `online`.

### 3. Stale Worker Detection

A management command `check_worker_health` (run via cron every 60s on the dashboard host, or called from the orchestrator's poll loop):

- Workers with `status="online"` and `last_heartbeat` older than `HEARTBEAT_STALE_REMOTE` (300s, already defined) → mark `status="stale"`
- Stale workers' running jobs → mark as `failed` with `error_message="worker stale: heartbeat timeout"`
- Workers with `status="stale"` that resume heartbeating → auto-recover to `online`

The dashboard host's orchestrator runs this check in its own poll loop (no separate cron needed). Remote orchestrators do NOT run this check — only the dashboard host acts as the health monitor.

### 4. Dashboard UI Changes

#### 4a. Worker Status Panel (Main Dashboard)

Add a "Workers" section above the state progress table:

```
Workers
┌─────────────────┬────────┬──────────────┬───────────┬──────────┐
│ Host             │ Status │ Last Seen    │ Active    │ Phases   │
├─────────────────┼────────┼──────────────┼───────────┼──────────┤
│ ip-172-31-35-76 │ 🟢     │ 5s ago       │ 2 jobs    │ all      │
│ ip-172-31-42-10 │ 🟢     │ 12s ago      │ 1 job     │ resolve  │
│ ip-172-31-55-03 │ 🔴     │ 8m ago       │ 0 jobs    │ crawl    │
└─────────────────┴────────┴──────────────┴───────────┴──────────┘
```

Status indicators: 🟢 online, 🟡 stale (heartbeat > 120s but < 300s), 🔴 offline.

Active jobs count = `Job.objects.filter(host=hostname, status__in=["running", "pending"]).count()`.

#### 4b. Host Selector on Job Queue Forms

Every job queue form (seeder, resolver, crawler, classifier, phone enrich, 990 index, 990 parse) gets a **Host** dropdown:

```html
<select name="host">
    <option value="ip-172-31-35-76" selected>ip-172-31-35-76 (this host)</option>
    <option value="ip-172-31-42-10">ip-172-31-42-10 (online)</option>
    <option value="ip-172-31-55-03" disabled>ip-172-31-55-03 (offline)</option>
</select>
```

- Default: current host (preserves existing behavior)
- Online workers shown with "(online)" suffix
- Offline/stale workers shown but disabled — can't target a dead host
- If only one host (localhost), hide the dropdown entirely to avoid clutter

Update `_get_hostname()` usages in views to read from `request.POST.get("host", _get_hostname())` instead.

#### 4c. Host Column in Job Tables

The shared `_recent_jobs_table.html` partial and the jobs list page gain a **Host** column showing the hostname (truncated if long). On the main dashboard this helps operators see which work is happening where.

#### 4d. Worker Management Page

New page at `/dashboard/workers/` (linked from sidebar):

- List all registered workers with full details
- Register new worker form (hostname, display_name, capabilities)
- Edit/delete workers
- Manual "mark offline" / "mark online" buttons for overrides

### 5. Host Setup CLI

A new management command `setup_worker` that automates remote host configuration:

```bash
# Run ON the remote host after cloning the repo:
python3 lavandula/dashboard/manage.py setup_worker \
    --display-name "Resolver GPU" \
    --phases resolve,classify \
    --gpu
```

This command:
1. Verifies RDS connectivity (tries `connections["default"]` and `connections["pipeline"]`)
2. Verifies S3 access (tries `boto3.client("s3").head_bucket(Bucket="lavandula-nonprofit-collaterals")`)
3. Registers the worker in the `workers` table (hostname from `socket.gethostname()`)
4. Creates a systemd service file for the orchestrator (`lavandula-orchestrator.service`)
5. Prints next steps: `sudo systemctl enable --now lavandula-orchestrator`

The systemd service:
```ini
[Unit]
Description=Lavandula Pipeline Orchestrator
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/research
ExecStart=/usr/bin/python3 lavandula/dashboard/manage.py run_orchestrator
Restart=on-failure
RestartSec=10
Environment=PYTHONPATH=/home/ubuntu/research

[Install]
WantedBy=multi-user.target
```

### 6. Phase Conflict Relaxation

Currently `check_phase_conflict()` blocks globally — only one job per phase across ALL hosts. For national ingest, we need to run resolvers on multiple hosts simultaneously (different states).

Change the conflict check to be **per-state** for state-scoped phases:
- `resolve`, `classify`, `enrich-phone`: conflict check scoped to `(phase, state_code)` — two hosts can resolve different states concurrently
- `crawl`: conflict check scoped to `(phase)` — only one crawl job at a time (crawler is already concurrent internally)
- `seed`: conflict check scoped to `(phase, state_code)`
- `990-index`, `990-parse`: conflict check unchanged (990-family advisory lock)

```python
def check_phase_conflict(phase: str, state_code: str | None = None) -> bool:
    qs = Job.objects.filter(phase=phase, status="running")
    if state_code and phase in ("resolve", "classify", "enrich-phone", "seed"):
        qs = qs.filter(state_code=state_code)
    if qs.exists():
        return True
    if PipelineProcess.objects.filter(name=phase, status="running").exists():
        return True
    return False
```

Update `_start_eligible_jobs` to pass `state_code` to the conflict check.

## Technical Implementation

### Database Changes

One new table (`workers`), one migration:

```sql
CREATE TABLE workers (
    id SERIAL PRIMARY KEY,
    hostname VARCHAR(100) UNIQUE NOT NULL,
    display_name VARCHAR(100) DEFAULT '',
    status VARCHAR(20) DEFAULT 'offline',
    capabilities JSONB DEFAULT '{}',
    last_heartbeat TIMESTAMP WITH TIME ZONE,
    registered_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    ip_address INET
);
```

No changes to the `jobs` table — `host` field already exists.

### Files Changed

| File | Change |
|------|--------|
| `models.py` | Add `Worker` model |
| `orchestrator.py` | Update `check_phase_conflict()` signature, add `state_code` param |
| `run_orchestrator.py` | Add heartbeat writes, stale worker detection, worker auto-registration |
| `views.py` | Add `WorkerListView`, update all job create views to accept `host` from form, add worker context to dashboard |
| `forms.py` (or inline in views) | Add host field to job queue forms |
| `urls.py` | Add `/workers/` route |
| `dashboard_stats.html` | Add workers panel above state progress table |
| `_recent_jobs_table.html` | Add host column |
| `workers.html` (new) | Worker management page |
| `base.html` | Add Workers link to sidebar nav |
| All job queue templates | Add host dropdown to forms |
| `setup_worker.py` (new mgmt cmd) | Host setup automation |
| `check_worker_health.py` (new mgmt cmd) | Standalone health check (optional, main orchestrator also runs it) |

### Security

- **Worker registration**: Only authenticated Django users can register/edit workers via the dashboard. The `setup_worker` CLI command writes directly to RDS, so it requires DB credentials — same trust boundary as the orchestrator itself.
- **No new network exposure**: Remote orchestrators connect outbound to RDS. The dashboard does not SSH into remote hosts or open new ports. Communication is entirely through the shared database.
- **Host field validation**: The `host` form field in job queue views must match a registered worker hostname. Reject unknown hostnames to prevent jobs targeted at non-existent hosts from sitting in `pending` forever.

## Acceptance Criteria

### Worker Registry
1. `Worker` model exists with hostname, status, capabilities, last_heartbeat, ip_address fields
2. Workers can be registered via `setup_worker` management command
3. Workers can be viewed/edited/deleted via dashboard UI at `/workers/`
4. Worker hostname is unique (enforced by DB constraint)

### Orchestrator Heartbeat
5. Orchestrator writes heartbeat to Worker row every poll cycle (~10s)
6. Orchestrator auto-registers Worker on first startup if no row exists
7. Orchestrator sets `status="offline"` on graceful shutdown (SIGTERM/SIGINT)

### Stale Detection
8. Dashboard-host orchestrator detects stale workers (heartbeat > 300s) and marks them `stale`
9. Running jobs on stale workers are marked `failed` with descriptive error message
10. Workers that resume heartbeating after being stale auto-recover to `online`

### Dashboard UI
11. Workers panel on main dashboard shows all registered hosts with status, last seen, active job count
12. All job queue forms include host dropdown (default: current host)
13. Offline/stale hosts shown but disabled in host dropdown
14. Host dropdown hidden when only one worker is registered
15. Host column added to recent jobs table and jobs list page
16. Worker management page accessible from sidebar

### Host Targeting
17. Jobs can be queued to any online worker from the dashboard
18. `_get_hostname()` calls in views replaced with form-submitted host value
19. Unknown hostnames (not in workers table) rejected with error message

### Phase Conflict Relaxation
20. `resolve`, `classify`, `enrich-phone`, `seed` jobs: conflict check scoped to `(phase, state_code)` — different states can run concurrently on different hosts
21. `crawl` jobs: conflict check remains global (one at a time)
22. `990-index`, `990-parse`: conflict check unchanged (advisory lock)
23. Two resolve jobs for different states can run simultaneously on different (or same) hosts

### Setup Automation
24. `setup_worker` command verifies RDS connectivity
25. `setup_worker` command verifies S3 access
26. `setup_worker` command registers worker in database
27. `setup_worker` command generates systemd service file
28. `setup_worker` prints clear next-steps for operator

### General
29. All existing single-host behavior preserved when only one worker is registered
30. Dashboard HTMX polling still works (workers panel included in stats partial)
31. No breaking changes to existing job creation functions (host parameter already exists)

## Traps to Avoid

1. **Don't SSH from the dashboard** — all coordination is through the shared RDS. Remote orchestrators are autonomous daemons that poll the `jobs` table. No SSH, no message broker, no RPC.
2. **Don't enforce capabilities** — the `capabilities.phases` field is advisory for operator information only. The orchestrator runs whatever jobs are assigned to its hostname. Enforcement would create hard-to-debug routing failures.
3. **Don't auto-distribute jobs** — the operator picks the target host. Automatic distribution sounds useful but requires load balancing heuristics that are hard to get right. Keep it manual; the 50-state dashboard makes it easy to see where work should go.
4. **Heartbeat race on startup** — the orchestrator must `update_or_create` the Worker row, not just `create`. A host that restarts should reclaim its existing row, not fail with a uniqueness violation.
5. **Phase conflict state_code=NULL** — global jobs (crawl, some classify) have `state_code=NULL`. The relaxed conflict check must still block globally for NULL-state jobs: `if state_code is None, check all running jobs for that phase`.
6. **Remote log files** — log files are written to the local filesystem of the host running the job. The dashboard's log viewer reads `job.log_file` as a local path. For remote jobs, this path doesn't exist on the dashboard host. Don't try to fix this now — it's a non-goal. The log viewer should show "Log file is on remote host {hostname}" instead of a broken read.

## Testing

Manual verification against the live multi-host setup. Checklist:
- Worker registration via CLI on a second host
- Orchestrator heartbeat visible on dashboard
- Job queued to remote host via dashboard form
- Remote orchestrator picks up and executes the job
- Job status updates visible on dashboard from either host
- Stale detection: stop remote orchestrator, verify dashboard marks it stale after 5 min
- Phase conflict relaxation: queue resolve jobs for two different states simultaneously
- Single-host regression: all existing behavior unchanged when only localhost is registered

## Consultation Log

(Pending — will be populated after expert review)

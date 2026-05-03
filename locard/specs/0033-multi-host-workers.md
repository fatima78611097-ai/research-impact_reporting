# Spec 0033: Multi-Host Job Distribution & Remote Workers

## Problem

The pipeline has outgrown single-host execution. National ingest across 50 states means running resolvers, crawlers, classifiers, and phone enrichment concurrently — but the bottleneck is no longer API rate limits, it's operational throughput: site blocks, crawl failures, retry backlogs, and the inability to parallelize work across hosts.

The orchestrator already has multi-host plumbing: `Job.host` field, hostname-filtered polling, per-host orphan recovery. But there's no way to:
- Register and monitor remote hosts from the dashboard
- Target a job at a specific remote host from the UI
- Detect when a remote orchestrator is dead
- Run the same phase for different states concurrently

Today, running on multiple hosts means manually SSH-ing into each machine, cloning the repo, configuring credentials, starting the orchestrator, and manually queuing jobs with the right hostname. The dashboard only shows jobs for `_get_hostname()` (the dashboard host), so remote work is invisible.

## Goals

1. **Worker registry** — dashboard tracks known hosts with their capabilities (GPU, memory tier), online status, and last heartbeat
2. **Host targeting** — queue jobs from the dashboard UI to any registered host, not just localhost
3. **Health monitoring** — detect stale orchestrators (heartbeat timeout), surface health on dashboard so operator can investigate
4. **Visibility** — dashboard shows all jobs across all hosts, with host column in job tables
5. **Host setup verification** — a CLI command (`setup_worker`) that validates a host's connectivity (RDS, S3), registers it with the dashboard, and prints the orchestrator systemd unit for the operator to install

## Non-Goals

- Auto-scaling / elastic host provisioning (use AWS console to launch instances)
- Job migration between hosts (if a host dies, retry the job manually)
- Cross-host log streaming (remote logs stay on the remote host; dashboard shows a message instead)
- Load-based job routing (operator decides which host gets which work)
- Changing the subprocess execution model (orchestrator still launches local subprocesses)
- Auto-failing jobs on stale workers (too dangerous — transient DB partitions could cause zombie processes)
- Installing OS packages, Python deps, or AWS credentials (that's manual pre-setup before `setup_worker`)

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
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "workers"
```

**`is_active` field**: Soft-delete flag. Workers are never hard-deleted because historical `Job.host` values reference hostnames. Setting `is_active=False` hides the worker from dropdowns and health checks but preserves the row for job history display. A deactivated hostname can be re-registered by running `setup_worker` on a new instance with the same hostname (unlikely in EC2, but safe).

**Host identity**: EC2 private hostnames (`ip-172-31-35-76`) are stable for the lifetime of the instance. When an instance is terminated and a new one launched, it gets a new IP and hostname. No collision risk. If an operator reuses a hostname (custom `/etc/hostname`), the `setup_worker` command reclaims the existing Worker row via `update_or_create`.

**Capabilities JSON** — reserved keys with conventional meanings:
```json
{
    "gpu": true,           // boolean — has GPU available
    "memory_gb": 16,       // int — RAM in GB
    "phases": ["resolve"], // list[str] — advisory: what this host is suited for
    "notes": "g6 instance" // str — free-form operator notes
}
```

All keys are advisory — the orchestrator does not enforce them. Additional free-form keys are allowed. The dashboard displays `phases` and `notes`; unknown keys are ignored in the UI.

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
        "is_active": True,
    },
)
```

On shutdown (SIGTERM/SIGINT handler): set `status="offline"` in a finally block. Use a short DB timeout (5s) to avoid hanging on shutdown if RDS is unreachable.

On startup: auto-register if no Worker row exists. If one exists (even if `is_active=False`), update to `online` and set `is_active=True`. This handles both fresh starts and re-registrations.

**Clock skew**: Heartbeat uses Python-side `timezone.now()` (UTC). With the 300s stale threshold, clock skew up to ~30s between hosts is tolerable. If hosts use NTP (default on EC2), skew is typically <1s.

### 3. Stale Worker Detection

Every orchestrator checks worker health in its poll loop — the UPDATE queries are idempotent and cheap, safe to run from multiple hosts simultaneously:

- Workers with `status="online"` and `last_heartbeat` older than 300s → mark `status="stale"` (transient blip — dashboard shows warning)
- Workers with `status="stale"` and `last_heartbeat` older than 1800s (30 min) → mark `status="offline"` (definitely dead)
- Workers with `status="stale"` that resume heartbeating → auto-recover to `online` (their own heartbeat write sets `status="online"`)
- Only active workers (`is_active=True`) are checked

**No auto-failure of jobs on stale workers.** This is a deliberate design choice. If a worker goes stale due to a transient DB/network partition, the subprocess may still be running. Auto-failing the job would leave the process alive and untracked — the remote orchestrator would resume heartbeating but wouldn't re-adopt a "failed" job, creating a zombie. Instead:

- The dashboard shows the worker as stale with a visual warning
- The operator investigates (SSH into the host, check if orchestrator is alive)
- If the host is truly dead, the operator manually retries the job (existing retry mechanism)
- If the host recovers, the orchestrator resumes tracking its jobs normally

**Distributed stale detection**: Every orchestrator runs the health check, so stale detection works as long as any orchestrator is running. If all orchestrators are down, worker statuses freeze — but the operator notices because the dashboard itself is unreachable.

### 4. Dashboard UI Changes

#### 4a. Worker Status Panel (Main Dashboard)

Add a "Workers" section above the state progress table in `dashboard_stats.html`:

```
Workers
┌──────────────────────────┬────────┬──────────┬────────┬──────────┐
│ Host                      │ Status │ Last Seen│ Active │ Phases   │
├──────────────────────────┼────────┼──────────┼────────┼──────────┤
│ Resolver GPU (ip-172-…)  │ 🟢     │ 5s ago   │ 2 jobs │ resolve  │
│ ip-172-31-42-10          │ 🟡     │ 3m ago   │ 1 job  │ all      │
│ ip-172-31-55-03          │ 🔴     │ offline  │ 0 jobs │ crawl    │
└──────────────────────────┴────────┴──────────┴────────┴──────────┘
```

- Show `display_name (hostname)` when display_name is set, otherwise just hostname
- Status indicators: 🟢 online, 🟡 stale (heartbeat > 300s), 🔴 offline
- Active = `Job.objects.filter(host=hostname, status__in=["running", "pending"]).count()`
- Phases = `capabilities.get("phases", ["all"])` — display advisory info
- Only show active workers (`is_active=True`)
- If only one worker exists, still show the panel (operator needs to see health status)

#### 4b. Host Selector on Job Queue Forms

Every job queue form gets a **Host** dropdown:

```html
<select name="host">
    <option value="ip-172-31-35-76" selected>Resolver GPU — ip-172-31-35-76 (this host)</option>
    <option value="ip-172-31-42-10">ip-172-31-42-10 (online)</option>
    <option value="ip-172-31-55-03" disabled>ip-172-31-55-03 (stale)</option>
</select>
```

- Default: current host (`socket.gethostname()`)
- Show `display_name — hostname` when display_name is set, otherwise just hostname
- Online workers enabled, offline/stale workers shown but disabled
- If only one active worker, hide the dropdown and use it implicitly
- If current host is not registered, auto-register it on first dashboard startup (see §2)
- If no workers exist at all (fresh install), the forms work as today — `_get_hostname()` fallback

Update views: `request.POST.get("host", _get_hostname())` replaces direct `_get_hostname()` calls. Validate that the submitted hostname exists in the `workers` table and `is_active=True` and `status != "offline"`. Reject with a form validation error ("Unknown or offline host") if not.

#### 4c. Host Column in Job Tables

The shared `_recent_jobs_table.html` partial and the jobs list page (`jobs.html`) gain a **Host** column. Show hostname truncated to last octet or display_name if set. On the main dashboard, controlled by a `show_host` context variable (default True on dashboard, False on phase pages since those show the current host's jobs).

#### 4d. Worker Management Page

New page at `/dashboard/workers/` (linked from sidebar):

- List all workers (including deactivated, with visual distinction)
- Edit display_name and capabilities (inline or modal)
- Deactivate/reactivate button (sets `is_active` flag — no hard delete)
- Show worker's recent jobs (last 10)
- **No manual registration form** — workers can only be registered via `setup_worker` CLI on the actual host, which verifies connectivity. This prevents creating rows for non-existent hosts that would accept jobs but never execute them.
- **No manual status override** — status is derived exclusively from heartbeat. Manual overrides conflict with heartbeat-derived truth and create confusing state. If a worker shows stale and the operator knows it's fine, they restart the orchestrator on that host.

### 5. Host Setup CLI

A new management command `setup_worker` that validates and registers a host:

```bash
# Run ON the remote host after:
# 1. Cloning the repo
# 2. Installing Python deps (pip install -r requirements.txt)
# 3. Configuring AWS credentials (RDS access via security group, S3 via IAM role or env)
# 4. Setting Django settings (DATABASE_URL or settings.py)

python3 lavandula/dashboard/manage.py setup_worker \
    --display-name "Resolver GPU" \
    --phases resolve,classify \
    --gpu
```

This command:
1. Verifies RDS connectivity: `connections["default"].ensure_connection()` and `connections["pipeline"].ensure_connection()`
2. Verifies S3 access: `boto3.client("s3").head_bucket(Bucket="lavandula-nonprofit-collaterals")` — skip if `--skip-s3` flag (some hosts don't need S3, e.g. resolver-only)
3. Registers (or re-registers) the worker: `Worker.objects.update_or_create(hostname=socket.gethostname(), ...)`
4. **Prints** (does not write) a systemd service file to stdout. The operator copies it to `/etc/systemd/system/` themselves — writing to `/etc/systemd/system/` requires root, and the setup command runs as the application user.

Output:
```
✓ RDS (default): connected
✓ RDS (pipeline): connected
✓ S3 (lavandula-nonprofit-collaterals): accessible
✓ Worker registered: ip-172-31-35-76 (Resolver GPU)

Systemd service file (copy to /etc/systemd/system/lavandula-orchestrator.service):
────────────────────────────────────────────────────────
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
────────────────────────────────────────────────────────

Next steps:
  sudo cp <paste above> /etc/systemd/system/lavandula-orchestrator.service
  sudo systemctl daemon-reload
  sudo systemctl enable --now lavandula-orchestrator
```

The `User` and `WorkingDirectory` are detected from the current process (`getpass.getuser()`, `PROJECT_ROOT`), not hardcoded. Use `getpass.getuser()` instead of `os.getlogin()` because the latter is unreliable under systemd/cron contexts. If the detected user is `root`, refuse and print an error — running the orchestrator as root is a privilege escalation risk.

### 6. Phase Conflict Relaxation

Currently `check_phase_conflict()` blocks globally — only one job per phase across ALL hosts. For national ingest, we need to run resolvers on multiple hosts simultaneously (different states).

Change the conflict check to be **per-state** for state-scoped phases:
- `resolve`, `classify`, `enrich-phone`, `seed`: conflict check scoped to `(phase, state_code)` — two hosts can resolve different states concurrently
- `crawl`: conflict check remains global — only one crawl job at a time (crawler is already concurrent internally)
- `990-index`, `990-parse`: conflict check unchanged (990-family advisory lock)

```python
_PER_STATE_PHASES = {"resolve", "classify", "enrich-phone", "seed"}

def check_phase_conflict(phase: str, state_code: str | None = None) -> bool:
    qs = Job.objects.filter(phase=phase, status="running")
    if state_code and phase in _PER_STATE_PHASES:
        qs = qs.filter(state_code=state_code)
    # NULL state_code → check ALL running jobs for this phase (global conflict)
    if qs.exists():
        return True
    # Ad-hoc PipelineProcess check remains global — ad-hoc processes don't have
    # state_code and are being phased out in favor of jobs. A running ad-hoc
    # process blocks all jobs for that phase regardless of state.
    if PipelineProcess.objects.filter(name=phase, status="running").exists():
        return True
    return False
```

**NULL state_code semantics**: When `state_code` is None (global job), the conflict check is global — it looks at ALL running jobs for that phase. When `state_code` is set, it only checks jobs for that specific state. This means:
- A global classify job (state=NULL) blocks all classify jobs
- A state-scoped classify job (state=TX) only blocks other TX classify jobs
- Two different-state classify jobs can run concurrently

**Pending job duplicates**: The existing `create_*_job` functions already prevent duplicate pending/running jobs via `select_for_update()`. The conflict check in `check_phase_conflict` is about whether the orchestrator starts a pending job, not whether it can be created. Multiple pending jobs for the same (phase, state) cannot accumulate — they're blocked at creation time.

Update `_start_eligible_jobs` in `run_orchestrator.py` to pass `job.state_code` to `check_phase_conflict`.

## Technical Implementation

### Database Changes

One new table (`workers`), one Django migration:

```sql
CREATE TABLE workers (
    id SERIAL PRIMARY KEY,
    hostname VARCHAR(100) UNIQUE NOT NULL,
    display_name VARCHAR(100) DEFAULT '',
    status VARCHAR(20) DEFAULT 'offline',
    capabilities JSONB DEFAULT '{}',
    last_heartbeat TIMESTAMP WITH TIME ZONE,
    registered_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    ip_address INET,
    is_active BOOLEAN DEFAULT TRUE
);
```

No changes to the `jobs` table — `host` field already exists.

### Files Changed

| File | Change |
|------|--------|
| `models.py` | Add `Worker` model |
| `orchestrator.py` | Update `check_phase_conflict()` to accept `state_code` param |
| `run_orchestrator.py` | Add heartbeat writes, stale worker detection, worker auto-registration |
| `views.py` | Add `WorkerListView`, `WorkerEditView`, update all job create views to accept `host` from form, add worker context to dashboard stats |
| `urls.py` | Add `/workers/`, `/workers/<pk>/edit/` routes |
| `dashboard_stats.html` | Add workers panel above state progress table |
| `_recent_jobs_table.html` | Add optional host column (controlled by `show_host` context var) |
| `workers.html` (new) | Worker management page |
| `base.html` | Add Workers link to sidebar nav |
| All job queue templates | Add host dropdown to forms |
| `setup_worker.py` (new mgmt cmd) | Host verification + registration CLI |

### Security

**Trust model**: This is a single-operator system with 2-4 trusted EC2 instances in the same VPC and AWS account. The trust boundary is DB credentials — any process with RDS credentials can read/write all tables. This is the same trust model as the existing orchestrator; this spec does not expand it. All hosts share the same RDS credentials (via SSM or environment). There is no adversarial threat model between hosts.

- **Worker registration**: Workers register themselves via `setup_worker` CLI, which requires DB credentials — same trust boundary as the orchestrator. The dashboard UI cannot create new workers; it can only edit display_name/capabilities and deactivate.
- **Dashboard authz**: All dashboard pages (including worker management) are behind `LoginRequiredMixin` — existing Django auth. Single operator, single user account. No role-based access needed.
- **No new network exposure**: Remote orchestrators connect outbound to RDS. No SSH, no new ports, no message broker. Communication is entirely through the shared database.
- **Host field validation**: The `host` form field in job queue views must match an active, non-offline worker hostname. Reject with a form validation error. Additionally, `create_*_job` functions should validate the host parameter against the workers table (defense-in-depth for any future non-view callers).
- **Hostname spoofing**: Any process with DB credentials could write a Worker row with any hostname. Accepted risk — DB credentials are the trust boundary, and all hosts are operator-controlled.
- **Audit logging**: All job-targeting decisions are logged via the existing `PipelineAuditLog`. Add the `host` field to audit entries when a job is queued so the operator can see which host was targeted.
- **Credential lifecycle**: DB credentials are shared across hosts. Decommissioning a host means terminating the instance — AWS security group rules prevent the dead IP from connecting. No per-host credential rotation is needed for this scale. If the number of hosts grows beyond ~5, revisit with per-host DB roles.

## Acceptance Criteria

### Worker Registry
1. `Worker` model exists with hostname, display_name, status, capabilities, last_heartbeat, ip_address, is_active fields
2. Workers can be registered via `setup_worker` management command
3. Workers can be viewed, edited (display_name, capabilities), and deactivated via dashboard UI at `/workers/`
4. Worker hostname is unique (enforced by DB constraint)
5. Deactivated workers (`is_active=False`) hidden from job queue dropdowns and health checks but visible on worker management page

### Orchestrator Heartbeat
6. Orchestrator writes heartbeat to Worker row every poll cycle (~10s)
7. Orchestrator auto-registers Worker on first startup if no row exists
8. Orchestrator reclaims existing Worker row on restart (update_or_create, not create)
9. Orchestrator sets `status="offline"` on graceful shutdown (SIGTERM/SIGINT)

### Stale Detection
10. Every orchestrator detects stale workers (heartbeat > 300s) and marks them `stale`
11. Stale workers shown with warning indicator on dashboard — no auto-failure of their jobs
12. Workers that resume heartbeating after being stale auto-recover to `online`

### Dashboard UI
13. Workers panel on main dashboard shows all active workers with status, last seen, active job count, phases
14. Worker display shows `display_name (hostname)` when display_name is set
15. All job queue forms include host dropdown (default: current host)
16. Offline/stale hosts shown but disabled in host dropdown
17. Host dropdown hidden when only one active worker exists
18. Host column added to dashboard recent jobs table and jobs list page
19. Worker management page accessible from sidebar
20. No manual worker registration form in UI — CLI only
21. No manual status override buttons — status derived from heartbeat only

### Host Targeting
22. Jobs can be queued to any online active worker from the dashboard
23. `_get_hostname()` calls in views replaced with form-submitted host value (with fallback)
24. Unknown or offline hostnames rejected with form validation error

### Phase Conflict Relaxation
25. `resolve`, `classify`, `enrich-phone`, `seed` jobs: conflict check scoped to `(phase, state_code)` — different states can run concurrently
26. NULL state_code jobs: conflict check remains global for that phase
27. `crawl` jobs: conflict check remains global (one at a time)
28. `990-index`, `990-parse`: conflict check unchanged (advisory lock)
29. Two resolve jobs for different states can run simultaneously on different (or same) hosts

### Remote Log Handling
30. Job detail page: if `job.host != current_hostname` and log file doesn't exist locally, display "Log file is on remote host {hostname}" instead of an error or empty content

### Setup Automation
31. `setup_worker` command verifies RDS connectivity (both `default` and `pipeline` connections)
32. `setup_worker` command verifies S3 access (skippable with `--skip-s3`)
33. `setup_worker` command registers/re-registers worker in database
34. `setup_worker` prints systemd service file to stdout (does not write to filesystem)
35. `setup_worker` detects User and WorkingDirectory from current process, not hardcoded
36. `setup_worker` prints clear next-steps for operator

### Testing
37. Unit tests for `check_phase_conflict`: same-state conflict → True, different-state → False, NULL-state → global conflict, crawl → always global
38. Unit tests for Worker model: `update_or_create` on restart, soft-delete preserves hostname

### General
39. All existing single-host behavior preserved when only one worker is registered
40. Dashboard HTMX polling still works (workers panel included in stats partial)
41. No breaking changes to existing job creation functions (host parameter already exists)
42. Empty workers table (fresh install before any orchestrator starts) does not break any page

## Traps to Avoid

1. **Don't SSH from the dashboard** — all coordination is through the shared RDS. Remote orchestrators are autonomous daemons that poll the `jobs` table. No SSH, no message broker, no RPC.
2. **Don't enforce capabilities** — the `capabilities.phases` field is advisory for operator information only. The orchestrator runs whatever jobs are assigned to its hostname. Enforcement would create hard-to-debug routing failures.
3. **Don't auto-distribute jobs** — the operator picks the target host. Automatic distribution sounds useful but requires load balancing heuristics that are hard to get right. Keep it manual; the 50-state dashboard makes it easy to see where work should go.
4. **Don't auto-fail jobs on stale workers** — a transient DB partition would mark jobs failed while their processes continue running, creating zombies. Stale = alert, not action.
5. **Heartbeat race on startup** — the orchestrator must `update_or_create` the Worker row, not just `create`. A host that restarts should reclaim its existing row, not fail with a uniqueness violation.
6. **Phase conflict state_code=NULL** — global jobs (crawl, some classify) have `state_code=NULL`. The relaxed conflict check must still block globally for NULL-state jobs: `if state_code is None, check all running jobs for that phase`.
7. **Remote log files** — log files are written to the local filesystem of the host running the job. The dashboard's log viewer reads `job.log_file` as a local path. For remote jobs, this path doesn't exist on the dashboard host. Show "Log file is on remote host {hostname}" instead of a broken read.
8. **Worker deletion** — never hard-delete. `Job.host` stores hostnames as strings, not FK references. Deleting a Worker row would leave orphaned hostname references in historical jobs. Use soft-delete (`is_active=False`).
9. **Don't allow UI worker creation** — only `setup_worker` CLI on the actual host should register workers. UI-created workers could point to non-existent hosts, accepting jobs that never execute.
10. **Don't add manual status overrides** — status must be derived from heartbeat. Manual "mark online" creates false positives; manual "mark offline" fights with the heartbeat loop. If a worker is stuck, restart its orchestrator.

## Testing

### Unit Tests
- `check_phase_conflict("resolve", "CA")` with running CA job → `True`
- `check_phase_conflict("resolve", "CA")` with running NY job → `False`
- `check_phase_conflict("resolve", None)` with any running resolve → `True` (global conflict)
- `check_phase_conflict("crawl", None)` with any running crawl → `True`
- `check_phase_conflict("crawl", None)` with no running crawl → `False`
- Worker `update_or_create` on restart: existing row updated, not duplicated
- Worker soft-delete: deactivated worker excluded from `get_online_workers()` but preserved in DB

### Manual Verification
- Worker registration via `setup_worker` CLI on a second host
- Orchestrator heartbeat visible on dashboard (worker panel shows "online", last seen updates)
- Job queued to remote host via dashboard form
- Remote orchestrator picks up and executes the job
- Job status updates visible on dashboard from either host
- Stale detection: stop remote orchestrator, verify dashboard marks it stale after 5 min
- Phase conflict relaxation: queue resolve jobs for two different states simultaneously
- Remote job detail page shows "Log on remote host" message
- Single-host regression: all existing behavior unchanged when only localhost is registered

## Consultation Log

- **Codex spec-review**: REQUEST_CHANGES → addressed (stale-job safety: don't auto-fail; worker deletion: soft-delete; host identity: EC2 hostname stability note; dashboard SPOF: documented; host selector edge cases: auto-register + fallback; UI registration: removed; pending duplicates: existing create functions handle this; remote log AC: added; setup command scope: clarified as post-clone verification)
- **Claude spec-review**: COMMENT → addressed (stale detection failsafe: documented SPOF + acceptable for single-operator; worker deletion: soft-delete with is_active; NULL state conflicts: explicit semantics; _get_hostname sweep: views.py covers all creation paths; unit tests: added for conflict check; capabilities schema: reserved keys documented; setup_worker: print-don't-write + detect user/path; heartbeat shutdown: finally block with timeout; clock skew: noted + NTP; display_name in dropdown: added; host validation error behavior: form validation error)
- **Codex red-team-spec**: REQUEST_CHANGES → addressed (worker authz: all pages behind LoginRequiredMixin, documented; stale host validation: disabled in dropdown + form validation; PipelineProcess global check: documented as intentional with ad-hoc phase-out note; setup_worker vs auto-registration: both valid paths, documented)
- **Claude red-team-spec**: REQUEST_CHANGES (2 CRITICAL, 4 HIGH) → addressed. CRITICAL: (1) hostname spoofing — accepted risk, DB credentials are the trust boundary for this single-operator system, documented; (2) form-level validation — added defense-in-depth validation in create_*_job functions. HIGH: (3) dashboard authz — documented LoginRequiredMixin; (4) audit log — added host field to PipelineAuditLog entries; (5) DB credential lifecycle — documented, SG-based decommissioning; (6) PipelineProcess global block — documented as intentional. MEDIUM: os.getlogin() → getpass.getuser(); stale→offline second threshold (1800s) added; setup_worker refuse root user. LOW: accepted as-is (paste leak advisory, heartbeat rate already 10s, Job.host stays string, capabilities keys renamed to "conventional")

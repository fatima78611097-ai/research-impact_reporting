# Spec 0054: Parse Dashboard (Docling GPU Orchestration)

**Status:** Draft
**Author:** Architect
**Created:** 2026-05-27
**Dependencies:** 0030 (Docling Parse Pipeline)

## Problem Statement

Docling GPU parsing is the only pipeline stage managed entirely via SSH + CLI. All other stages (seed, resolve, crawl, classify, extract, 990) are launched, monitored, and stopped through the pipeline dashboard. This gap causes operational problems:

1. **No pre-flight visibility** — operators must compose CLI flags manually and can't preview eligible document counts or cost estimates before committing to a run
2. **Ad-hoc status queries** — checking progress requires raw SQL against `lava_parse.parse_runs` and `lava_parse.documents`, risking wrong queries (e.g., querying by wrong classification filter inflates counts 4x)
3. **No unified history** — completed runs live only in `lava_parse.parse_runs` with raw JSON stats, not visible alongside other pipeline jobs
4. **Manual instance lifecycle** — launching, monitoring spot reclaims, and terminating GPU instances requires SSH to cloud2 and running `manage.py parse_documents` directly
5. **No abort mechanism** — stopping a run requires finding the orchestrator PID and killing it, then manually checking for orphan EC2 instances

The corpus will continue growing. Education (NTEE B) alone has ~11K annual/impact reports to parse, and the national crawl will produce 100K+ documents across all verticals. Parse operations need the same dashboard-grade management as every other pipeline stage.

## Goals

1. **Dashboard page for parse** — a `/parse/` page in the pipeline dashboard showing current run status, eligible document counts by NTEE/classification, and run history
2. **Launch with dry-run** — form to configure a parse run (NTEE filter, classification filter, instance type, max hours, batch size) with a dry-run preview showing eligible count, estimated GPU hours, and estimated cost before committing
3. **Live progress** — HTMX-polled progress display showing docs succeeded/failed/total, processing rate (docs/min), ETA, elapsed time, and instance health (running/terminated/spot-reclaimed)
4. **Run control** — ability to stop a running parse from the dashboard (terminates instance and marks run finished)
5. **Run history** — table of past runs with tag, start/finish times, document counts, success/failure rates, and total cost
6. **Integration with job system** — parse runs tracked as `Job` records in `lava_dashboard.jobs` so they appear in the master job queue alongside other pipeline stages

## Non-Goals

- Multi-worker concurrent parsing (requires `fetch_work_batch` rewrite for row locking — separate spec)
- Worker code changes (worker runs on GPU instance, deployed via S3 tarball — out of scope)
- Real-time log streaming from the GPU instance (SSM log relay is fragile; progress polling is sufficient)
- Custom AMI creation/building from the dashboard (AMIs are built externally)
- AZ pricing comparison or spot price graphs
- Complex scheduling (cron, recurring runs, dependency chains)

## Hard Constraints (DO NOT VIOLATE)

1. **Use the existing `Job` model** (`pipeline/models.py:153`). Do NOT create a new model, table, or abstraction for tracking parse jobs. Parse runs are `Job` records with `phase='parse'`. Period.
2. **Use the existing `STAGE_REGISTRY`** (`pipeline/stages.py:55`). Add a `"parse"` entry. Do NOT create a separate registry, config system, or stage abstraction.
3. **Use the existing `COMMAND_MAP`** (`pipeline/orchestrator.py:27`). Add a `"parse"` entry. Do NOT create a new command dispatch mechanism.
4. **Use the existing orchestrator subprocess pattern**. The dashboard starts the `parse_documents` management command as a subprocess, exactly like it starts crawl, classify, and every other stage. Do NOT build a custom process manager, task queue, or async worker for parse.
5. **No new Django models or tables**. The only model change is adding `("parse", "Parse")` to `Job.PHASE_CHOICES`. All required `Job.STATUS_CHOICES` values (`scheduled`, `pending`, `running`, `completed`, `failed`, `cancelled`) already exist. If you think you need a new model, you are going off-spec. Stop and ask.
6. **The `parse_documents` management command already exists**. Adapt it (add Job progress writes, SIGTERM handler, log file output). Do NOT rewrite it or replace it with a new command.

## Technical Context

### Existing Parse Infrastructure

The orchestrator is a Django management command (`parse_documents.py`) that:
1. Creates/resumes a `lava_parse.parse_runs` record
2. Launches a G6.2xlarge spot instance across 5 AZ subnets (with fallback)
3. Deploys worker code via SSM + S3 tarball
4. Starts the worker process via SSM `nohup`
5. Polls `parse_runs.stats_json` for progress (heartbeat loop)
6. Handles spot reclaims with automatic relaunch (up to 3 attempts)
7. Terminates instance when done or max hours reached

The worker (`lavandula.parse.worker`) runs on the GPU instance:
- Downloads PDFs from S3 in batches
- Parses with Docling, writes to `lava_parse.documents/sections/tables`
- Updates `parse_runs.stats_json` every N documents
- Detects spot termination notices via instance metadata
- Filters by `--priority` (classification) and `--ntee` (NTEE code prefix)

### Dashboard Patterns to Follow

All other pipeline stages follow this pattern:
- **Stage registration** in `stages.py` (`STAGE_REGISTRY` dict with `StageDefinition`)
- **COMMAND_MAP entry** in `orchestrator.py` mapping phase name → command + parameters
- **Job model** in `models.py` tracking status, progress, config, log file
- **View** in `views.py` with GET (render page) and POST (create job)
- **Template** in `templates/pipeline/` with form, progress display, and history table
- **URL** in `urls.py` mounting the stage page

Parse differs from other stages in one key way: **it manages an EC2 instance lifecycle**, not a local subprocess. The orchestrator runs locally on cloud2 as a subprocess (like other jobs), but it in turn manages a remote GPU instance. The dashboard needs to surface both levels: the orchestrator process status AND the GPU instance status.

### Data Model

**Existing tables (read-only from dashboard perspective):**

`lava_parse.parse_runs`:
- `id`, `run_tag`, `started_at`, `finished_at`
- `config_json` — stores priority, ntee_filter, instance_type, batch_size, max_hours
- `stats_json` — stores total, succeeded, failed, skipped, transient_skipped, start_time, end_time, duration_seconds
- `instance_id` — EC2 instance ID

`lava_parse.documents`:
- `content_sha256`, `source_org_ein`, `parse_version`, `parsed_at`
- `page_count`, `section_count`, `table_count`, `figure_count`, `total_text_chars`
- `parse_duration_ms`, `error`, `metadata_json`

**Dashboard-managed table:**

`lava_dashboard.jobs` — parse runs will be tracked here as `phase='parse'` jobs, alongside all other pipeline stages. The `config_json` field stores the parse-specific parameters.

### State Ownership

There are three state holders. This hierarchy resolves conflicts:

1. **`lava_parse.parse_runs`** — source of truth for parse progress. The worker writes `stats_json` directly. The orchestrator writes `instance_id`, `started_at`, `finished_at`. The dashboard reads this table for progress display.
2. **`lava_dashboard.jobs`** — source of truth for job lifecycle (pending → running → completed/failed/cancelled). The dashboard manages this. The orchestrator updates `progress_current`/`progress_total` as a convenience, but never controls its own `Job.status`.
3. **EC2 instance state** — ephemeral. Queried via boto3, cached 30 seconds. Never persisted. Used only for the instance health badge.

**Conflict resolution rules:**
- If `Job.status` is `running` but `parse_runs.finished_at` is set → the worker finished but the orchestrator died before updating the Job. Dashboard marks Job as `completed`.
- If `Job.status` is `running` but orchestrator PID is dead and `parse_runs.finished_at` is NULL → the orchestrator crashed. Dashboard marks Job as `failed`, terminates any orphan EC2 instance.
- If `parse_runs` rows exist without a matching `Job` → legacy CLI runs. Display in run history but without Job metadata (no log file, no job queue entry).
- If `Job` exists but orchestrator hasn't created the `parse_runs` row yet → Job is in early startup. Show "Initializing..." in the active run panel.

**Job lifecycle for parse** (all states already exist in `Job.STATUS_CHOICES` — no enum changes needed):

```
scheduled ──→ pending ──→ running ──→ completed
    │            │           │
    └──→ cancelled ←─────────┘
                 │
                 └──→ failed
```

- `scheduled` — operator set a future `start_at`. Orchestrator subprocess is alive but sleeping.
- `pending` — orchestrator is starting up (acquiring lock, creating parse_run). Immediate launches skip `scheduled` and start here.
- `running` — EC2 instance launched, worker processing documents.
- `completed` — worker reported all batches done, or no eligible docs remain.
- `failed` — orchestrator crashed, exceeded relaunch attempts, capacity exhausted, or unrecoverable error.
- `cancelled` — operator pressed Stop.

**Who writes each status transition:**

| Transition | Writer | Trigger |
|-----------|--------|---------|
| → `scheduled` | Dashboard | Form POST with `start_at` set |
| → `pending` | Dashboard | Form POST without `start_at`, or orchestrator wakes from scheduled sleep |
| → `running` | Orchestrator | EC2 instance launched and worker started |
| → `completed` | Orchestrator | Worker finished or no eligible docs |
| → `failed` | Orchestrator (normal) or Dashboard (crash recovery) | Error, capacity exhausted, or stale job detection |
| → `cancelled` | Dashboard | Operator pressed Stop |

### Launch and Stop Semantics

**Launch:**
1. Dashboard checks for any `Job` with `phase='parse'` and `status='running'`. If found, reject with message: "A parse run is already active (tag: X). Stop it before launching a new one."
2. Dashboard checks `run_tag` uniqueness against `parse_runs` table. If tag exists with `finished_at IS NOT NULL`, reject with suggestion to append `-v2`. If tag exists with `finished_at IS NULL`, offer to resume.
3. Create `Job` record with `status='pending'`, `config_json` from form.
4. Start orchestrator subprocess. Orchestrator acquires advisory lock (second layer of protection).
5. If orchestrator fails to start (exits immediately), mark Job as `failed`.
6. Double-submit prevention: Launch button disabled via HTMX on click (`hx-disabled-elt`). Server-side check (#1 above) is the authoritative guard.

**Stop:**
1. Find the running parse `Job` and its `parse_runs.instance_id`.
2. Send SIGTERM to orchestrator PID. Orchestrator's SIGTERM handler terminates EC2 instance and marks `parse_runs.finished_at`.
3. If orchestrator PID is already dead, terminate EC2 instance directly via boto3 (idempotent — `terminate_instances` on an already-terminated instance is a no-op).
4. Mark `Job.status = 'cancelled'`, `Job.finished_at = now()`.
5. Mark `parse_runs.finished_at = now()` if still NULL.
6. All stop operations are idempotent — pressing Stop twice has no additional effect.

**Stop during startup/relaunch:** Safe. If the orchestrator is in `_launch_with_capacity_retry` (sleeping between capacity retries), SIGTERM interrupts the sleep. If an instance is mid-launch, `terminate_instances` handles any state.

### Edge Cases

| Scenario | Handling |
|----------|----------|
| Orchestrator crashes before creating `parse_runs` | Job stays `running` with no progress. Stale job detector (existing dashboard feature) marks it `failed` after heartbeat timeout. |
| Spot reclaim during stop | No issue — `terminate_instances` is idempotent. Instance may already be `shutting-down`. |
| `stats_json` is NULL or malformed | Progress display shows "Waiting for first batch..." instead of computed stats. Use `json.loads` with fallback to empty dict. |
| EC2 `describe_instances` rate-limited or fails | Return cached state. If no cache, show "Instance status unavailable" badge. Never block page load on EC2 API. |
| Instance launched but worker never starts | Orchestrator's health check (30s after start) detects this and retries. Dashboard shows orchestrator's progress as stalled until relaunch or timeout. |
| User navigates away during launch | Orchestrator continues as subprocess. Dashboard reconnects on next page load by finding the running Job. |
| `max_hours` expires during a batch | Orchestrator terminates instance, worker loses in-progress batch. Already-committed documents are preserved. No data loss. |
| Selected AMI deregistered or unavailable | EC2 `run_instances` fails with `InvalidAMIID.NotFound`. Orchestrator raises `CommandError`, Job marked `failed`. Dashboard pre-validates by checking AMI list on form load; stale selections caught at launch. |
| Legacy `parse_runs` without a `Job` | Display in run history with tag, dates, and stats from `parse_runs`. Show "CLI run" badge instead of Job metadata. No log file link. |

### Security and Access Control

The dashboard is an internal tool running on cloud2, accessible only via Tailscale VPN. It follows the same auth model as all other pipeline pages:

- **Django login required** — all pipeline views require `@login_required` (existing middleware)
- **CSRF protection** — all POST forms use `{% csrf_token %}` (existing Django default)
- **No public exposure** — dashboard binds to `0.0.0.0:4300` but is only reachable via Tailscale (`100.x.x.x`)
- **Single operator** — only ronp has dashboard credentials. No role-based access control needed.
- **EC2 permissions** — dashboard uses the `cloud2_lavandulagroup` IAM role which already has `ec2:RunInstances`, `ec2:TerminateInstances`, `ec2:DescribeInstances`, `ssm:SendCommand`. No new IAM changes required.
- **No secrets in forms** — all sensitive values (RDS endpoint, IAM tokens) come from SSM parameters, not user input.
- **Subprocess injection prevention** — the orchestrator is launched via Python's `subprocess.Popen` with arguments as a list (not a shell string). User-supplied parameters (run_tag, NTEE filter, etc.) are validated against `ParamSpec` regex patterns in `STAGE_REGISTRY` before being appended to the argument list. No shell interpolation occurs. This matches the existing pattern used by all other pipeline stages.
- **Parameterized SQL** — all database queries use psycopg2 parameterized statements (`%(name)s` placeholders). No user input is interpolated into SQL strings. This is enforced by the existing `lavandula/parse/db.py` module which uses parameterized queries exclusively (see `get_eligible_count` at line 309, `fetch_work_batch` at line 48).
- **Worker code integrity** — the S3 tarball deployment is pre-existing infrastructure from Spec 0030. The S3 bucket (`lavandula-nonprofit-collaterals`) is write-restricted to the CI/deploy pipeline. This spec does not change the deployment mechanism. Hardening (code signing, hash verification) is a separate infrastructure concern.

### Eligible Count and Cost Estimation

**Eligible count** — documents matching the form's filters that have not been parsed:

```
eligible = COUNT(corpus rows)
  WHERE classification IN (priority_filter)    -- default: annual, impact
  AND ntee_code LIKE (ntee_filter)             -- optional, e.g., 'B%'
  AND content_sha256 NOT IN (parsed documents)
```

This uses the existing `db.get_eligible_count()` function (already exists in `lavandula/parse/db.py:309`). The dashboard calls it directly via a psycopg2 connection from `_get_conn()`.

If `max_docs` is set, the displayed eligible count is `min(eligible, max_docs)`.

**Cost estimation formulas** (matching orchestrator constants):

```
estimated_pages    = eligible × AVG_PAGES_PER_DOC        (30)
estimated_seconds  = estimated_pages × SECONDS_PER_PAGE  (0.49)
estimated_hours    = estimated_seconds / 3600
estimated_cost     = estimated_hours × rate
  where rate = SPOT_RATE_PER_HOUR ($0.60) or ONDEMAND_RATE_PER_HOUR ($0.98)
```

Capped by `max_hours`: if `estimated_hours > max_hours`, display `max_hours` as the estimate and note "will not complete in one run."

**Actual cost** (for run history): `stats_json.duration_seconds / 3600 × rate`. Rate determined from `config_json.no_spot`.

## Technical Implementation

### 1. Stage Registration

Add a `"parse"` entry to `STAGE_REGISTRY` in `stages.py`:

```python
"parse": StageDefinition(
    name="parse",
    display_name="Docling Parse",
    command=["python3", "lavandula/dashboard/manage.py", "parse_documents"],
    parameters={
        "run_tag": ParamSpec(required=True, type="string", cli_flag="positional",
                             pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$"),
        "ntee": ParamSpec(type="string", cli_flag="--ntee",
                          pattern=r"^[A-Z][A-Z0-9%]*$"),
        "priority": ParamSpec(type="string", cli_flag="--priority",
                              pattern=r"^[a-z_]+(,[a-z_]+)*$"),
        "instance_type": ParamSpec(type="choice", cli_flag="--instance-type",
                                   choices=["g6.2xlarge", "g6.4xlarge", "g6.8xlarge"]),
        "max_hours": ParamSpec(type="integer", cli_flag="--max-hours",
                               min_value=1, max_value=24),
        "batch_size": ParamSpec(type="integer", cli_flag="--batch-size",
                                min_value=10, max_value=5000),
        "no_spot": ParamSpec(type="boolean", cli_flag="--no-spot"),
        "max_docs": ParamSpec(type="integer", cli_flag="--max-docs",
                              min_value=1, max_value=999999),
        "retry_errors": ParamSpec(type="boolean", cli_flag="--retry-errors"),
        "ami_id": ParamSpec(type="choice", cli_flag="--ami-id"),
        "start_at": ParamSpec(type="string", cli_flag="--start-at",
                              pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$"),
        "capacity_wait_hours": ParamSpec(type="integer", cli_flag="--capacity-wait-hours",
                                         min_value=1, max_value=12),
    },
    predecessors=["classify"],
    conflict_group="global",  # Only one parse orchestrator at a time (advisory lock)
    provenance_column=None,
    retry_policy=RetryPolicy(max_attempts=1),  # Manual relaunch only — GPU runs are expensive
    resource_class="light",  # Orchestrator itself is lightweight (runs on cloud2)
)
```

Add `"parse"` to `COMMAND_MAP` in `orchestrator.py` with matching parameter definitions.

Add `("parse", "Parse")` to `Job.PHASE_CHOICES`.

### 2. Dashboard View

Create a `ParseView` in `views.py` with:

**GET — Render page with:**

- **Eligible counts panel**: Query `lava_parse` + `lava_corpus` to show counts by NTEE major code and classification, filtered to unparsed documents. Shows what's available to parse before launching.
- **Active run panel** (if running): Live progress from `parse_runs.stats_json` — succeeded, failed, total, rate (docs/min), ETA, elapsed time. Instance status from EC2 API (running/terminated). Spot vs on-demand. AZ.
- **Run configuration form**: Fields for run_tag, NTEE filter, priority (classification), instance type, AMI selection, max hours, batch size, spot/on-demand toggle, max docs cap.
- **Dry-run results** (if dry-run was requested): Eligible count, estimated pages, estimated GPU hours, estimated cost.
- **Run history table**: Past runs from `lava_parse.parse_runs` joined with `lava_dashboard.jobs`, showing tag, dates, doc counts, success rate, duration.

**POST — Actions:**

- **dry-run**: Calls `db.get_eligible_count()` with the form parameters and returns estimates. No EC2 resources touched.
- **launch**: Validates parameters, creates a `Job` record with `phase='parse'`, then starts the orchestrator management command as a subprocess (same as other stages). The orchestrator handles EC2 lifecycle.
- **stop**: Finds the running parse `Job`, kills the orchestrator subprocess, calls `_safe_terminate` on the EC2 instance via boto3, marks the job and parse_run as finished.

### 3. Progress Polling

The dashboard already uses HTMX polling (`hx-get` with `hx-trigger="every 10s"`) for live job progress. Parse uses the same pattern:

**Progress endpoint** (`/parse/progress/`):
1. Read `parse_runs.stats_json` for doc counts and rate
2. Read `parse_runs.instance_id` and call EC2 `describe_instances` for instance state
3. Compute processing rate: `succeeded / elapsed_minutes` docs/min
4. Compute ETA: `remaining_eligible / rate` minutes
5. Return HTML partial with progress bar, stats, and instance badge

**Instance health badge:**
- 🟢 `running` — worker active
- 🟡 `pending` — instance launching
- 🔴 `terminated` / `shutting-down` — spot reclaimed or crashed (orchestrator will relaunch)
- ⚪ `none` — no active run

### 4. Eligible Counts Query

The dry-run and dashboard overview need accurate eligible counts. Centralize this as a view helper that queries:

```sql
SELECT
    ns.ntee_code,
    c.classification,
    COUNT(*) as eligible
FROM lava_corpus.corpus c
JOIN lava_corpus.nonprofits_seed ns ON c.source_org_ein = ns.ein
WHERE c.classification IN ('annual', 'impact', 'hybrid')
  AND c.content_sha256 NOT IN (
      SELECT content_sha256 FROM lava_parse.documents
  )
GROUP BY ns.ntee_code, c.classification
ORDER BY COUNT(*) DESC
```

This gives a breakdown by NTEE code and classification. The form's NTEE filter narrows this. Display as a summary table on the parse page so the operator always sees what's available.

### 5. Cost Tracking

The orchestrator already has cost constants (`SPOT_RATE_PER_HOUR`, `ONDEMAND_RATE_PER_HOUR`). For run history, compute actual cost from `stats_json.duration_seconds` and the pricing mode stored in `config_json`.

Display in run history: `$X.XX (Y.Yh spot)` or `$X.XX (Y.Yh on-demand)`.

### 6. AMI Selection

The form includes an AMI dropdown populated from EC2. The orchestrator currently reads a single AMI ID from SSM parameter `/cloud2.lavandulagroup.com/docling-ami-id`. The dashboard replaces this with a chooser:

**Population:** On page load, query EC2 `describe_images` filtered by:
- `owner-id: self` (owned by this AWS account)
- `tag:Purpose: docling-worker` (tagged during AMI build)

Display each AMI as: `{Name} ({ami-id}) — {CreationDate}`, sorted newest first.

**Default:** The AMI currently stored in the SSM parameter is pre-selected. If the SSM AMI is not in the list (deleted/deregistered), show a warning.

**Orchestrator change:** Add `--ami-id` flag to `parse_documents`. If provided, use it instead of reading SSM. If omitted, fall back to SSM (backward compatible for CLI usage).

**No AMI creation:** The dashboard only lists and selects existing AMIs. Building AMIs remains an external process (packer, manual snapshot, etc.).

### 7. Scheduled Launch and Capacity Wait

The form includes optional scheduling fields:

**Start At** — datetime picker (ISO 8601, `YYYY-MM-DDTHH:MM`, interpreted as **UTC**). The form displays the equivalent local time (US Eastern) as a hint. If set, the orchestrator sleeps until this time before attempting to launch an instance. If blank, launch immediately (current behavior).

**Capacity Wait Hours** — integer, 1–12, default 1. Controls how long the orchestrator retries when no spot capacity is available. Replaces the hardcoded `MAX_CAPACITY_RETRIES = 12` (1 hour) in `_launch_with_capacity_retry`.

**Orchestrator changes:**

```python
# At the top of _execute_run, before _launch_with_capacity_retry:
if options.get("start_at"):
    start_at = datetime.fromisoformat(options["start_at"])
    wait_seconds = (start_at - datetime.now()).total_seconds()
    if wait_seconds > 0:
        self.stdout.write(f"Scheduled start: waiting until {start_at}\n")
        time.sleep(wait_seconds)

# In _launch_with_capacity_retry, replace hardcoded MAX_CAPACITY_RETRIES:
capacity_wait_hours = options.get("capacity_wait_hours", 1)
max_capacity_retries = (capacity_wait_hours * 3600) // CAPACITY_RETRY_INTERVAL
```

**Job lifecycle with scheduling:**
1. Form submitted → `Job` created with `status='scheduled'`
2. Orchestrator subprocess starts, sleeps until `start_at`
3. `start_at` reached → orchestrator transitions Job to `running`, begins instance launch
4. No capacity → retries every 5 minutes for up to `capacity_wait_hours`
5. Still no capacity → Job marked `failed` with message "No spot capacity after N hours"

**Dashboard display when scheduled:**
- Active run panel shows: "Scheduled for 2:00 AM EST (in 4h 15m)" with countdown
- Capacity wait shows: "Waiting for spot capacity (attempt 3/36, next retry in 5m)"
- Stop button works during both wait phases (interrupts sleep)

**Typical overnight workflow:**
1. Operator configures run at 6 PM: NTEE B%, start at 2:00 AM, capacity wait 6 hours
2. Goes home. Orchestrator sleeps until 2:00 AM.
3. 2:00 AM: tries all 5 AZs. No capacity.
4. 2:05 AM – 8:00 AM: retries every 5 minutes (72 attempts over 6 hours)
5. 3:15 AM: spot available in us-east-1d. Instance launches, worker starts.
6. Operator checks dashboard at 9 AM: sees run in progress, 2,100 docs done.

### 8. Template

Follow the existing dashboard template patterns (Bootstrap, HTMX). The parse page layout:

```
┌─────────────────────────────────────────────────┐
│ Docling Parse                                    │
├─────────────────────────────────────────────────┤
│ ┌─── Active Run ──────────────────────────────┐ │
│ │ edu-b1 | 498/10,983 (4.5%) | 3.5 docs/min  │ │
│ │ Instance: i-0abc... 🟢 running (us-east-1c) │ │
│ │ Elapsed: 2h 15m | ETA: ~48h | Cost: $1.35   │ │
│ │ [Stop Run]                                   │ │
│ └──────────────────────────────────────────────┘ │
│                                                   │
│ ┌─── Eligible Documents ──────────────────────┐ │
│ │ NTEE B (Education): 10,485 remaining         │ │
│ │ NTEE P (Human Svc): 8,234 remaining          │ │
│ │ NTEE T (Philanthropy): 3,102 remaining       │ │
│ │ ... (annual: XX,XXX | impact: X,XXX)         │ │
│ └──────────────────────────────────────────────┘ │
│                                                   │
│ ┌─── Launch New Run ──────────────────────────┐ │
│ │ Run tag: [____________]                      │ │
│ │ NTEE filter: [B%_______] (blank = all)       │ │
│ │ Classifications: [annual, impact ▼]          │ │
│ │ Instance: [g6.2xlarge ▼]  Spot: [✓]         │ │
│ │ AMI: [docling-v2.1 (ami-0abc...) ▼]         │ │
│ │ Max hours: [24] Batch: [500] Max docs: [__]  │ │
│ │ Start at: [________] Capacity wait: [1]h     │ │
│ │ [Dry Run]  [Launch Now]  [Schedule]          │ │
│ └──────────────────────────────────────────────┘ │
│                                                   │
│ ┌─── Run History ─────────────────────────────┐ │
│ │ Tag     │ Date    │ Docs  │ Rate │ Cost     │ │
│ │ edu-b1  │ May 27  │ 498   │ 3.5  │ $3.60    │ │
│ │ p20-v2  │ May 20  │ 3,335 │ 3.2  │ $15.40   │ │
│ └──────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

### 9. Orchestrator Adaptation

The existing `parse_documents` management command needs these changes to work as a dashboard-launched subprocess:

1. **Write progress to Job model**: In addition to updating `parse_runs.stats_json`, the orchestrator should update `Job.progress_current` and `Job.progress_total` so the dashboard's generic job progress display works.
2. **Log to file**: Ensure output goes to a log file path that the Job model references (other stages already do this via `LOG_DIR`).
3. **Handle SIGTERM gracefully**: When the dashboard sends SIGTERM to stop a run, the orchestrator should terminate the EC2 instance and mark both the parse_run and Job as finished.
4. **`--start-at` flag**: ISO 8601 datetime. Sleep until this time before launching. Interruptible by SIGTERM.
5. **`--capacity-wait-hours` flag**: Integer 1–12, default 1. Controls retry window for spot capacity.
6. **`--ami-id` flag**: AMI to launch. Falls back to SSM parameter if omitted.

These changes are to the orchestrator (runs on cloud2), NOT the worker (runs on GPU instance). No S3 worker code redeploy needed.

## Acceptance Criteria

### Core Functionality
1. Parse page loads at `/parse/` in the pipeline dashboard
2. Eligible document counts display correctly, broken down by NTEE major code
3. Dry-run shows eligible count, estimated GPU hours, and cost without launching any resources
4. Launch creates a Job record and starts the orchestrator subprocess
5. Only one parse run can be active at a time (enforced by advisory lock + dashboard UI). If a run is active, the launch form is disabled and shows "A parse run is already active (tag: X). Stop it before launching a new one."
6. Live progress updates every 10 seconds via HTMX polling
7. Progress shows: succeeded, failed, total, rate (docs/min), ETA, elapsed, cost so far
8. Instance health badge reflects actual EC2 instance state
9. Stop button terminates EC2 instance and marks run as finished
10. Run history shows all past parse runs with summary stats
11. Scheduled launch: setting "Start at" creates Job with `status='scheduled'`, orchestrator waits until that time, then launches
12. Capacity wait: configurable 1–12 hours of retry when no spot capacity available
13. Scheduled run displays countdown ("Scheduled for 2:00 AM EST, in 4h 15m") and capacity retry status
14. Stop works during scheduled wait and capacity retry (interrupts sleep, no orphans)

### Integration
15. Parse jobs appear in the master job queue (`/jobs/`) alongside other pipeline stages
16. Parse stage registered in `STAGE_REGISTRY` and `COMMAND_MAP`
17. `Job.PHASE_CHOICES` includes `("parse", "Parse")`. No `STATUS_CHOICES` changes needed — `scheduled`, `pending`, `running`, `completed`, `failed`, `cancelled` all exist already.
18. Orchestrator updates `Job.progress_current`/`progress_total` during polling loop
19. Orchestrator writes logs to `LOG_DIR` path referenced by `Job.log_file`

### Safety
20. Launch form validates all parameters before submission (run_tag format, NTEE pattern, numeric ranges)
21. Stop action confirms before terminating ("Are you sure? This will terminate the GPU instance.")
22. Dry-run is the default action — Launch requires explicit button click
23. Form pre-fills with safe defaults: `priority=annual,impact`, `instance_type=g6.2xlarge`, `max_hours=24`, `batch_size=500`, spot enabled, `capacity_wait_hours=1`, `start_at` blank (immediate)
24. NTEE filter and classification filter are prominently displayed so the operator knows exactly what scope they're launching

### Display
25. Active run panel only shows when a parse run is in progress or scheduled
26. Eligible counts panel always shows (helps plan next run)
27. Run history is newest-first, shows at least the last 20 runs. Legacy CLI runs (no Job record) show with "CLI run" badge.
28. Cost estimates use current spot/on-demand rates from orchestrator constants

## Traps to Avoid

1. **Don't query eligible counts with `!= 'not_relevant'`** — this was the bug that inflated Education from 11K to 47K. Always use `IN ('annual', 'impact', 'hybrid')` matching the worker's actual `--priority` filter.
2. **Don't call EC2 APIs on every page load** — cache instance state for 30 seconds. EC2 describe_instances has rate limits.
3. **Don't stream logs from GPU instance** — SSM log relay is fragile. Use the progress polling pattern (read `stats_json`) instead.
4. **Don't create a second orchestrator model** — parse runs are already tracked in `lava_parse.parse_runs`. The dashboard adds a `Job` record for integration, but the orchestrator's source of truth remains `parse_runs`.
5. **Remember the `run_tag` uniqueness constraint** — `parse_runs` enforces unique tags. The form should check for existing tags before launch and suggest a suffix (e.g., `edu-b1-v2`) if the tag is taken.
6. **Orchestrator is the subprocess, not the worker** — the dashboard manages the orchestrator process (local). The orchestrator manages the GPU instance (remote). Don't conflate these two levels.
7. **DO NOT build a new job system** — a past spec went off-track by inventing a custom job/queue mechanism instead of using the existing `Job` model and orchestrator. This spec adds parse as a new stage to the existing system. If you find yourself creating new models, new tables, new abstractions, or "improving" the job infrastructure — STOP. You are out of scope. See Hard Constraints above.

## Testing Requirements

### Unit Tests
1. **Eligible count query** — verify `get_eligible_count` with known fixture data returns correct count for various filter combinations (NTEE + classification). Specifically test that `NOT IN` excludes already-parsed documents.
2. **Cost estimation** — verify formulas produce correct values for edge cases: 0 eligible docs, 1 doc, max_docs cap, max_hours cap, spot vs on-demand rates.
3. **Run tag validation** — verify pattern rejects invalid tags (spaces, special chars, empty) and accepts valid ones.
4. **State conflict resolution** — mock Job and parse_runs states to verify the dashboard correctly handles: orphaned jobs (PID dead), finished runs with stale Job status, legacy parse_runs without Jobs.

### Integration Tests
5. **Launch flow** — POST to launch endpoint creates a Job record with correct config, starts a subprocess, and returns redirect to parse page. (Mock EC2/SSM.)
6. **Stop flow** — POST to stop endpoint marks Job cancelled, calls terminate_instances (mocked), handles already-terminated instance gracefully.
7. **Double-launch prevention** — second POST to launch while a Job is running returns error, does not create a second Job.
8. **Dry-run flow** — POST with dry-run action returns eligible count and cost estimate without creating a Job or touching EC2.
9. **Progress endpoint** — returns valid HTML partial with stats from mock parse_runs data. Handles NULL/malformed stats_json gracefully.
10. **Scheduled launch** — POST with `start_at` in the future creates Job with `status='scheduled'`. Orchestrator subprocess starts but does not call EC2 APIs until `start_at`. SIGTERM during wait cancels cleanly.
11. **Capacity wait** — with `capacity_wait_hours=3`, orchestrator retries 36 times (3h / 5min) before giving up. Verify retry count matches parameter.

### Manual Verification
12. **Page load** — `/parse/` renders with eligible counts, form, and run history.
13. **Dry-run** — form submission with dry-run shows estimates, no EC2 launched.
14. **Full launch + monitor + stop cycle** — launch a real parse run, watch progress update, press stop, confirm instance terminated and Job marked cancelled.

## Migration Requirements

- Add `("parse", "Parse")` to `Job.PHASE_CHOICES` — this is a Django model change requiring a migration
- No new tables needed — `lava_parse.parse_runs` already exists, and `lava_dashboard.jobs` already exists

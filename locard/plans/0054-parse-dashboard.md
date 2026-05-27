# Plan 0054: Parse Dashboard (Docling GPU Orchestration)

**Spec:** `locard/specs/0054-parse-dashboard.md`
**Status:** Draft
**Created:** 2026-05-27

## Overview

Bring Docling GPU parsing into the pipeline dashboard alongside all other stages. The orchestrator already exists (`parse_documents.py`); this plan integrates it with the Job model, STAGE_REGISTRY, and dashboard UI.

## Hard Constraints Reminder

Before implementing ANYTHING, re-read the spec's Hard Constraints section. In summary:
- Use existing `Job` model — no new models or tables
- Use existing `STAGE_REGISTRY` — no new registry
- Use existing `COMMAND_MAP` — no new dispatch
- Use existing subprocess pattern — no task queues
- Adapt `parse_documents` — do not rewrite or replace it
- Only model change: `("parse", "Parse")` added to `PHASE_CHOICES`

## Implementation Steps

### Step 1: Django Migration — Add `parse` to PHASE_CHOICES

**File:** `pipeline/models.py`

Add `("parse", "Parse")` to `Job.PHASE_CHOICES` after `("promote-classify", "Promote Classification")`:

```python
PHASE_CHOICES = [
    ...
    ("promote-classify", "Promote Classification"),
    ("parse", "Parse"),
]
```

Generate and verify migration:
```bash
cd /home/ubuntu/research && python3 lavandula/dashboard/manage.py makemigrations pipeline
```

The migration only changes field choices — no schema change, no data migration. Django stores `phase` as a CharField; the choices list is for validation/display only. Still requires a migration file.

**Acceptance:** `python3 lavandula/dashboard/manage.py migrate --check` passes.

---

### Step 2: Stage Registration — STAGE_REGISTRY + COMMAND_MAP

**File:** `pipeline/stages.py`

Add after the `"promote-classify"` entry (line ~283):

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
        "ami_id": ParamSpec(type="string", cli_flag="--ami-id",
                            pattern=r"^ami-[a-f0-9]{8,17}$"),
        "start_at": ParamSpec(type="string", cli_flag="--start-at",
                              pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$"),
        "capacity_wait_hours": ParamSpec(type="integer", cli_flag="--capacity-wait-hours",
                                         min_value=1, max_value=12),
    },
    predecessors=["classify"],
    conflict_group="global",
    provenance_column=None,
    retry_policy=RetryPolicy(max_attempts=1),
    resource_class="light",
),
```

**Note on `cli_flag="positional"`:** The existing `build_argv()` in `orchestrator.py` doesn't handle positional arguments — it only handles `--flag value` and `--bool-flag`. This needs a small change: detect `cli_flag="positional"` and append the value directly to argv without a flag prefix.

**File:** `pipeline/orchestrator.py`

Add `"parse"` to `COMMAND_MAP` (after `"promote-classify"` entry, around line 168):

```python
"parse": {
    "cmd": ["python3", "lavandula/dashboard/manage.py", "parse_documents"],
    "params": {
        "run_tag": {"type": "text", "pattern": r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$", "flag": "positional"},
        "ntee": {"type": "text", "pattern": r"^[A-Z][A-Z0-9%]*$", "flag": "--ntee"},
        "priority": {"type": "text", "pattern": r"^[a-z_]+(,[a-z_]+)*$", "flag": "--priority"},
        "instance_type": {"type": "choice", "choices": ["g6.2xlarge", "g6.4xlarge", "g6.8xlarge"], "flag": "--instance-type"},
        "max_hours": {"type": "int", "min": 1, "max": 24, "flag": "--max-hours"},
        "batch_size": {"type": "int", "min": 10, "max": 5000, "flag": "--batch-size"},
        "no_spot": {"type": "bool", "flag": "--no-spot"},
        "max_docs": {"type": "int", "min": 1, "max": 999999, "flag": "--max-docs"},
        "retry_errors": {"type": "bool", "flag": "--retry-errors"},
        "ami_id": {"type": "text", "pattern": r"^ami-[a-f0-9]{8,17}$", "flag": "--ami-id"},
        "start_at": {"type": "text", "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", "flag": "--start-at"},
        "capacity_wait_hours": {"type": "int", "min": 1, "max": 12, "flag": "--capacity-wait-hours"},
    },
},
```

**Modify `build_argv()`** (line 228) to handle `"positional"` flag:

In the loop where parameters are appended, add before the existing `spec["flag"]` logic:

```python
if spec.get("flag") == "positional" or (isinstance(spec, dict) and spec.get("flag") == "positional"):
    validated = _validate_param(key, value, spec)
    argv.append(validated)
    continue
```

Positional args are appended as bare values without a `--flag` prefix. The parse command's `run_tag` is a positional argument.

**Add `_CONFIG_ALLOWLIST` entry** (views.py line ~121):

```python
"parse": ["run_tag", "ntee", "priority", "instance_type", "max_hours", "no_spot", "ami_id", "start_at"],
```

**Acceptance:** `validate_registry()` returns no errors. `build_argv("parse", {"run_tag": "edu-b1", "ntee": "B%", "max_hours": 12})` produces correct argv.

---

### Step 3: Orchestrator Job Functions

**File:** `pipeline/orchestrator.py`

Add `create_parse_job()` following the existing `create_crawl_job()` pattern (line ~385):

```python
def create_parse_job(config_overrides: dict, host: str) -> Job:
    """Create a parse job. Only one active parse job at a time (global)."""
    with transaction.atomic():
        # 1. Check for active Job (any status that means "not finished")
        existing = (
            Job.objects.select_for_update()
            .filter(phase="parse", status__in=["pending", "scheduled", "running"])
            .first()
        )
        if existing:
            raise DuplicateJobError(
                f"A parse run is already active (tag: {existing.config_json.get('run_tag', '?')}). "
                f"Stop it before launching a new one."
            )

        # 2. Check run_tag uniqueness against lava_parse.parse_runs
        run_tag = config_overrides.get("run_tag")
        if run_tag:
            from django.db import connections
            with connections["default"].cursor() as cur:
                cur.execute("""
                    SELECT run_tag, finished_at IS NOT NULL AS is_finished
                    FROM lava_parse.parse_runs WHERE run_tag = %s
                """, [run_tag])
                row = cur.fetchone()
                if row:
                    if row[1]:  # finished
                        raise InvalidParameterError(
                            f"Run tag '{run_tag}' already exists (completed). "
                            f"Try '{run_tag}-v2' instead."
                        )
                    else:  # still active
                        raise DuplicateJobError(
                            f"Run tag '{run_tag}' is already in progress in parse_runs. "
                            f"Stop the existing run before reusing this tag."
                        )

        status = "scheduled" if config_overrides.get("start_at") else "pending"
        try:
            return Job.objects.create(
                state_code=None,
                phase="parse",
                status=status,
                host=host,
                config_json=config_overrides,
            )
        except IntegrityError:
            raise DuplicateJobError("Duplicate parse job (constraint violation)")
```

Key differences from other `create_*_job`:
- No `state_code` — parse is global, not per-state
- No `depends_on` — parse jobs are standalone (no chaining)
- Status is `"scheduled"` if `start_at` is set, `"pending"` otherwise
- Duplicate detection uses the running job's run_tag in the error message
- **run_tag uniqueness check** against `lava_parse.parse_runs` — rejects finished tags with `-v2` suggestion, rejects active tags with stop guidance (per spec Launch Semantics #2)

**Acceptance:** `create_parse_job({"run_tag": "test"}, "cloud2")` creates a Job. Second call raises `DuplicateJobError`.

---

### Step 4: Orchestrator Adaptation — parse_documents.py

**File:** `pipeline/management/commands/parse_documents.py`

This is the largest step. The existing command needs 6 additions, all in the orchestrator (cloud2), NOT the worker (GPU instance).

#### 4a. Add new CLI arguments

In `add_arguments()` (line 46), add:

```python
parser.add_argument("--ami-id", default=None,
                    help="AMI ID to launch (default: read from SSM)")
parser.add_argument("--start-at", default=None,
                    help="ISO 8601 UTC datetime to delay launch (YYYY-MM-DDTHH:MM)")
parser.add_argument("--capacity-wait-hours", type=int, default=1,
                    help="Hours to retry when no spot capacity (1-12)")
parser.add_argument("--job-id", type=int, default=None,
                    help="Dashboard Job ID to update progress on")
```

`--job-id` is injected by the dashboard when starting the subprocess. CLI users don't need it — it's optional and defaults to None (no Job updates).

#### 4b. SIGTERM handler

At the start of `_run()` (line 165), install a signal handler:

```python
import signal

self._shutdown_requested = False

def _sigterm_handler(signum, frame):
    self._shutdown_requested = True
    self.stderr.write("SIGTERM received — shutting down gracefully\n")

signal.signal(signal.SIGTERM, _sigterm_handler)
```

Check `self._shutdown_requested` in the poll loop (line 222) alongside the existing conditions:

```python
if self._shutdown_requested:
    self.stdout.write("Shutdown requested. Terminating instance.\n")
    self._safe_terminate(ec2, instance_id)
    break
```

Also check it in `_launch_with_capacity_retry` between retry sleeps to allow interruption during capacity wait.

#### 4c. Scheduled start (`--start-at`)

At the start of `_execute_run()` (line 183), before `_launch_with_capacity_retry`:

```python
start_at_str = options.get("start_at")
if start_at_str:
    from datetime import datetime, timezone as tz
    start_at = datetime.fromisoformat(start_at_str).replace(tzinfo=tz.utc)
    wait_seconds = (start_at - datetime.now(tz.utc)).total_seconds()
    if wait_seconds > 0:
        self.stdout.write(f"Scheduled start: waiting until {start_at.isoformat()} UTC\n")
        # Sleep in small increments to allow SIGTERM interruption
        slept = 0
        while slept < wait_seconds:
            if self._shutdown_requested:
                self.stdout.write("Shutdown during scheduled wait.\n")
                return
            time.sleep(min(30, wait_seconds - slept))
            slept += 30

# Transition Job from scheduled → pending now that we're awake
self._update_job_progress(job_id, 0, 0, "pending")
```

The 30-second sleep increments ensure SIGTERM is handled promptly.

**Job lifecycle transition:** When the orchestrator wakes from a scheduled sleep, it transitions the Job from `scheduled` to `pending` via `_update_job_progress(job_id, 0, 0, "pending")`. The `pending` → `running` transition happens later when `_update_job_progress(job_id, 0, eligible_count, "running")` is called after the advisory lock is acquired and EC2 launch begins. This ensures the dashboard always reflects the correct state.

#### 4d. Configurable capacity wait

In `_launch_with_capacity_retry()` (line 315), replace the hardcoded `MAX_CAPACITY_RETRIES = 12`:

```python
capacity_wait_hours = options.get("capacity_wait_hours", 1)
max_capacity_retries = (capacity_wait_hours * 3600) // CAPACITY_RETRY_INTERVAL
```

Also add SIGTERM check between retries (inside the retry loop, before `time.sleep`):

```python
if self._shutdown_requested:
    raise CommandError("Shutdown requested during capacity wait")
```

#### 4e. AMI selection (`--ami-id`)

In `_launch_instance()` (line 374), change the AMI resolution:

```python
ami_id = options.get("ami_id")
if not ami_id:
    ssm = boto3.client("ssm", region_name="us-east-1")
    ami_resp = ssm.get_parameter(Name=SSM_AMI_PARAM)
    ami_id = ami_resp["Parameter"]["Value"]
```

If `--ami-id` is provided, skip SSM lookup. If omitted, fall back to SSM (preserving CLI compatibility).

#### 4f. Job progress updates

If `--job-id` is provided, update the Job model during the poll loop. Import Django setup only when needed:

```python
def _update_job_progress(self, job_id, succeeded, total, status=None):
    """Update dashboard Job with current progress."""
    if not job_id:
        return
    try:
        import django
        if not django.conf.settings.configured:
            return
        from pipeline.models import Job
        from django.utils import timezone as tz
        updates = {
            "progress_current": succeeded,
            "progress_total": total,
            "last_heartbeat": tz.now(),
        }
        fields = ["progress_current", "progress_total", "last_heartbeat"]
        if status:
            updates["status"] = status
            fields.append("status")
            if status == "running":
                updates["started_at"] = tz.now()
                fields.append("started_at")
            elif status in ("completed", "failed"):
                updates["finished_at"] = tz.now()
                fields.append("finished_at")
        Job.objects.filter(pk=job_id).update(**updates)
    except Exception:
        logger.exception("Failed to update Job %s progress", job_id)
```

Call points:
- After advisory lock acquired: `_update_job_progress(job_id, 0, eligible_count, "running")`
- In poll loop after reading stats_json: `_update_job_progress(job_id, succeeded, total)`
- On completion: `_update_job_progress(job_id, succeeded, total, "completed")`
- On error: `_update_job_progress(job_id, succeeded, total, "failed")`

#### 4g. Log file output

**Log handling is split between the dashboard view and the orchestrator command:**

The **dashboard** (in `ParseJobCreateView._launch()`) already creates the log file and passes it as `stdout` to `subprocess.Popen` — this is the existing pattern used by all other stages. The orchestrator subprocess inherits this file descriptor, so all `self.stdout.write()` calls automatically go to the log file. **The orchestrator command itself does NOT need to redirect `self.stdout` or open log files.**

The orchestrator's only log-related responsibility is updating `Job.log_file` if launched via CLI (without the dashboard):

```python
# Only needed for CLI launches where --job-id is set but dashboard didn't create the log
if job_id and not hasattr(self, '_dashboard_log'):
    from pipeline.orchestrator import LOG_DIR
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"parse_{run_tag}_{int(time.time())}.log"
    Job.objects.filter(pk=job_id).update(log_file=str(log_path))
```

For dashboard-launched runs, the view creates the log file and stores the path in `Job.log_file` before the subprocess starts — the orchestrator doesn't touch it.

**Acceptance:** `parse_documents test-tag --dry-run` still works. `parse_documents test-tag --start-at 2026-12-01T02:00 --capacity-wait-hours 6 --ami-id ami-0abc123456789def0 --job-id 99 --dry-run` accepts all new flags without error.

---

### Step 5: Parse Dashboard Form

**File:** `pipeline/forms.py`

Add `ParseRunForm` after `PhoneEnrichForm` (line ~279):

```python
INSTANCE_TYPE_CHOICES = [
    ("g6.2xlarge", "g6.2xlarge (1 GPU, $0.60/hr spot)"),
    ("g6.4xlarge", "g6.4xlarge (1 GPU, $1.01/hr spot)"),
    ("g6.8xlarge", "g6.8xlarge (1 GPU, $1.61/hr spot)"),
]

CLASSIFICATION_CHOICES = [
    ("annual,impact", "Annual + Impact (default)"),
    ("annual,impact,hybrid", "Annual + Impact + Hybrid"),
    ("annual", "Annual only"),
    ("impact", "Impact only"),
]


class ParseRunForm(forms.Form):
    run_tag = forms.CharField(
        max_length=64,
        widget=forms.TextInput(attrs={
            "class": _SELECT,
            "placeholder": "e.g. edu-b1, national-v2",
            "pattern": r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$",
        }),
        label="Run Tag",
    )
    ntee = forms.CharField(
        max_length=10, required=False,
        widget=forms.TextInput(attrs={
            "class": _SELECT,
            "placeholder": "e.g. B% (blank = all)",
        }),
        label="NTEE Filter",
    )
    priority = forms.ChoiceField(
        choices=CLASSIFICATION_CHOICES,
        initial="annual,impact",
        widget=forms.Select(attrs={"class": _SELECT}),
        label="Classifications",
    )
    instance_type = forms.ChoiceField(
        choices=INSTANCE_TYPE_CHOICES,
        initial="g6.2xlarge",
        widget=forms.Select(attrs={"class": _SELECT}),
        label="Instance Type",
    )
    no_spot = forms.BooleanField(
        required=False,
        label="Use on-demand (not spot)",
    )
    max_hours = forms.IntegerField(
        initial=24, min_value=1, max_value=24,
        widget=forms.NumberInput(attrs={"class": _SELECT}),
        label="Max Hours",
    )
    batch_size = forms.IntegerField(
        initial=500, min_value=10, max_value=5000,
        widget=forms.NumberInput(attrs={"class": _SELECT}),
        label="Batch Size",
    )
    max_docs = forms.IntegerField(
        required=False, min_value=1, max_value=999999,
        widget=forms.NumberInput(attrs={"class": _SELECT, "placeholder": "blank = all eligible"}),
        label="Max Documents",
    )
    retry_errors = forms.BooleanField(
        required=False,
        label="Retry previous errors",
    )
    start_at = forms.CharField(
        required=False, max_length=16,
        widget=forms.TextInput(attrs={
            "class": _SELECT,
            "type": "datetime-local",
        }),
        label="Start At (UTC)",
        help_text="Leave blank to launch immediately",
    )
    capacity_wait_hours = forms.IntegerField(
        initial=1, min_value=1, max_value=12, required=False,
        widget=forms.NumberInput(attrs={"class": _SELECT}),
        label="Capacity Wait (hours)",
        help_text="How long to retry if no spot capacity",
    )

    def clean_run_tag(self):
        tag = self.cleaned_data["run_tag"]
        if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$", tag):
            raise forms.ValidationError("Run tag must be alphanumeric with hyphens/underscores")
        return tag

    def clean_ntee(self):
        ntee = self.cleaned_data.get("ntee", "").strip()
        if ntee and not re.match(r"^[A-Z][A-Z0-9%]*$", ntee):
            raise forms.ValidationError("NTEE filter must start with a capital letter (e.g. B%, P2%)")
        return ntee or None
```

The AMI field is NOT on the form — it's populated dynamically from EC2 in the view and rendered in the template as a `<select>`. The form receives it as a hidden/select field value. Add a simple clean method:

```python
    ami_id = forms.CharField(
        required=False, max_length=25,
        widget=forms.Select(attrs={"class": _SELECT}),
        label="AMI",
    )

    def clean_ami_id(self):
        ami = self.cleaned_data.get("ami_id", "").strip()
        if ami and not re.match(r"^ami-[a-f0-9]{8,17}$", ami):
            raise forms.ValidationError("Invalid AMI ID format")
        return ami or None
```

The view sets the choices dynamically when rendering the form. On POST, validation uses `clean_ami_id`.

**Acceptance:** `ParseRunForm({"run_tag": "test", "priority": "annual,impact", "instance_type": "g6.2xlarge", "max_hours": "24", "batch_size": "500"}).is_valid()` returns True.

---

### Step 6: Parse Dashboard Views

**File:** `pipeline/views.py`

Add 5 views following existing patterns.

#### 6a. ParseView (GET — main page)

```python
class ParseView(LoginRequiredMixin, TemplateView):
    template_name = "pipeline/parse.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from .forms import ParseRunForm

        # State reconciliation: fix orphaned/stale parse jobs before rendering
        self._reconcile_parse_jobs()

        # Active run
        active_job = Job.objects.filter(
            phase="parse", status__in=["running", "scheduled", "pending"]
        ).first()
        if active_job:
            active_job = _annotate_running_jobs([active_job])[0]
        ctx["active_job"] = active_job

        # Parse run details from lava_parse (if active)
        ctx["parse_run"] = None
        ctx["instance_state"] = None
        if active_job and active_job.status == "running":
            run_tag = (active_job.config_json or {}).get("run_tag")
            if run_tag:
                ctx["parse_run"] = self._get_parse_run(run_tag)
                instance_id = ctx["parse_run"].get("instance_id") if ctx["parse_run"] else None
                if instance_id:
                    ctx["instance_state"] = self._get_cached_instance_state(instance_id)

        # Eligible counts
        ctx["eligible_counts"] = self._get_eligible_counts()

        # Form
        form = ParseRunForm()
        ami_choices = self._get_ami_choices()
        form.fields["ami_id"].widget.choices = ami_choices
        ctx["form"] = form
        ctx["has_active_run"] = active_job is not None

        # Run history (parse_runs joined with jobs)
        ctx["run_history"] = self._get_run_history()

        return ctx
```

Helper methods on ParseView (private — keep as standalone functions or view methods):

**`_get_parse_run(run_tag)`** — Read from `lava_parse.parse_runs`:
```python
def _get_parse_run(self, run_tag):
    from django.db import connections
    with connections["default"].cursor() as cur:
        cur.execute("""
            SELECT id, run_tag, started_at, finished_at, config_json,
                   stats_json, instance_id
            FROM lava_parse.parse_runs
            WHERE run_tag = %s
        """, [run_tag])
        row = cur.fetchone()
        if not row:
            return None
        columns = [col[0] for col in cur.description]
        return dict(zip(columns, row))
```

**`_get_cached_instance_state(instance_id)`** — EC2 state with 30s cache:
```python
def _get_cached_instance_state(self, instance_id):
    from django.core.cache import cache
    cache_key = f"ec2_state_{instance_id}"
    state = cache.get(cache_key)
    if state is None:
        try:
            import boto3
            ec2 = boto3.client("ec2", region_name="us-east-1")
            resp = ec2.describe_instances(InstanceIds=[instance_id])
            reservations = resp.get("Reservations", [])
            if reservations and reservations[0].get("Instances"):
                inst = reservations[0]["Instances"][0]
                state = {
                    "state": inst["State"]["Name"],
                    "az": inst.get("Placement", {}).get("AvailabilityZone", "unknown"),
                    "instance_type": inst.get("InstanceType", "unknown"),
                }
            else:
                state = {"state": "terminated", "az": "unknown", "instance_type": "unknown"}
        except Exception:
            import logging
            logging.getLogger("pipeline.parse").exception("EC2 describe_instances failed for %s", instance_id)
            state = {"state": "unavailable", "az": "unknown", "instance_type": "unknown"}
        cache.set(cache_key, state, 30)
    return state
```

**`_get_eligible_counts()`** — Breakdown by NTEE major code:
```python
def _get_eligible_counts(self):
    from django.db import connections
    with connections["default"].cursor() as cur:
        cur.execute("""
            SELECT
                LEFT(ns.ntee_code, 1) AS ntee_major,
                c.classification,
                COUNT(*) AS eligible
            FROM lava_corpus.corpus c
            JOIN lava_corpus.nonprofits_seed ns ON c.source_org_ein = ns.ein
            WHERE c.classification IN ('annual', 'impact', 'hybrid')
              AND c.content_sha256 NOT IN (
                  SELECT content_sha256 FROM lava_parse.documents
              )
            GROUP BY LEFT(ns.ntee_code, 1), c.classification
            ORDER BY COUNT(*) DESC
        """)
        rows = []
        for ntee_major, classification, count in cur.fetchall():
            rows.append({"ntee_major": ntee_major, "classification": classification, "count": count})
    return rows
```

**CRITICAL: This query uses `IN ('annual', 'impact', 'hybrid')` — NOT `!= 'not_relevant'`.** See Trap #1 in spec.

**`_get_ami_choices()`** — EC2 AMI dropdown:
```python
def _get_ami_choices(self):
    try:
        import boto3
        ec2 = boto3.client("ec2", region_name="us-east-1")
        resp = ec2.describe_images(
            Owners=["self"],
            Filters=[{"Name": "tag:Purpose", "Values": ["docling-worker"]}],
        )
        images = sorted(resp.get("Images", []), key=lambda i: i.get("CreationDate", ""), reverse=True)

        ssm = boto3.client("ssm", region_name="us-east-1")
        try:
            default_ami = ssm.get_parameter(Name="/cloud2.lavandulagroup.com/docling-ami-id")["Parameter"]["Value"]
        except Exception:
            default_ami = None

        choices = []
        for img in images:
            ami_id = img["ImageId"]
            name = img.get("Name", ami_id)
            date = img.get("CreationDate", "")[:10]
            label = f"{name} ({ami_id}) — {date}"
            if ami_id == default_ami:
                label += " [default]"
            choices.append((ami_id, label))

        if not choices:
            if default_ami:
                choices = [(default_ami, f"{default_ami} (from SSM)")]
            else:
                choices = [("", "No AMIs available")]
        return choices
    except Exception:
        import logging
        logging.getLogger("pipeline.parse").exception("Failed to load AMI list from EC2/SSM")
        return [("", "Error loading AMIs")]
```

**`_get_run_history()`** — Past runs from parse_runs + jobs:
```python
def _get_run_history(self):
    from django.db import connections
    import json as _json
    with connections["default"].cursor() as cur:
        cur.execute("""
            SELECT pr.id, pr.run_tag, pr.started_at, pr.finished_at,
                   pr.config_json, pr.stats_json, pr.instance_id
            FROM lava_parse.parse_runs pr
            ORDER BY pr.id DESC
            LIMIT 20
        """)
        columns = [col[0] for col in cur.description]
        runs = []
        for row in cur.fetchall():
            run = dict(zip(columns, row))
            stats = run.get("stats_json") or {}
            if isinstance(stats, str):
                stats = _json.loads(stats)
            run["succeeded"] = stats.get("succeeded", 0)
            run["failed"] = stats.get("failed", 0)
            run["total"] = stats.get("total", 0)
            duration_s = stats.get("duration_seconds", 0)
            run["duration_h"] = round(duration_s / 3600, 1) if duration_s else None
            config = run.get("config_json") or {}
            if isinstance(config, str):
                config = _json.loads(config)
            is_spot = not config.get("no_spot", False)
            rate = 0.60 if is_spot else 0.98
            run["cost"] = round((duration_s / 3600) * rate, 2) if duration_s else None
            run["pricing_mode"] = "spot" if is_spot else "on-demand"

            # Check if a matching Job exists
            job = Job.objects.filter(
                phase="parse", config_json__run_tag=run["run_tag"]
            ).first()
            run["job"] = job
            run["is_cli_run"] = job is None

            runs.append(run)
    return runs
```

**`_reconcile_parse_jobs()`** — Fix stale/orphaned parse jobs on page load:
```python
def _reconcile_parse_jobs(self):
    """Apply spec conflict resolution rules to fix inconsistent Job/parse_runs state."""
    from django.db import connections
    import os

    active_jobs = Job.objects.filter(
        phase="parse", status__in=["running", "pending"]
    )
    for job in active_jobs:
        run_tag = (job.config_json or {}).get("run_tag")

        # Rule 1: Job running but parse_runs.finished_at is set → mark completed
        if run_tag:
            with connections["default"].cursor() as cur:
                cur.execute("""
                    SELECT finished_at FROM lava_parse.parse_runs
                    WHERE run_tag = %s
                """, [run_tag])
                row = cur.fetchone()
                if row and row[0] is not None:
                    job.status = "completed"
                    job.finished_at = row[0]
                    job.save(update_fields=["status", "finished_at"])
                    continue

        # Rule 2: Job running but orchestrator PID is dead → mark failed, terminate orphan EC2
        if job.pid and not _pid_alive(job.pid):
            if run_tag:
                with connections["default"].cursor() as cur:
                    cur.execute("""
                        SELECT instance_id FROM lava_parse.parse_runs
                        WHERE run_tag = %s AND finished_at IS NULL
                    """, [run_tag])
                    row = cur.fetchone()
                    if row and row[0]:
                        try:
                            import boto3
                            ec2 = boto3.client("ec2", region_name="us-east-1")
                            ec2.terminate_instances(InstanceIds=[row[0]])
                        except Exception:
                            import logging
                            logging.getLogger("pipeline.parse").exception(
                                "Failed to terminate orphan EC2 instance %s during reconciliation", row[0]
                            )
                    cur.execute("""
                        UPDATE lava_parse.parse_runs SET finished_at = NOW()
                        WHERE run_tag = %s AND finished_at IS NULL
                    """, [run_tag])
            job.status = "failed"
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "finished_at"])


def _pid_alive(pid):
    """Check if a process is still running."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
```

This runs on every page load (cheap — at most 1 active Job to check). It implements the spec's conflict resolution rules:
- **Rule 1:** `Job.status=running` + `parse_runs.finished_at` set → worker finished, orchestrator died → mark Job `completed`
- **Rule 2:** `Job.status=running` + PID dead + `parse_runs.finished_at` NULL → orchestrator crashed → mark Job `failed`, terminate orphan EC2

**Rule 3** (legacy `parse_runs` without Job) and **Rule 4** (Job without `parse_runs` row yet) are handled in the template display logic, not reconciliation — they're display-only states.

#### 6b. ParseJobCreateView (POST — launch or dry-run)

```python
class ParseJobCreateView(LoginRequiredMixin, View):
    def post(self, request):
        from .forms import ParseRunForm

        action = request.POST.get("action", "launch")
        form = ParseRunForm(request.POST)

        # Re-populate AMI choices for validation
        ami_choices = ParseView._get_ami_choices(None)
        form.fields["ami_id"].widget.choices = ami_choices

        if not form.is_valid():
            messages.error(request, f"Invalid form: {form.errors.as_text()}")
            return redirect("parse")

        config = {k: v for k, v in form.cleaned_data.items() if v not in (None, "", False)}

        if action == "dry_run":
            return self._dry_run(request, config)
        return self._launch(request, config)

    def _dry_run(self, request, config):
        """Preview eligible count and cost estimate without creating anything."""
        from lavandula.parse import db as parse_db

        priority = [v.strip() for v in config.get("priority", "annual,impact").split(",")]
        ntee = config.get("ntee")

        try:
            conn = self._get_parse_conn()
            count = parse_db.get_eligible_count(conn, priority, ntee_filter=ntee)
            conn.close()
        except Exception as e:
            import logging
            logging.getLogger("pipeline.parse").exception("Eligible count query failed")
            messages.error(request, "Failed to query eligible document count. Check dashboard logs.")
            return redirect("parse")

        max_docs = config.get("max_docs")
        if max_docs:
            count = min(count, max_docs)

        est_pages = count * 30  # AVG_PAGES_PER_DOC
        est_hours = (est_pages * 0.49) / 3600  # SECONDS_PER_PAGE
        use_spot = not config.get("no_spot", False)
        rate = 0.60 if use_spot else 0.98
        est_cost = est_hours * rate

        max_hours = config.get("max_hours", 24)
        capped = est_hours > max_hours

        messages.info(
            request,
            f"Dry run: {count:,} eligible docs, ~{est_pages:,.0f} pages, "
            f"~{min(est_hours, max_hours):.1f}h GPU time, "
            f"~${min(est_cost, max_hours * rate):.0f} "
            f"({'spot' if use_spot else 'on-demand'})"
            + (" — will not complete in one run" if capped else "")
        )
        return redirect("parse")

    def _launch(self, request, config):
        """Create Job and start orchestrator subprocess."""
        from .orchestrator import create_parse_job, DuplicateJobError, InvalidParameterError

        host = _get_hostname()
        try:
            job = create_parse_job(config, host)
        except DuplicateJobError as e:
            messages.error(request, str(e))
            return redirect("parse")
        except InvalidParameterError as e:
            messages.error(request, str(e))
            return redirect("parse")

        # Start orchestrator subprocess
        from .orchestrator import build_argv, LOG_DIR
        import subprocess

        try:
            argv = build_argv("parse", config)
            argv.extend(["--job-id", str(job.pk)])

            LOG_DIR.mkdir(parents=True, exist_ok=True)
            log_path = LOG_DIR / f"parse_{config['run_tag']}_{int(time.time())}.log"

            proc = subprocess.Popen(
                argv,
                cwd=str(Path(__file__).resolve().parents[3]),
                stdout=open(str(log_path), "w"),
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

            job.pid = proc.pid
            job.log_file = str(log_path)
            job.save(update_fields=["pid", "log_file"])

            _log_audit(request, "parse_launch", "parse", {
                "job_id": job.pk, "run_tag": config["run_tag"],
                "pid": proc.pid,
            })
            messages.success(request, f"Launched parse run '{config['run_tag']}' (Job #{job.pk})")
        except Exception as e:
            import logging
            logging.getLogger("pipeline.parse").exception("Orchestrator launch failed for job %s", job.pk)
            job.status = "failed"
            job.error_message = "Orchestrator launch failed — see logs"
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error_message", "finished_at"])
            messages.error(request, "Failed to start the parse orchestrator. Check dashboard logs.")

        return redirect("parse")

    def _get_parse_conn(self):
        """Get psycopg2 connection for parse DB queries."""
        from lavandula.common.secrets import get_secret
        import boto3
        from lavandula.parse import db as parse_db

        host = get_secret("rds-endpoint")
        port = int(get_secret("rds-port"))
        database = get_secret("rds-database")
        rds_client = boto3.client("rds", region_name="us-east-1")

        def _get_token():
            return rds_client.generate_db_auth_token(
                DBHostname=host, Port=port, DBUsername="research_app", Region="us-east-1"
            )

        return parse_db.get_connection(
            host=host, port=port, database=database,
            user="research_app", iam_token_fn=_get_token,
        )
```

#### 6c. ParseProgressPartial (HTMX polling)

```python
class ParseProgressPartial(HtmxLoginRequiredMixin, View):
    def get(self, request):
        import json as _json
        from django.db import connections

        active_job = Job.objects.filter(
            phase="parse", status__in=["running", "scheduled", "pending"]
        ).first()

        if not active_job:
            return HttpResponse('<div id="parse-progress">No active run</div>')

        run_tag = (active_job.config_json or {}).get("run_tag")
        parse_run = None
        instance_state = None

        if run_tag:
            with connections["default"].cursor() as cur:
                cur.execute("""
                    SELECT stats_json, instance_id, started_at
                    FROM lava_parse.parse_runs WHERE run_tag = %s
                """, [run_tag])
                row = cur.fetchone()
                if row:
                    stats = row[0] if isinstance(row[0], dict) else _json.loads(row[0] or "{}")
                    parse_run = {
                        "stats": stats,
                        "instance_id": row[1],
                        "started_at": row[2],
                    }

            if parse_run and parse_run["instance_id"]:
                from django.core.cache import cache
                cache_key = f"ec2_state_{parse_run['instance_id']}"
                instance_state = cache.get(cache_key)
                if instance_state is None:
                    try:
                        import boto3
                        ec2 = boto3.client("ec2", region_name="us-east-1")
                        resp = ec2.describe_instances(InstanceIds=[parse_run["instance_id"]])
                        reservations = resp.get("Reservations", [])
                        if reservations and reservations[0].get("Instances"):
                            inst = reservations[0]["Instances"][0]
                            instance_state = {"state": inst["State"]["Name"],
                                              "az": inst.get("Placement", {}).get("AvailabilityZone", "")}
                        else:
                            instance_state = {"state": "terminated"}
                    except Exception:
                        instance_state = {"state": "unavailable"}
                    cache.set(cache_key, instance_state, 30)

        context = {
            "job": active_job,
            "parse_run": parse_run,
            "instance_state": instance_state,
        }
        return render(request, "pipeline/partials/parse_progress.html", context)
```

#### 6d. ParseStopView (POST — stop run)

```python
class ParseStopView(LoginRequiredMixin, View):
    def post(self, request):
        active_job = Job.objects.filter(
            phase="parse", status__in=["running", "scheduled", "pending"]
        ).first()

        if not active_job:
            messages.error(request, "No active parse run to stop")
            return redirect("parse")

        # Send SIGTERM to orchestrator
        if active_job.pid:
            from .orchestrator import _local_kill
            _local_kill(active_job.pid)

        # Terminate EC2 instance if known
        run_tag = (active_job.config_json or {}).get("run_tag")
        if run_tag:
            from django.db import connections
            with connections["default"].cursor() as cur:
                cur.execute("""
                    SELECT instance_id FROM lava_parse.parse_runs
                    WHERE run_tag = %s AND finished_at IS NULL
                """, [run_tag])
                row = cur.fetchone()
                if row and row[0]:
                    try:
                        import boto3
                        ec2 = boto3.client("ec2", region_name="us-east-1")
                        ec2.terminate_instances(InstanceIds=[row[0]])
                    except Exception:
                        import logging
                        logging.getLogger("pipeline.parse").exception(
                            "Failed to terminate EC2 instance %s during stop", row[0]
                        )

                # Mark parse_run finished
                cur.execute("""
                    UPDATE lava_parse.parse_runs
                    SET finished_at = NOW()
                    WHERE run_tag = %s AND finished_at IS NULL
                """, [run_tag])

        # Mark job cancelled
        active_job.status = "cancelled"
        active_job.finished_at = timezone.now()
        active_job.save(update_fields=["status", "finished_at"])

        _log_audit(request, "parse_stop", "parse", {
            "job_id": active_job.pk, "run_tag": run_tag,
        })
        messages.success(request, f"Stopped parse run '{run_tag}' (Job #{active_job.pk})")
        return redirect("parse")
```

#### 6e. ParseDryRunView (alternative: handle via ParseJobCreateView)

Dry-run is handled by `ParseJobCreateView._dry_run()` above (action="dry_run"). No separate view needed — the form POSTs with an `action` field.

**Acceptance:** All 4 views load without error. Launch creates a Job + subprocess. Stop terminates and marks cancelled. Progress returns HTML partial. Dry-run shows estimates.

---

### Step 7: Template

**File:** `pipeline/templates/pipeline/parse.html`

Create the template following existing dashboard patterns. Key sections:

1. **Active Run Panel** — shown only when `active_job` is set. HTMX-polled progress partial (`hx-get="/parse/progress/" hx-trigger="every 10s"`). Shows run tag, progress bar, docs/min rate, ETA, elapsed, cost estimate. Instance badge (green/yellow/red). Stop button with confirmation.

2. **Eligible Documents Panel** — always shown. Table of NTEE major codes with eligible counts. Summarize totals by classification.

3. **Launch Form** — disabled when `has_active_run` is True (show message: "A parse run is already active"). All form fields from `ParseRunForm`. Three buttons: "Dry Run" (action=dry_run), "Launch Now" (action=launch), "Schedule" (action=launch, only when start_at is set).

4. **Run History Table** — newest first. Columns: Tag, Started, Duration, Docs (succeeded/failed/total), Rate, Cost, Status badge, "CLI run" badge for legacy runs.

**Partial file:** `pipeline/templates/pipeline/partials/parse_progress.html`

Renders the live progress content swapped in by HTMX. Contains:
- Progress bar (succeeded/total)
- Stats: succeeded, failed, rate (docs/min), ETA, elapsed
- Instance badge with state/AZ
- Cost so far
- Scheduled wait countdown (if status=scheduled)
- Capacity retry status (if waiting)

**Template patterns to follow:** Look at `crawler.html` and `classifier.html` for the layout structure. Use Bootstrap cards, HTMX attributes, and the existing CSS classes.

**Acceptance:** `/parse/` renders with eligible counts, form, and empty history. Form disables when a run is active.

---

### Step 8: URL Configuration

**File:** `pipeline/urls.py`

Add after the phone enrichment block (line ~39):

```python
# Parse (Docling GPU)
path("parse/", views.ParseView.as_view(), name="parse"),
path("parse/queue/", views.ParseJobCreateView.as_view(), name="parse_job_create"),
path("parse/progress/", views.ParseProgressPartial.as_view(), name="parse_progress"),
path("parse/stop/", views.ParseStopView.as_view(), name="parse_stop"),
```

Also add `"parse"` import for `create_parse_job` in views.py imports.

**Acceptance:** `python3 lavandula/dashboard/manage.py show_urls` (if available) or manual URL resolution confirms all 4 routes are registered.

---

### Step 9: Dashboard Navigation

Add a "Parse" link to the pipeline dashboard sidebar/nav. Find the navigation template (likely `pipeline/base.html` or `dashboard.html`) and add:

```html
<a href="{% url 'parse' %}" class="...">Docling Parse</a>
```

Place it after "Classifier" and before "990 Index" in the nav order, matching the pipeline stage progression: seed → resolve → crawl → classify → **parse** → 990.

---

### Step 10: Tests

#### Unit Tests

1. **`test_eligible_count_query`** — Mock DB, verify `get_eligible_count` with `['annual', 'impact']` excludes already-parsed docs. Specifically test the `NOT IN` subquery.

2. **`test_cost_estimation`** — Test formulas with edge cases: 0 docs (cost = $0), 1 doc, max_docs cap, max_hours cap, spot vs on-demand.

3. **`test_run_tag_validation`** — Test `ParseRunForm.clean_run_tag()` rejects: empty, spaces, special chars, starts with hyphen. Accepts: `edu-b1`, `national_v2`, `test123`.

4. **`test_build_argv_parse`** — Test `build_argv("parse", {"run_tag": "test", "ntee": "B%", "max_hours": 12})` produces `["python3", "lavandula/dashboard/manage.py", "parse_documents", "test", "--ntee", "B%", "--max-hours", "12"]`.

5. **`test_create_parse_job_duplicate`** — Create a running parse job, attempt second creation, verify `DuplicateJobError` with run_tag in message.

6. **`test_stage_registry_parse`** — Verify `STAGE_REGISTRY["parse"]` exists, has correct conflict_group, resource_class, and all expected parameters.

#### Integration Tests

7. **`test_parse_page_loads`** — GET `/parse/` returns 200 with eligible counts.

8. **`test_dry_run`** — POST to `/parse/queue/` with `action=dry_run`, verify redirect and info message with estimates. No Job created.

9. **`test_launch_creates_job`** — POST to `/parse/queue/` with valid config, verify Job created with `phase="parse"`, subprocess started. (Mock EC2/SSM.)

10. **`test_double_launch_rejected`** — With a running parse Job, POST launch returns error, no second Job created.

11. **`test_stop_cancels_job`** — With a running parse Job, POST `/parse/stop/` marks Job cancelled and calls terminate_instances (mocked).

12. **`test_progress_partial`** — GET `/parse/progress/` with a running Job returns HTML with stats from mocked parse_runs.

13. **`test_scheduled_launch`** — POST with `start_at` in the future creates Job with `status="scheduled"`.

14. **`test_stop_during_scheduled`** — Scheduled Job cancelled cleanly, no orphans.

#### State Reconciliation Tests

15. **`test_reconcile_stale_job_finished_run`** — Create a Job with `status="running"` and a matching `parse_runs` row with `finished_at` set. Load `/parse/`. Verify Job is now `status="completed"` with `finished_at` from `parse_runs`.

16. **`test_reconcile_dead_pid_orphan_ec2`** — Create a Job with `status="running"`, a dead PID (use a known-dead PID), and a `parse_runs` row with `finished_at IS NULL` and an `instance_id`. Load `/parse/`. Verify: Job is `status="failed"`, `terminate_instances` was called (mocked) with the instance_id, and `parse_runs.finished_at` is set.

17. **`test_run_tag_uniqueness_finished`** — Create a finished `parse_runs` row with tag `edu-b1`. POST launch with `run_tag=edu-b1`. Verify error message suggests `edu-b1-v2`. No Job created.

18. **`test_run_tag_uniqueness_active`** — Create an active `parse_runs` row (no `finished_at`) with tag `edu-b1`. POST launch with `run_tag=edu-b1`. Verify error message says tag is in progress. No Job created.

19. **`test_legacy_cli_runs_display`** — Insert `parse_runs` rows with no matching Job. Load `/parse/`. Verify run history shows these rows with "CLI run" badge and no log file link.

---

## File Change Summary

| File | Change |
|------|--------|
| `pipeline/models.py` | Add `("parse", "Parse")` to `PHASE_CHOICES` |
| `pipeline/stages.py` | Add `"parse"` entry to `STAGE_REGISTRY` |
| `pipeline/orchestrator.py` | Add `"parse"` to `COMMAND_MAP`, add `create_parse_job()`, modify `build_argv()` for positional args |
| `pipeline/management/commands/parse_documents.py` | Add `--ami-id`, `--start-at`, `--capacity-wait-hours`, `--job-id` flags; SIGTERM handler; Job progress updates; log file output |
| `pipeline/forms.py` | Add `ParseRunForm` |
| `pipeline/views.py` | Add `ParseView`, `ParseJobCreateView`, `ParseProgressPartial`, `ParseStopView` |
| `pipeline/templates/pipeline/parse.html` | New template (main page) |
| `pipeline/templates/pipeline/partials/parse_progress.html` | New template (HTMX partial) |
| `pipeline/urls.py` | Add 4 URL patterns |
| `pipeline/templates/pipeline/base.html` (or nav include) | Add "Parse" nav link |
| Migration file | Auto-generated (PHASE_CHOICES change) |

## Implementation Order

Steps must be done in order 1→10. Each step builds on the previous:
1. Migration (enables `phase="parse"` in DB)
2. Stage registration (STAGE_REGISTRY + COMMAND_MAP + build_argv positional support)
3. Orchestrator job function (create_parse_job)
4. Orchestrator adaptation (the big step — CLI flags, SIGTERM, scheduling, progress)
5. Form (Django form for the template)
6. Views (all 4 views)
7. Template (HTML)
8. URLs (wire it up)
9. Navigation (add to sidebar)
10. Tests

Steps 5-9 can be done in any order as a group, but all depend on steps 1-4 being complete.

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| `build_argv` positional support breaks existing phases | All existing phases use `--flag` style. Positional logic only triggers when `flag="positional"`. Add a unit test. |
| EC2 API calls slow down page load | 30-second cache on instance state. AMI list fetched on page load but could be cached too (1-minute TTL). |
| Orchestrator SIGTERM during DB write | SIGTERM sets a flag, checked at safe points (between poll iterations, between capacity retries). Never interrupts mid-write. |
| Job progress gets stale if orchestrator crashes | Existing stale-job detector in the dashboard marks stale jobs as failed. The parse conflict resolution rules in the spec handle orphan states. |
| Large eligible count query is slow | The `NOT IN` subquery joins corpus (186K) with documents (~11K parsed). On RDS db.t4g.small this may take a few seconds. Acceptable for page load; future optimization could add an index or materialized view. |

## Security Hardening (Red Team Findings)

### HIGH-1: No raw exception text in user-facing messages or Job.error_message

**Rule:** Never pass `str(e)` directly to `messages.error()` or store it in `Job.error_message`. Raw exceptions can leak AWS credentials, DB connection strings, internal paths, or application logic.

**Pattern to follow everywhere:**

```python
# BAD — leaks internal details
except Exception as e:
    messages.error(request, f"Failed to start orchestrator: {e}")
    job.error_message = str(e)

# GOOD — generic user message, detailed internal log
import logging
logger = logging.getLogger("pipeline.parse")

except Exception as e:
    logger.exception("Failed to start parse orchestrator for job %s", job.pk)
    messages.error(request, "Failed to start the parse orchestrator. Check the dashboard logs.")
    job.error_message = "Orchestrator launch failed — see logs"
```

**Apply to:** `ParseJobCreateView._launch()`, `ParseJobCreateView._dry_run()`, `_update_job_progress()`, `_reconcile_parse_jobs()`.

### HIGH-2: No silent `except Exception: pass` on critical operations

**Rule:** Every `except Exception: pass` that swallows a failure involving EC2 termination, Job updates, or DB writes must log the exception. Silent failures create orphaned EC2 instances (cost + attack surface) and invisible state corruption.

**Pattern to follow:**

```python
# BAD — orphaned instance goes unnoticed
except Exception:
    pass

# GOOD — failure is logged, operation degrades gracefully
except Exception:
    logger.exception("Failed to terminate EC2 instance %s during stop", instance_id)
```

**Apply to:**
- `_update_job_progress()` — log but don't crash (existing "never crash the orchestrator" rule is correct, but must log)
- `ParseStopView` — EC2 terminate_instances call
- `_reconcile_parse_jobs()` — EC2 terminate_instances call
- `_get_cached_instance_state()` — EC2 describe_instances call
- `_get_ami_choices()` — EC2 describe_images + SSM call

All of these should use `logger.exception(...)` instead of bare `pass`.

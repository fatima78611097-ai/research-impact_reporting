# Plan 0033: Multi-Host Job Distribution & Remote Workers

**Spec**: `locard/specs/0033-multi-host-workers.md`

## Overview

Implement the multi-host worker system in 7 steps:
1. Worker model + migration
2. Orchestrator heartbeat + stale detection
3. Phase conflict relaxation + host validation
4. Dashboard visibility (worker panel, host column in job tables)
5. Host targeting (host dropdown on all queue forms, remote log message)
6. Worker management page + `setup_worker` CLI
7. Testing

## Step 1: Worker Model & Migration

**Files**: `models.py`

Add `Worker` model after existing managed models:

```python
class Worker(models.Model):
    STATUS_CHOICES = [
        ("online", "Online"),
        ("offline", "Offline"),
        ("stale", "Stale"),
    ]
    hostname = models.CharField(max_length=100, unique=True)
    display_name = models.CharField(max_length=100, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="offline")
    capabilities = models.JSONField(default=dict, blank=True)
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    registered_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "workers"

    def __str__(self):
        name = self.display_name or self.hostname
        return f"{name} ({self.status})"
```

Generate migration: `python3 manage.py makemigrations pipeline`

**ACs covered**: 1, 4, 5

## Step 2: Orchestrator Heartbeat & Stale Detection

**Files**: `run_orchestrator.py`

### 2a. Heartbeat writes

In `handle()`, after the existing `self._recover_orphaned_jobs()` call, add worker auto-registration:

```python
from pipeline.models import Worker

# Auto-register this host as a worker
Worker.objects.update_or_create(
    hostname=self.hostname,
    defaults={
        "status": "online",
        "last_heartbeat": timezone.now(),
        "ip_address": self._get_local_ip(),
        "is_active": True,
    },
)
```

In the main `while not self._shutdown` loop, after `_start_eligible_jobs()`, add:

```python
self._update_heartbeat()
```

Where `_update_heartbeat` does:

```python
def _update_heartbeat(self):
    try:
        Worker.objects.filter(hostname=self.hostname).update(
            status="online",
            last_heartbeat=timezone.now(),
        )
    except Exception:
        pass  # Heartbeat failure is non-fatal
```

Use `filter().update()` instead of `update_or_create` in the loop — cheaper, no race, and the row was already created on startup.

Add `_get_local_ip()`:

```python
def _get_local_ip(self):
    try:
        import socket as _socket
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None
```

### 2b. Graceful shutdown

In `_signal_handler`, add worker offline update with a short timeout:

```python
def _signal_handler(self, signum, frame):
    self._shutdown = True
    try:
        Worker.objects.filter(hostname=self.hostname).update(status="offline")
    except Exception:
        pass
```

### 2c. Stale detection

Add a `_check_worker_health()` method called from the main loop. The spec says "dashboard-host orchestrator" runs this, but every orchestrator can safely run it — the two UPDATE queries are idempotent and cheap. Multiple hosts checking simultaneously just means the stale transition happens on whichever host's loop fires first. **This is an intentional deviation from the spec for simplicity** — no need for a `DASHBOARD_HOST` env var or per-host flag.

```python
HEARTBEAT_STALE_THRESHOLD = 300    # 5 min → stale
HEARTBEAT_OFFLINE_THRESHOLD = 1800  # 30 min → offline

def _check_worker_health(self):
    now = timezone.now()
    stale_cutoff = now - timedelta(seconds=HEARTBEAT_STALE_THRESHOLD)
    offline_cutoff = now - timedelta(seconds=HEARTBEAT_OFFLINE_THRESHOLD)

    # Stale workers → offline (30 min without heartbeat)
    Worker.objects.filter(
        is_active=True,
        status="stale",
        last_heartbeat__lt=offline_cutoff,
    ).update(status="offline")

    # Online workers → stale (5 min without heartbeat)
    Worker.objects.filter(
        is_active=True,
        status="online",
        last_heartbeat__lt=stale_cutoff,
    ).update(status="stale")
```

Order matters: check offline first (stale→offline), then check stale (online→stale). This prevents a worker from jumping online→stale→offline in a single cycle.

Call `_check_worker_health()` in the main loop after `_update_heartbeat()`.

**ACs covered**: 6, 7, 8, 9, 10, 11, 12

## Step 3: Phase Conflict Relaxation

**Files**: `orchestrator.py`, `run_orchestrator.py`

### 3a. Update `check_phase_conflict` in `orchestrator.py`

Change signature and logic:

```python
_PER_STATE_PHASES = {"resolve", "classify", "enrich-phone", "seed"}

def check_phase_conflict(phase: str, state_code: str | None = None) -> bool:
    """Return True if a conflicting job or ad-hoc process is already running.

    For per-state phases (resolve, classify, enrich-phone, seed), only
    conflicts with the same state block. NULL state_code → global conflict.
    Ad-hoc PipelineProcess checks remain global (no state_code on that model).
    """
    qs = Job.objects.filter(phase=phase, status="running")
    if state_code and phase in _PER_STATE_PHASES:
        qs = qs.filter(state_code=state_code)
    if qs.exists():
        return True
    if PipelineProcess.objects.filter(name=phase, status="running").exists():
        return True
    return False
```

**990 advisory lock**: The 990 conflict check uses `pg_advisory_xact_lock` inside `create_990_index_job` and `create_990_parse_job` (not in `check_phase_conflict`). This is unchanged — the advisory lock prevents duplicate 990 job creation, while `check_phase_conflict` prevents the orchestrator from starting two 990 jobs simultaneously. Both remain global.

### 3b. Update caller in `run_orchestrator.py`

In `_start_eligible_jobs`:

```python
def _start_eligible_jobs(self):
    eligible = get_eligible_jobs(self.hostname)
    for job in eligible[:3]:
        if check_phase_conflict(job.phase, job.state_code):
            continue
        self._launch_job(job)
```

### 3c. Validate host in `create_*_job` functions

Add a shared helper in `orchestrator.py`:

```python
def _validate_host(host: str) -> str:
    """Validate that host matches an active, online worker. Returns hostname.

    Rejects offline AND stale hosts — matches the UI dropdown behavior
    where both are disabled.
    """
    from .models import Worker
    try:
        worker = Worker.objects.get(hostname=host, is_active=True)
    except Worker.DoesNotExist:
        raise InvalidParameterError(f"Unknown or inactive host: {host!r}")
    if worker.status in ("offline", "stale"):
        raise InvalidParameterError(f"Host {host!r} is {worker.status}")
    return host
```

Call `_validate_host(host)` at the top of each `create_*_job` function, with a narrow fallback for fresh installs:

```python
def create_resolve_job(config_overrides: dict, host: str) -> Job:
    if Worker.objects.filter(is_active=True).exists():
        _validate_host(host)
    # ... rest unchanged
```

The fallback only activates when there are zero active workers — the true fresh-install case (before any orchestrator has started). Once any worker is registered, all hosts must pass validation.

**ACs covered**: 25, 26, 27, 28, 29, 41, 42

## Step 4: Dashboard Visibility (Worker Panel + Host Column)

### 4a. Worker panel in dashboard stats

**Files**: `views.py` (`_dashboard_stats`), `dashboard_stats.html`

In `_dashboard_stats()`, add worker context:

```python
from .models import Worker

workers = list(
    Worker.objects.filter(is_active=True)
    .values("hostname", "display_name", "status", "last_heartbeat", "capabilities")
)
for w in workers:
    w["active_jobs"] = Job.objects.filter(
        host=w["hostname"], status__in=["running", "pending"]
    ).count()
    w["phases"] = (w["capabilities"] or {}).get("phases", ["all"])
    w["last_heartbeat_display"] = _elapsed_since(w["last_heartbeat"]) if w["last_heartbeat"] else "never"

ctx["workers"] = workers
```

In `dashboard_stats.html`, add before the "National Ingest Progress" heading:

```html
{% if workers %}
<div class="mb-6">
  <h2 class="text-lg font-semibold text-gray-900 mb-3">Workers</h2>
  <div class="overflow-x-auto">
    <table class="min-w-full text-sm">
      <thead class="bg-gray-100">
        <tr>
          <th class="px-3 py-2 text-left">Host</th>
          <th class="px-3 py-2 text-center">Status</th>
          <th class="px-3 py-2 text-right">Last Seen</th>
          <th class="px-3 py-2 text-right">Active</th>
          <th class="px-3 py-2 text-left">Phases</th>
        </tr>
      </thead>
      <tbody>
        {% for w in workers %}
        <tr class="border-b">
          <td class="px-3 py-2 font-medium">
            {% if w.display_name %}{{ w.display_name }} <span class="text-gray-400 text-xs">({{ w.hostname }})</span>
            {% else %}{{ w.hostname }}{% endif %}
          </td>
          <td class="px-3 py-2 text-center">
            {% if w.status == "online" %}<span class="inline-block w-2.5 h-2.5 rounded-full bg-green-500" title="Online"></span>
            {% elif w.status == "stale" %}<span class="inline-block w-2.5 h-2.5 rounded-full bg-yellow-500" title="Stale"></span>
            {% else %}<span class="inline-block w-2.5 h-2.5 rounded-full bg-red-500" title="Offline"></span>{% endif %}
          </td>
          <td class="px-3 py-2 text-right text-gray-500">{{ w.last_heartbeat_display }}</td>
          <td class="px-3 py-2 text-right">{{ w.active_jobs }} job{{ w.active_jobs|pluralize }}</td>
          <td class="px-3 py-2 text-gray-500">{{ w.phases|join:", " }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% endif %}
```

### 4b. Host column in job tables

**Files**: `_recent_jobs_table.html`, `views.py` (dashboard stats context, `JobListView`)

In `_recent_jobs_table.html`, add a Host column controlled by `show_host`:

```html
<!-- In thead -->
{% if show_host %}<th class="pb-2">Host</th>{% endif %}

<!-- In tbody row -->
{% if show_host %}
<td class="py-2 text-gray-500 text-xs">
  {% if job.host_display_name %}{{ job.host_display_name }}{% else %}{{ job.host|truncatechars:20 }}{% endif %}
</td>
{% endif %}
```

To resolve `host_display_name`, annotate recent_jobs in `_dashboard_stats()` and `JobListView`:

```python
# After fetching recent_jobs, annotate with worker display names
worker_names = dict(Worker.objects.filter(is_active=True).values_list("hostname", "display_name"))
for job in recent_jobs:
    job.host_display_name = worker_names.get(job.host, "")
```

Dashboard stats context already has `show_phase: True`. Add `show_host: True` alongside it.
Also add `show_host: True` to `JobListView.get_context_data`.
Phase pages do NOT show the host column.

**ACs covered**: 13, 14, 18, 40

## Step 5: Host Targeting (Host Dropdown + Remote Log)

### 5a. Host dropdown on job queue forms

**Files**: `views.py` (all `*JobCreateView` classes + phase view `get_context_data`), all queue form templates

Add a shared helper in `views.py`:

```python
def _get_worker_choices():
    """Return list of worker choices for the host dropdown."""
    workers = Worker.objects.filter(is_active=True).order_by("hostname")
    current = socket.gethostname()
    choices = []
    for w in workers:
        label = w.display_name or w.hostname
        if w.hostname == current:
            label += " (this host)"
        elif w.status == "online":
            label += " (online)"
        else:
            label += f" ({w.status})"
        choices.append({
            "hostname": w.hostname,
            "label": label,
            "disabled": w.status in ("offline", "stale"),
            "selected": w.hostname == current,
        })
    return choices
```

Each phase view's `get_context_data` adds:

```python
workers = _get_worker_choices()
ctx["worker_choices"] = workers
ctx["show_host_selector"] = len(workers) > 1
```

Each queue template adds (inside the existing `<form>`):

```html
{% if show_host_selector %}
<div class="mb-4">
  <label class="block text-sm font-medium text-gray-700 mb-1">Host</label>
  <select name="host" class="w-full border border-gray-300 rounded px-3 py-2">
    {% for w in worker_choices %}
    <option value="{{ w.hostname }}" {% if w.selected %}selected{% endif %} {% if w.disabled %}disabled{% endif %}>{{ w.label }}</option>
    {% endfor %}
  </select>
</div>
{% endif %}
```

Each `*JobCreateView.post()` method reads host and catches validation errors:

```python
host = request.POST.get("host", _get_hostname())
try:
    job = create_resolve_job(config, host)
    _log_audit(request, "job_create", "resolve", {"job_id": job.pk, "host": host})
except InvalidParameterError as exc:
    messages.error(request, str(exc))
    return redirect("resolver")
except DuplicateJobError as exc:
    messages.error(request, str(exc))
    return redirect("resolver")
```

The existing views already catch `DuplicateJobError` — extend the same pattern to also catch `InvalidParameterError` (which `_validate_host` raises). This ensures the user sees a form error message, not a 500.

Add `host` to **all** audit log entries across all create views:

```python
_log_audit(request, "job_create", "resolve", {"job_id": job.pk, "host": host})
```

**Templates to update** (add host dropdown):
- `seeder.html`
- `resolver.html`
- `crawler.html`
- `classifier.html`
- `phone_enrich.html`
- `990_index.html`
- `990_parse.html`

### 5b. Remote log message

**Files**: `views.py` (`JobLogPartial`)

In `JobLogPartial.get`:

```python
def get(self, request, pk):
    job = get_object_or_404(Job, pk=pk)
    current_host = socket.gethostname()

    if job.host and job.host != current_host:
        if not job.log_file or not Path(job.log_file).exists():
            return HttpResponse(
                f'<pre class="text-xs bg-gray-900 text-yellow-400 p-4 rounded">'
                f'Log file is on remote host: {_escape(job.host)}\n'
                f'Path: {_escape(job.log_file or "unknown")}</pre>'
            )

    content = read_log_tail(job.log_file)
    return HttpResponse(
        f'<pre class="text-xs bg-gray-900 text-green-400 p-4 rounded overflow-auto max-h-96">{_escape(content)}</pre>'
    )
```

**ACs covered**: 15, 16, 17, 22, 23, 24, 30, 39

## Step 6: Worker Management Page & Setup CLI

### 6a. Worker management page

**Files**: `views.py`, `urls.py`, `workers.html` (new), `base.html`

Add `WorkerListView`:

```python
class WorkerListView(LoginRequiredMixin, TemplateView):
    template_name = "pipeline/workers.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        workers = Worker.objects.all().order_by("-is_active", "hostname")
        for w in workers:
            w.active_jobs = Job.objects.filter(
                host=w.hostname, status__in=["running", "pending"]
            ).count()
            w.recent_jobs = Job.objects.filter(
                host=w.hostname
            ).order_by("-created_at")[:10]
        ctx["workers"] = workers
        return ctx
```

Add `WorkerEditView` (POST only — edit display_name, capabilities, toggle is_active):

```python
class WorkerEditView(LoginRequiredMixin, View):
    def post(self, request, pk):
        worker = get_object_or_404(Worker, pk=pk)
        action = request.POST.get("action")
        if action == "deactivate":
            worker.is_active = False
            worker.save(update_fields=["is_active"])
            _log_audit(request, "worker_deactivate", worker.hostname)
            messages.success(request, f"Worker {worker.hostname} deactivated")
        elif action == "reactivate":
            worker.is_active = True
            worker.save(update_fields=["is_active"])
            _log_audit(request, "worker_reactivate", worker.hostname)
            messages.success(request, f"Worker {worker.hostname} reactivated")
        elif action == "edit":
            worker.display_name = request.POST.get("display_name", "").strip()[:100]
            phases = request.POST.get("phases", "").strip()[:200]
            caps = worker.capabilities or {}
            if phases:
                phase_tokens = [p.strip()[:30] for p in phases.split(",")[:20]]
                caps["phases"] = phase_tokens
            else:
                caps.pop("phases", None)
            notes = request.POST.get("notes", "").strip()[:500]
            if notes:
                caps["notes"] = notes
            else:
                caps.pop("notes", None)
            worker.capabilities = caps
            worker.save(update_fields=["display_name", "capabilities"])
            _log_audit(request, "worker_edit", worker.hostname, {"display_name": worker.display_name})
            messages.success(request, f"Worker {worker.hostname} updated")
        return redirect("worker_list")
```

Add URL routes:

```python
path("workers/", views.WorkerListView.as_view(), name="worker_list"),
path("workers/<int:pk>/edit/", views.WorkerEditView.as_view(), name="worker_edit"),
```

Add sidebar link in `base.html`, under Operations:

```html
<li><a href="{% url 'worker_list' %}" class="block px-4 py-2 hover:bg-gray-800 hover:text-white {% if 'worker' in request.resolver_match.url_name %}bg-gray-800 text-white{% endif %}">Workers</a></li>
```

Create `workers.html` template showing:
- Table of all workers (active + deactivated with visual distinction)
- Inline edit for display_name, phases, notes
- Deactivate/reactivate button
- Recent 10 jobs per worker (collapsible)
- "Workers register themselves via `setup_worker` CLI" info text (no create form)

### 6b. `setup_worker` management command

**Files**: `management/commands/setup_worker.py` (new)

```python
import getpass
import socket
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import connections

from pipeline.models import Worker

PROJECT_ROOT = Path(__file__).resolve().parents[5]


class Command(BaseCommand):
    help = "Verify host connectivity and register as a pipeline worker"

    def add_arguments(self, parser):
        parser.add_argument("--display-name", default="")
        parser.add_argument("--phases", default="", help="Comma-separated phase list")
        parser.add_argument("--gpu", action="store_true")
        parser.add_argument("--skip-s3", action="store_true")

    def handle(self, *args, **options):
        user = getpass.getuser()
        if user == "root":
            self.stderr.write("ERROR: Do not run as root. Use the application user.")
            return

        hostname = socket.gethostname()
        self.stdout.write(f"Host: {hostname}")
        self.stdout.write(f"User: {user}")

        # Verify RDS
        for alias in ("default", "pipeline"):
            try:
                conn = connections[alias]
                conn.ensure_connection()
                self.stdout.write(self.style.SUCCESS(f"✓ RDS ({alias}): connected"))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"✗ RDS ({alias}): {e}"))
                return

        # Verify S3
        if not options["skip_s3"]:
            try:
                import boto3
                boto3.client("s3").head_bucket(Bucket="lavandula-nonprofit-collaterals")
                self.stdout.write(self.style.SUCCESS("✓ S3: accessible"))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"✗ S3: {e}"))
                return
        else:
            self.stdout.write("⊘ S3: skipped")

        # Register worker
        caps = {}
        if options["phases"]:
            caps["phases"] = [p.strip() for p in options["phases"].split(",")]
        if options["gpu"]:
            caps["gpu"] = True

        worker, created = Worker.objects.update_or_create(
            hostname=hostname,
            defaults={
                "display_name": options["display_name"],
                "capabilities": caps,
                "status": "offline",  # Will go online when orchestrator starts
                "is_active": True,
            },
        )
        action = "registered" if created else "re-registered"
        name = worker.display_name or hostname
        self.stdout.write(self.style.SUCCESS(f"✓ Worker {action}: {name}"))

        # Print systemd unit
        cwd = PROJECT_ROOT
        self.stdout.write("")
        self.stdout.write("Systemd service file (copy to /etc/systemd/system/lavandula-orchestrator.service):")
        self.stdout.write("─" * 60)
        self.stdout.write(f"""[Unit]
Description=Lavandula Pipeline Orchestrator
After=network.target

[Service]
Type=simple
User={user}
WorkingDirectory={cwd}
ExecStart=/usr/bin/python3 {cwd}/lavandula/dashboard/manage.py run_orchestrator
Restart=on-failure
RestartSec=10
Environment=PYTHONPATH={cwd}

[Install]
WantedBy=multi-user.target""")
        self.stdout.write("─" * 60)
        self.stdout.write("")
        self.stdout.write("Next steps:")
        self.stdout.write("  1. Copy the above to /etc/systemd/system/lavandula-orchestrator.service")
        self.stdout.write("  2. sudo systemctl daemon-reload")
        self.stdout.write("  3. sudo systemctl enable --now lavandula-orchestrator")
```

**ACs covered**: 2, 3, 19, 20, 21, 31, 32, 33, 34, 35, 36

## Step 7: Testing

### Unit tests

Add to existing test suite or new `tests/test_workers.py`:

```python
# --- Phase conflict tests ---

def test_conflict_same_state():
    Job.objects.create(phase="resolve", state_code="CA", status="running", host="h1")
    assert check_phase_conflict("resolve", "CA") is True

def test_conflict_different_state():
    Job.objects.create(phase="resolve", state_code="CA", status="running", host="h1")
    assert check_phase_conflict("resolve", "NY") is False

def test_conflict_null_state_global():
    Job.objects.create(phase="resolve", state_code="CA", status="running", host="h1")
    assert check_phase_conflict("resolve", None) is True

def test_conflict_crawl_global():
    Job.objects.create(phase="crawl", state_code=None, status="running", host="h1")
    assert check_phase_conflict("crawl", None) is True

def test_conflict_crawl_no_running():
    assert check_phase_conflict("crawl", None) is False

# --- Worker model tests ---

def test_worker_update_or_create():
    Worker.objects.create(hostname="h1", status="offline")
    Worker.objects.update_or_create(hostname="h1", defaults={"status": "online"})
    assert Worker.objects.filter(hostname="h1").count() == 1
    assert Worker.objects.get(hostname="h1").status == "online"

def test_worker_soft_delete():
    w = Worker.objects.create(hostname="h1", is_active=True)
    w.is_active = False
    w.save()
    assert Worker.objects.filter(is_active=True).count() == 0
    assert Worker.objects.filter(hostname="h1").exists()

# --- Worker health transition tests ---

def test_stale_detection():
    """Online worker with old heartbeat → stale"""
    Worker.objects.create(hostname="h1", status="online", is_active=True,
        last_heartbeat=timezone.now() - timedelta(seconds=400))
    _check_worker_health()  # import from run_orchestrator
    assert Worker.objects.get(hostname="h1").status == "stale"

def test_offline_detection():
    """Stale worker with very old heartbeat → offline"""
    Worker.objects.create(hostname="h1", status="stale", is_active=True,
        last_heartbeat=timezone.now() - timedelta(seconds=2000))
    _check_worker_health()
    assert Worker.objects.get(hostname="h1").status == "offline"

def test_stale_recovery():
    """Stale worker that heartbeats → online"""
    Worker.objects.create(hostname="h1", status="stale", is_active=True,
        last_heartbeat=timezone.now() - timedelta(seconds=400))
    # Simulate heartbeat resuming
    Worker.objects.filter(hostname="h1").update(
        status="online", last_heartbeat=timezone.now())
    assert Worker.objects.get(hostname="h1").status == "online"

# --- Host validation tests ---

def test_validate_host_unknown():
    Worker.objects.create(hostname="h1", status="online", is_active=True)
    with pytest.raises(InvalidParameterError):
        _validate_host("unknown-host")

def test_validate_host_offline():
    Worker.objects.create(hostname="h1", status="offline", is_active=True)
    with pytest.raises(InvalidParameterError):
        _validate_host("h1")

def test_validate_host_online():
    Worker.objects.create(hostname="h1", status="online", is_active=True)
    assert _validate_host("h1") == "h1"

def test_validate_host_skipped_when_no_workers():
    """When workers table is empty, validation is skipped (fresh install)"""
    # create_resolve_job should not raise even with unknown host
    # when no workers exist
    assert Worker.objects.count() == 0
    # This is tested at the create_*_job level, not _validate_host directly
```

Note: These tests need a test DB. The Job model uses `default` (lava_dashboard) which works with Django's test runner. For the Worker model, same approach — it's a managed model on the default DB. Extract `_check_worker_health` logic into a testable function (not buried in the Command class).

**ACs covered**: 37, 38

## File Summary

| File | Step | Change |
|------|------|--------|
| `models.py` | 1 | Add `Worker` model |
| `migrations/XXXX_worker.py` | 1 | Auto-generated migration |
| `run_orchestrator.py` | 2 | Heartbeat writes, shutdown offline, stale detection, `_get_local_ip()` |
| `orchestrator.py` | 3 | `check_phase_conflict` per-state, `_validate_host` helper |
| `views.py` | 4, 5 | `_get_worker_choices()`, worker context in dashboard stats, host param in all create views, `WorkerListView`, `WorkerEditView`, `JobLogPartial` remote message, audit log host field |
| `urls.py` | 5 | `/workers/`, `/workers/<pk>/edit/` |
| `dashboard_stats.html` | 4 | Workers panel |
| `_recent_jobs_table.html` | 4 | Optional host column |
| `base.html` | 5 | Workers sidebar link |
| `workers.html` (new) | 5 | Worker management page |
| `seeder.html` | 4 | Host dropdown |
| `resolver.html` | 4 | Host dropdown |
| `crawler.html` | 4 | Host dropdown |
| `classifier.html` | 4 | Host dropdown |
| `phone_enrich.html` | 4 | Host dropdown |
| `990_index.html` | 4 | Host dropdown |
| `990_parse.html` | 4 | Host dropdown |
| `setup_worker.py` (new) | 5 | Setup CLI command |
| `tests/test_workers.py` (new) | 6 | Unit tests for conflict check + worker model |

## Verification Checklist

After implementation, verify on the live system:
- [ ] Dashboard loads with worker panel (single worker: this host, online)
- [ ] Each phase page shows host dropdown (hidden if single worker)
- [ ] Queue a job — job row shows host in dashboard recent jobs table
- [ ] Orchestrator heartbeat visible (worker shows "online", last seen updates every 5s on HTMX refresh)
- [ ] Stop orchestrator → worker shows "stale" after 5 min, "offline" after 30 min
- [ ] Restart orchestrator → worker recovers to "online"
- [ ] Job detail page for a hypothetical remote job shows "Log on remote host" message
- [ ] `setup_worker` command runs, verifies connectivity, registers worker, prints systemd unit
- [ ] Worker management page: edit display_name, deactivate/reactivate
- [ ] Phase conflict: two resolve jobs for different states can both be queued and started
- [ ] Existing single-host behavior unchanged

## Consultation Log

- **Codex plan-review**: REQUEST_CHANGES → addressed (stale detection: documented intentional deviation from spec — all orchestrators run it; form validation: added explicit InvalidParameterError catch in views with messages.error; UI step split: separated into Step 4 visibility + Step 5 targeting; testing: expanded with health transitions, host validation, offline detection; host column display_name: annotate jobs with worker display names)
- **Claude plan-review**: COMMENT → addressed (form error rendering: explicit try/except in views; 990 conflict: confirmed advisory lock lives in create_*_job, not check_phase_conflict — added note; Path import: fixed in setup_worker; audit log host: explicit "all create views" coverage; stale detection scope: documented as intentional deviation)
- **Codex red-team-plan**: REQUEST_CHANGES → addressed (spec updated: stale detection now "every orchestrator"; _validate_host rejects stale hosts too; fresh-install fallback narrowed to `is_active=True` check; setup_worker: notes/memory_gb editable via UI post-registration)
- **Claude red-team-plan**: REQUEST_CHANGES → addressed (WorkerEditView: input length caps on display_name, phases, notes; audit logging: added _log_audit to deactivate/reactivate/edit actions; per-state conflict locking: existing select_for_update in create_*_job already covers (phase, state_code) — check_phase_conflict is a soft gate in the orchestrator loop, not a lock)

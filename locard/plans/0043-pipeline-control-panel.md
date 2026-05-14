# Plan 0043: Pipeline Control Panel

**Spec**: `locard/specs/0043-pipeline-control-panel.md`
**Date**: 2026-05-14

## Overview

Add a control panel page to the dashboard with queue pause/resume, bulk job operations, orphan detection/cleanup, host status cards, remote service management via command agent, and scheduler config view. 6 features across 6 implementation phases.

## Phase 1: Models & Migration

### Step 1.1: Add `PipelineConfig` model

**File**: `pipeline/models.py` — add after `PipelineAuditLog` (~line 391)

```python
class PipelineConfig(models.Model):
    queue_paused = models.BooleanField(default=False)
    paused_at = models.DateTimeField(null=True, blank=True)
    paused_by = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        db_table = "pipeline_config"
        constraints = [
            models.CheckConstraint(check=models.Q(pk=1), name="singleton_config")
        ]

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
```

### Step 1.2: Add `HostCommand` model

**File**: `pipeline/models.py` — add after `PipelineConfig`

```python
COMMAND_CHOICES = [
    ("start-orchestrator", "Start Orchestrator"),
    ("stop-orchestrator", "Stop Orchestrator"),
    ("restart-orchestrator", "Restart Orchestrator"),
    ("start-dashboard", "Start Dashboard"),
    ("stop-dashboard", "Stop Dashboard"),
    ("restart-dashboard", "Restart Dashboard"),
    ("report-status", "Report Status"),
    ("cleanup-locks", "Cleanup Locks"),
    ("kill-process", "Kill Process"),
]

class HostCommand(models.Model):
    host = models.CharField(max_length=100)
    command = models.CharField(max_length=50, choices=COMMAND_CHOICES)
    args_json = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, default="pending",
        choices=[("pending", "Pending"), ("running", "Running"),
                 ("completed", "Completed"), ("failed", "Failed")])
    result_text = models.TextField(blank=True, default="")
    requested_by = models.ForeignKey(
        "auth.User", on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "host_commands"
        ordering = ["-created_at"]
```

### Step 1.3: Generate and run migration

```bash
python3 manage.py makemigrations pipeline
python3 manage.py migrate
```

Verify both tables are created in `lava_dashboard` schema.

## Phase 2: Command Agent (Orchestrator)

### Step 2.1: Add `_poll_host_commands()` to orchestrator

**File**: `pipeline/management/commands/run_orchestrator.py`

Add to the main loop after `_check_worker_health()` (line 68):

```python
while not self._shutdown:
    self._poll_running_jobs()
    self._start_eligible_jobs()
    self._update_worker_heartbeat()
    self._check_worker_health()
    self._poll_host_commands()       # NEW
    time.sleep(POLL_INTERVAL)
```

The method:

```python
def _poll_host_commands(self):
    from pipeline.models import HostCommand
    from django.db import transaction
    from django.utils import timezone

    # Claim one pending command at a time (atomic to prevent duplicate pickup)
    with transaction.atomic():
        cmd = (
            HostCommand.objects
            .select_for_update(skip_locked=True)
            .filter(host=self.hostname, status="pending")
            .order_by("created_at")
            .first()
        )
        if cmd is None:
            # Also check for restart-orchestrator in "running" state
            # (we are the new process after restart)
            self._check_restart_recovery()
            return

        cmd.status = "running"
        cmd.started_at = timezone.now()
        cmd.save(update_fields=["status", "started_at"])

    try:
        result = self._execute_command(cmd)
        cmd.status = "completed"
        cmd.result_text = _sanitize_result(result)[:4096]
    except Exception as e:
        cmd.status = "failed"
        cmd.result_text = _sanitize_result(str(e))[:4096]
    cmd.finished_at = timezone.now()
    cmd.save(update_fields=["status", "result_text", "finished_at"])
```

### Step 2.2: Add `_execute_command()` dispatcher

```python
_ALLOWED_SERVICES = {
    "orchestrator": "lavandula-orchestrator",
    "dashboard": "lavandula-dashboard",
}

_SERVICE_COMMANDS = {
    "start-orchestrator", "stop-orchestrator", "restart-orchestrator",
    "start-dashboard", "stop-dashboard", "restart-dashboard",
}

def _execute_command(self, cmd):
    if cmd.command in _SERVICE_COMMANDS:
        return self._exec_service_action(cmd)
    elif cmd.command == "report-status":
        return self._exec_report_status()
    elif cmd.command == "cleanup-locks":
        return self._exec_cleanup_locks(cmd.args_json)
    elif cmd.command == "kill-process":
        return self._exec_kill_process(cmd.args_json)
    else:
        raise ValueError(f"Unknown command: {cmd.command}")
```

### Step 2.3: Implement command executors

**`_exec_service_action(cmd)`**:
- Parse action and service from command name: `action, service_key = cmd.command.rsplit("-", 1)` — but actually command names are like `restart-orchestrator`, so split on first `-`: `parts = cmd.command.split("-", 1)` → `action = parts[0]`, `service_name = _ALLOWED_SERVICES[parts[1]]`.
- All service commands use **non-blocking `subprocess.Popen`** (fire-and-forget) per spec contract. The agent spawns the subprocess and returns immediately to avoid blocking the orchestrator loop.
- For `restart-orchestrator` / `stop-orchestrator`: The current process will be killed by systemd — the new process picks up the still-`running` command via `_check_restart_recovery()`.
- For `start-orchestrator`: Only useful when the service is stopped. If the orchestrator is already running (since the agent IS the orchestrator), this is a no-op. Mark completed immediately.
- For dashboard commands (`start-dashboard`, `stop-dashboard`, `restart-dashboard`): Fire-and-forget via `subprocess.Popen(["sudo", "systemctl", action, service_name])`. The agent polls `systemctl is-active <service>` for up to 10 seconds (1s intervals) to confirm the action took effect, then marks completed/failed. This polling is non-blocking to the orchestrator loop because the entire `_poll_host_commands()` call happens within the tick — the 10s poll is acceptable since it's shorter than `POLL_INTERVAL`.
- For all commands: `["sudo", "systemctl", action, service_name]` where `action` is from a hardcoded set `{"start", "stop", "restart"}` and `service_name` is from `_ALLOWED_SERVICES` — never from user input.

**`_check_restart_recovery()`**:
- Query `HostCommand.objects.filter(host=self.hostname, command__in=["restart-orchestrator", "stop-orchestrator"], status="running")`. If found and `started_at` is within last 120 seconds, verify service is running (`systemctl is-active`), mark completed. If older than 120 seconds, mark failed with timeout message.

**`_exec_report_status()`**:
- Collect: `systemctl is-active lavandula-orchestrator`, `systemctl is-active lavandula-dashboard`, disk usage (`shutil.disk_usage("/")`), uptime (`/proc/uptime`), running job count.
- Return JSON string with all values.

**`_exec_cleanup_locks(args)`**:
- Validate `args.get("dry_run")` is bool or absent.
- Run the orphan lock detection query (spec's safety check steps 1-5).
- If `dry_run`: return list of orphan PIDs without terminating.
- Otherwise: `pg_terminate_backend()` for each, return summary.

**`_exec_kill_process(args)`**:
- Validate `args["pid"]` is int, `args["signal"]` is "TERM" or "KILL".
- `os.kill(pid, signal.SIGTERM or signal.SIGKILL)`.
- Catch `ProcessLookupError` (not an error), `PermissionError` (report as failure).

### Step 2.4: Add `_sanitize_result()` helper

```python
import re
_SECRET_PATTERNS = [
    re.compile(r"AKIA[A-Z0-9]{16}"),                              # AWS access key
    re.compile(r"(?:aws_)?(?:secret_)?(?:access_)?key\s*[:=]\s*\S+", re.I),  # key=value
    re.compile(r"(?:password|passwd|token|secret|api.?key)\s*[:=]\s*\S+", re.I),  # generic secrets
    re.compile(r"/home/\w+", re.I),                                # home dir paths
    re.compile(r"(?:export\s+)?\w+(?:KEY|SECRET|TOKEN|PASSWORD)\w*\s*=\s*\S+", re.I),  # env vars
]

def _sanitize_result(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text[:4096]
```

### Step 2.5: Add command timeout classification

Add a `_expire_stale_commands()` method, called at the start of `_poll_host_commands()`:

```python
def _expire_stale_commands(self):
    from pipeline.models import HostCommand
    now = timezone.now()
    # Pending commands older than 60 seconds → timed out
    timed_out_pending = HostCommand.objects.filter(
        host=self.hostname, status="pending",
        created_at__lt=now - timedelta(seconds=60)
    )
    timed_out_pending.update(
        status="failed", finished_at=now,
        result_text="Timed out: command was pending for > 60 seconds"
    )
    # Running commands older than 120 seconds → timed out
    # (except restart-orchestrator/stop-orchestrator which are handled by _check_restart_recovery)
    timed_out_running = HostCommand.objects.filter(
        host=self.hostname, status="running",
        started_at__lt=now - timedelta(seconds=120)
    ).exclude(command__in=["restart-orchestrator", "stop-orchestrator"])
    timed_out_running.update(
        status="failed", finished_at=now,
        result_text="Timed out: command was running for > 120 seconds"
    )
```

### Step 2.6: Integrate queue pause check

**File**: `pipeline/management/commands/run_orchestrator.py`

In `_start_eligible_jobs()`, add at the top:

```python
def _start_eligible_jobs(self):
    from pipeline.models import PipelineConfig
    if PipelineConfig.get().queue_paused:
        return
    # ... existing logic
```

## Phase 3: Backend Views

**File**: `pipeline/views.py` — add new view classes after the existing views (after ~line 1558)

### Step 3.1: `ControlPanelView` (main page)

```python
class ControlPanelView(LoginRequiredMixin, TemplateView):
    template_name = "pipeline/control_panel.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from pipeline.models import PipelineConfig
        ctx["config"] = PipelineConfig.get()
        ctx["phases"] = list(dict(Job.PHASE_CHOICES).keys())
        ctx["states"] = list(
            Job.objects.filter(state_code__isnull=False)
            .values_list("state_code", flat=True).distinct().order_by("state_code")
        )
        ctx["hosts"] = list(
            Worker.objects.filter(is_active=True)
            .values_list("hostname", flat=True).order_by("hostname")
        )
        from pipeline.scheduler_config import load_config
        ctx["scheduler_config"] = load_config()
        return ctx
```

### Step 3.2: Queue pause/resume views

```python
class QueuePauseView(LoginRequiredMixin, View):
    def post(self, request):
        from pipeline.models import PipelineConfig
        config = PipelineConfig.get()
        config.queue_paused = True
        config.paused_at = timezone.now()
        config.paused_by = request.user.username
        config.save()
        PipelineAuditLog.objects.create(
            action="queue_pause", process_name="control-panel",
            parameters={}, source_ip=_client_ip(request))
        messages.success(request, "Queue paused")
        return redirect("control_panel")

class QueueResumeView(LoginRequiredMixin, View):
    def post(self, request):
        from pipeline.models import PipelineConfig
        config = PipelineConfig.get()
        config.queue_paused = False
        config.paused_at = None
        config.paused_by = ""
        config.save()
        PipelineAuditLog.objects.create(
            action="queue_resume", process_name="control-panel",
            parameters={}, source_ip=_client_ip(request))
        messages.success(request, "Queue resumed")
        return redirect("control_panel")
```

### Step 3.3: Bulk cancel view

Two-step flow: preview (GET-like) shows count, then confirm (POST) executes.

```python
class BulkCancelView(LoginRequiredMixin, View):
    def _build_queryset(self, request):
        phase = request.POST.get("phase") or None
        state = request.POST.get("state") or None
        host = request.POST.get("host") or None
        status_filter = request.POST.get("status") or "pending"
        # Valid status values: "pending", "running", "pending+running"
        if status_filter == "pending+running":
            statuses = ["pending", "running"]
        else:
            statuses = [status_filter] if status_filter in ("pending", "running") else ["pending"]
        qs = Job.objects.filter(status__in=statuses)
        if phase:
            qs = qs.filter(phase=phase)
        if state:
            qs = qs.filter(state_code=state)
        if host:
            qs = qs.filter(host=host)
        return qs, {"phase": phase, "state": state, "host": host, "status": status_filter}

    def post(self, request):
        qs, filters = self._build_queryset(request)
        confirmed = request.POST.get("confirmed") == "1"

        if not confirmed:
            # Preview step: return count for confirmation dialog
            count = qs.count()
            # Return HTMX partial with count and hidden form for confirmation
            return render(request, "pipeline/partials/confirm_bulk_action.html", {
                "action": "cancel", "count": count, "filters": filters,
                "warning": "Running jobs will be sent SIGTERM" if "running" in filters.get("status", "") else "",
            })

        jobs = list(qs)
        cancelled = 0
        skipped = 0
        for job in jobs:
            try:
                cancel_job(job)
                cancelled += 1
            except Exception:
                skipped += 1

        outcome = "success" if skipped == 0 else "partial"
        PipelineAuditLog.objects.create(
            action="bulk_cancel", process_name="control-panel",
            parameters={**filters, "cancelled": cancelled, "skipped": skipped,
                        "outcome": outcome},
            source_ip=_client_ip(request))
        messages.success(request, f"Cancelled {cancelled} of {len(jobs)} jobs" +
                         (f" ({skipped} already completed)" if skipped else ""))
        return redirect("control_panel")
```

### Step 3.4: Bulk retry view

Same two-step preview/confirm pattern as BulkCancelView. Filters `status="failed"` with optional phase, state, and host dimensions. Calls `retry_job()`. Skips jobs that already have a pending retry (`Job.objects.filter(retry_of=job, status="pending").exists()`). Confirmation shows "Retry N failed jobs" with count. Outcome logging includes retried count, skipped count (with reason), and outcome status (success/partial).

```python
class BulkRetryView(LoginRequiredMixin, View):
    def post(self, request):
        phase = request.POST.get("phase") or None
        state = request.POST.get("state") or None
        host = request.POST.get("host") or None
        confirmed = request.POST.get("confirmed") == "1"
        qs = Job.objects.filter(status="failed")
        if phase:
            qs = qs.filter(phase=phase)
        if state:
            qs = qs.filter(state_code=state)
        if host:
            qs = qs.filter(host=host)

        if not confirmed:
            return render(request, "pipeline/partials/confirm_bulk_action.html", {
                "action": "retry", "count": qs.count(),
                "filters": {"phase": phase, "state": state, "host": host},
            })

        retried = 0
        skipped = 0
        for job in qs:
            if Job.objects.filter(retry_of=job, status="pending").exists():
                skipped += 1
                continue
            retry_job(job)
            retried += 1
        outcome = "success" if skipped == 0 else "partial"
        PipelineAuditLog.objects.create(
            action="bulk_retry", process_name="control-panel",
            parameters={"phase": phase, "state": state, "host": host,
                        "retried": retried, "skipped_existing_retry": skipped,
                        "outcome": outcome},
            source_ip=_client_ip(request))
        messages.success(request, f"Retried {retried} jobs" +
                         (f" (skipped {skipped} with existing retry)" if skipped else ""))
        return redirect("control_panel")
```

### Step 3.5: Clear queue view

Same two-step confirmation pattern. Shows "This will cancel N pending jobs" before executing.

```python
class ClearQueueView(LoginRequiredMixin, View):
    def post(self, request):
        pending = Job.objects.filter(status="pending")
        confirmed = request.POST.get("confirmed") == "1"
        if not confirmed:
            return render(request, "pipeline/partials/confirm_bulk_action.html", {
                "action": "clear_queue", "count": pending.count(), "filters": {},
            })
        cancelled = 0
        skipped = 0
        for job in pending:
            try:
                cancel_job(job)
                cancelled += 1
            except Exception:
                skipped += 1
        outcome = "success" if skipped == 0 else "partial"
        PipelineAuditLog.objects.create(
            action="clear_queue", process_name="control-panel",
            parameters={"cancelled": cancelled, "skipped": skipped, "outcome": outcome},
            source_ip=_client_ip(request))
        messages.success(request, f"Cancelled {cancelled} pending jobs" +
                         (f" ({skipped} already changed)" if skipped else ""))
        return redirect("control_panel")
```

### Step 3.6: Health check partial

```python
class HealthCheckPartial(HtmxLoginRequiredMixin, TemplateView):
    template_name = "pipeline/partials/control_health.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ten_min_ago = timezone.now() - timedelta(minutes=10)
        offline_hosts = set(
            Worker.objects.filter(
                is_active=True, status__in=["stale", "offline"]
            ).values_list("hostname", flat=True)
        )
        stale_jobs = list(
            Job.objects.filter(status="running").filter(
                models.Q(last_heartbeat__lt=ten_min_ago) |
                models.Q(host__in=offline_hosts)
            )
        )
        ctx["stale_jobs"] = stale_jobs

        # Orphan lock detection
        from django.db import connections
        with connections["default"].cursor() as cur:
            cur.execute("""
                SELECT l.pid, a.client_addr, a.state, a.backend_start
                FROM pg_locks l
                JOIN pg_stat_activity a ON a.pid = l.pid
                WHERE l.locktype = 'advisory' AND a.state = 'idle'
            """)
            lock_rows = cur.fetchall()

        running_host_ips = set(
            Worker.objects.filter(
                hostname__in=Job.objects.filter(status="running")
                    .values_list("host", flat=True),
                is_active=True
            ).values_list("ip_address", flat=True)
        )
        orphan_locks = []
        for pid, client_addr, state, backend_start in lock_rows:
            if str(client_addr) not in {str(ip) for ip in running_host_ips if ip}:
                orphan_locks.append({
                    "pid": pid, "client_addr": client_addr,
                    "backend_start": backend_start,
                })
        ctx["orphan_locks"] = orphan_locks
        return ctx
```

### Step 3.7: Fix stale jobs view

```python
class FixStaleJobsView(LoginRequiredMixin, View):
    def post(self, request):
        job_ids = request.POST.getlist("job_ids")
        fixed = 0
        for jid in job_ids:
            try:
                job = Job.objects.get(pk=int(jid), status="running")
                job.status = "failed"
                job.finished_at = timezone.now()
                job.error_message = "Marked failed by operator (stale)"
                job.save(update_fields=["status", "finished_at", "error_message"])
                # Cascade cancel dependents
                for dep in Job.objects.filter(depends_on=job, status__in=["pending", "scheduled"]):
                    cancel_job(dep)
                fixed += 1
            except (Job.DoesNotExist, ValueError):
                pass
        outcome = "success" if fixed == len(job_ids) else ("partial" if fixed > 0 else "failed")
        PipelineAuditLog.objects.create(
            action="fix_stale", process_name="control-panel",
            parameters={"job_ids": job_ids, "fixed": fixed, "outcome": outcome},
            source_ip=_client_ip(request))
        messages.success(request, f"Marked {fixed} jobs as failed")
        return redirect("control_panel")
```

### Step 3.8: Release locks view

```python
class ReleaseLocksView(LoginRequiredMixin, View):
    def post(self, request):
        pids = request.POST.getlist("pids")
        released = 0
        failed = 0
        from django.db import connections
        with connections["default"].cursor() as cur:
            for pid_str in pids:
                try:
                    cur.execute("SELECT pg_terminate_backend(%s)", [int(pid_str)])
                    if cur.fetchone()[0]:
                        released += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1
        outcome = "success" if failed == 0 else ("partial" if released > 0 else "failed")
        PipelineAuditLog.objects.create(
            action="release_locks", process_name="control-panel",
            parameters={"pids": pids, "released": released, "failed": failed, "outcome": outcome},
            source_ip=_client_ip(request))
        messages.success(request, f"Released {released} locks ({failed} failed)")
        return redirect("control_panel")
```

### Step 3.9: Host status partial

```python
class HostStatusPartial(HtmxLoginRequiredMixin, TemplateView):
    template_name = "pipeline/partials/control_hosts.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from pipeline.models import HostCommand
        workers = list(Worker.objects.filter(is_active=True).order_by("hostname"))
        now = timezone.now()
        for w in workers:
            w.running_count = Job.objects.filter(host=w.hostname, status="running").count()
            w.pending_count = Job.objects.filter(host=w.hostname, status="pending").count()
            # Freshness
            if w.last_heartbeat:
                age = (now - w.last_heartbeat).total_seconds()
                w.heartbeat_stale = age > 60
                w.heartbeat_offline = age > 300
            else:
                w.heartbeat_stale = True
                w.heartbeat_offline = True
            # Latest report-status
            latest_report = (
                HostCommand.objects.filter(host=w.hostname, command="report-status", status="completed")
                .order_by("-finished_at").first()
            )
            w.latest_report = latest_report
        ctx["workers"] = workers
        return ctx
```

### Step 3.10: Host command status partial

```python
class HostCommandStatusPartial(HtmxLoginRequiredMixin, TemplateView):
    template_name = "pipeline/partials/host_command_status.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        hostname = self.kwargs["hostname"]
        recent_commands = (
            HostCommand.objects.filter(host=hostname)
            .order_by("-created_at")[:10]
        )
        ctx["commands"] = recent_commands
        ctx["hostname"] = hostname
        return ctx
```

This endpoint is polled by the host card to show command completion status. Dashboard polls every 2 seconds for up to 30 seconds after issuing a command, then stops (shows timeout if still pending/running).

### Step 3.12: Host command view

```python
class HostCommandView(LoginRequiredMixin, View):
    def post(self, request, hostname):
        from pipeline.models import HostCommand, COMMAND_CHOICES
        command = request.POST.get("command")
        valid_commands = dict(COMMAND_CHOICES)
        if command not in valid_commands:
            PipelineAuditLog.objects.create(
                action="host_command", process_name="control-panel",
                parameters={"host": hostname, "command": command, "outcome": "denied",
                            "reason": "invalid command"},
                source_ip=_client_ip(request))
            messages.error(request, f"Invalid command: {command}")
            return redirect("control_panel")

        worker = Worker.objects.filter(hostname=hostname, is_active=True).first()
        if not worker:
            PipelineAuditLog.objects.create(
                action="host_command", process_name="control-panel",
                parameters={"host": hostname, "command": command, "outcome": "denied",
                            "reason": "unknown host"},
                source_ip=_client_ip(request))
            messages.error(request, f"Unknown host: {hostname}")
            return redirect("control_panel")

        # Check if host is offline — disable stop/start/restart for offline hosts
        if worker.status == "offline" and command not in ("report-status",):
            messages.error(request, f"Host {hostname} is offline")
            return redirect("control_panel")

        args = {}
        if command == "cleanup-locks":
            args["dry_run"] = request.POST.get("dry_run") == "on"
        elif command == "kill-process":
            try:
                args["pid"] = int(request.POST["pid"])
                args["signal"] = request.POST.get("signal", "TERM")
                if args["signal"] not in ("TERM", "KILL"):
                    raise ValueError
            except (KeyError, ValueError):
                messages.error(request, "Invalid kill-process parameters")
                return redirect("control_panel")

        HostCommand.objects.create(
            host=hostname, command=command, args_json=args,
            requested_by=request.user)
        PipelineAuditLog.objects.create(
            action="host_command", process_name="control-panel",
            parameters={"host": hostname, "command": command, "args": args, "outcome": "submitted"},
            source_ip=_client_ip(request))
        messages.success(request, f"Command '{command}' sent to {hostname}")
        return redirect("control_panel")
```

### Step 3.13: Add `_client_ip()` helper

```python
def _client_ip(request):
    # Use REMOTE_ADDR directly — no reverse proxy in this deployment.
    # XFF is untrusted (spoofable) and only logged as supplementary info.
    return request.META.get("REMOTE_ADDR", "0.0.0.0")
```

Check if this already exists in views.py — the existing audit logging likely has a similar pattern.

## Phase 4: Templates

### Step 4.1: Main control panel page

**New file**: `pipeline/templates/pipeline/control_panel.html`

Extends `pipeline/base.html`. Sections:

1. **Queue Control** — Pause/Resume toggle, bulk filter form, Clear All Pending button
2. **Health Check** — HTMX div polling `control/health/` every 10s
3. **Hosts** — HTMX div polling `control/hosts/` every 10s
4. **Scheduler Config** — Read-only display of `scheduler_config` values

Queue pause/resume uses standard Django forms with `{% csrf_token %}`. Bulk operations use a filter form with dropdowns for phase, state, host, and an "Include running" checkbox.

Destructive actions (bulk cancel, clear queue, release locks, mark failed) use a two-step server-backed confirmation flow:
1. First POST without `confirmed=1` returns an HTMX partial showing the count of affected items and a "Confirm" button
2. The confirm button submits with `confirmed=1` to execute the action
This ensures the operator sees exact counts (e.g., "This will cancel 12 pending jobs") before executing.

### Step 4.2: Confirmation partial

**New file**: `pipeline/templates/pipeline/partials/confirm_bulk_action.html`

HTMX partial returned by the preview step of bulk actions. Shows:
- Action description ("Cancel", "Retry", "Clear All Pending")
- Count of affected items ("This will cancel 12 pending jobs")
- Warning text (if applicable, e.g., "Running jobs will be sent SIGTERM")
- Hidden form fields to replay the original filters + `confirmed=1`
- "Confirm" and "Cancel" buttons

### Step 4.3: Health check partial

**New file**: `pipeline/templates/pipeline/partials/control_health.html`

Shows:
- Stale jobs list with checkboxes and "Mark Failed" button (form posts to `fix-stale/` with `job_ids[]`)
- Orphan locks list with checkboxes and "Release Locks" button (form posts to `release-locks/` with `pids[]`)
- If both lists empty, show green "All clear" message

### Step 4.4: Host status partial

**New file**: `pipeline/templates/pipeline/partials/control_hosts.html`

Card grid (flex/grid layout). Per host card:
- Header: hostname / display_name + status badge
- Body: CPU/memory bars (with freshness rules), running/pending job counts
- Service status (from latest report-status command result, parsed from JSON)
- Footer: action buttons (Start/Stop/Restart Orchestrator, Start/Stop/Restart Dashboard, Report Status)

Each action button is a form posting to `control/hosts/<hostname>/command/` with a hidden `command` field. Buttons for offline hosts are disabled with a "Host offline" tooltip.

### Step 4.5: Host command status partial

**New file**: `pipeline/templates/pipeline/partials/host_command_status.html`

Shows recent commands for a specific host. Polled via HTMX every 2 seconds for 30 seconds after a command is issued (then stops). Displays command status badges (pending/running/completed/failed), result text for completed/failed commands. All `result_text` output is rendered via Django's default auto-escaping (`{{ cmd.result_text }}`) — never use `|safe` or `{% autoescape off %}` for system-generated output to prevent XSS.

### Step 4.6: Queue paused banner

**File**: `pipeline/templates/pipeline/base.html`

Add immediately after the opening `<main>` or at the top of the content area:

```html
{% load pipeline_tags %}
{% if queue_paused %}
<div class="bg-yellow-100 border-l-4 border-yellow-500 text-yellow-700 p-3 mb-4">
  <span class="font-bold">QUEUE PAUSED</span>
  {% if paused_by %} by {{ paused_by }}{% endif %}
  {% if paused_at %} — {{ paused_at|timesince }} ago{% endif %}
</div>
{% endif %}
```

This requires a context processor to inject `queue_paused` into every template. Add to `pipeline/context_processors.py` (new file):

```python
def pipeline_config(request):
    from pipeline.models import PipelineConfig
    try:
        config = PipelineConfig.get()
        return {
            "queue_paused": config.queue_paused,
            "paused_by": config.paused_by,
            "paused_at": config.paused_at,
        }
    except Exception:
        return {"queue_paused": False}
```

Register in `dashboard/settings.py` under `TEMPLATES[0]["OPTIONS"]["context_processors"]`:

```python
"pipeline.context_processors.pipeline_config",
```

### Step 4.7: Navigation link

**File**: `pipeline/templates/pipeline/base.html`

Add after the "Workers" link (line 33):

```html
<li><a href="{% url 'control_panel' %}" class="block px-4 py-2 hover:bg-gray-800 hover:text-white {% if 'control' in request.resolver_match.url_name %}bg-gray-800 text-white{% endif %}">Control Panel</a></li>
```

## Phase 5: URLs & Wiring

### Step 5.1: Add URL patterns

**File**: `pipeline/urls.py` — add after existing patterns:

```python
# Control Panel
path("control/", views.ControlPanelView.as_view(), name="control_panel"),
path("control/queue/pause/", views.QueuePauseView.as_view(), name="queue_pause"),
path("control/queue/resume/", views.QueueResumeView.as_view(), name="queue_resume"),
path("control/jobs/bulk-cancel/", views.BulkCancelView.as_view(), name="bulk_cancel"),
path("control/jobs/bulk-retry/", views.BulkRetryView.as_view(), name="bulk_retry"),
path("control/jobs/clear-queue/", views.ClearQueueView.as_view(), name="clear_queue"),
path("control/health/", views.HealthCheckPartial.as_view(), name="control_health"),
path("control/health/fix-stale/", views.FixStaleJobsView.as_view(), name="fix_stale_jobs"),
path("control/health/release-locks/", views.ReleaseLocksView.as_view(), name="release_locks"),
path("control/hosts/", views.HostStatusPartial.as_view(), name="control_hosts"),
path("control/hosts/<str:hostname>/command/", views.HostCommandView.as_view(), name="host_command"),
path("control/hosts/<str:hostname>/status/", views.HostCommandStatusPartial.as_view(), name="host_command_status"),
```

## Phase 6: Tests

**New file**: `pipeline/tests/test_control_panel.py`

### Test data strategy

Tests use Django's `TestCase` with the default database. Models `PipelineConfig`, `HostCommand`, `Job`, `Worker`, and `PipelineAuditLog` are all ORM-managed — no raw SQL needed.

### Test cases

1. **`test_queue_pause_resume`** — POST pause → `PipelineConfig.queue_paused` is True → POST resume → False
2. **`test_pause_blocks_scheduling`** — Pause queue → call `_start_eligible_jobs()` (or verify `PipelineConfig.get().queue_paused` check) → no jobs start
3. **`test_bulk_cancel_by_phase`** — Create 3 extract + 2 reclassify pending → POST bulk cancel phase=extract, confirmed=1 → 3 cancelled, 2 remain
4. **`test_bulk_cancel_cascade`** — Create A→B→C chain → cancel A → B and C also cancelled
5. **`test_bulk_cancel_excludes_running_by_default`** — Create 1 running + 1 pending → POST with status=pending → only pending cancelled
6. **`test_bulk_cancel_includes_running_when_selected`** — Same setup → POST with status=pending+running → both cancelled
7. **`test_bulk_cancel_preview_shows_count`** — POST without confirmed=1 → returns HTMX partial with count, no jobs cancelled
8. **`test_bulk_cancel_partial_success`** — Create 3 pending, cancel 1 between preview and confirm → shows "Cancelled 2 of 3 (1 already completed)"
9. **`test_bulk_retry`** — Create 3 failed jobs → POST bulk retry confirmed=1 → 3 new pending jobs exist with retry_of set
10. **`test_bulk_retry_skips_existing_retry`** — Failed job already has pending retry → bulk retry skips it, summary says "Skipped 1"
11. **`test_clear_queue`** — Create 5 pending + 2 running → POST clear queue confirmed=1 → 5 cancelled, 2 still running
12. **`test_stale_job_detection`** — Create running job with heartbeat 15 min ago → health check partial includes it in stale_jobs
13. **`test_fix_stale_marks_failed`** — POST fix-stale with job_id → job status is failed, error_message set
14. **`test_fix_stale_cascades`** — Stale job has pending dependent → marking stale job failed → dependent cancelled
15. **`test_orphan_lock_detection`** — Unit test the lock detection query logic (mock pg_locks results)
16. **`test_host_command_creation`** — POST restart-orchestrator for host → HostCommand created with correct fields
17. **`test_host_command_invalid_command`** — POST unknown command → error message, no HostCommand created, audit log with outcome="denied"
18. **`test_host_command_invalid_host`** — POST for non-existent host → error message, audit log with outcome="denied"
19. **`test_host_command_offline_host_blocked`** — POST restart-orchestrator for offline host → error message, command not created
20. **`test_kill_process_validates_args`** — POST kill-process without pid → error
21. **`test_audit_logging_with_outcome`** — Perform bulk cancel → PipelineAuditLog entry exists with action="bulk_cancel" and outcome="success"
22. **`test_audit_logging_partial_outcome`** — Bulk cancel with some skipped → outcome="partial"
23. **`test_pause_banner_context_processor`** — Pause queue → GET any page → context contains `queue_paused=True`
24. **`test_control_panel_requires_login`** — GET control panel without auth → 302 redirect
25. **`test_htmx_partials_are_fragments`** — GET health/hosts/host-command-status endpoints → no `<html>` tag, contains expected elements
26. **`test_pipeline_config_singleton`** — Two calls to `PipelineConfig.get()` return same row (pk=1)
27. **`test_command_pending_timeout`** — Create HostCommand with created_at 90s ago, status=pending → agent's `_expire_stale_commands()` marks it failed
28. **`test_command_running_timeout`** — Create HostCommand with started_at 150s ago, status=running → agent's `_expire_stale_commands()` marks it failed
29. **`test_command_claiming_atomicity`** — Create 2 pending commands for same host → calling `_poll_host_commands()` claims only one, second stays pending
30. **`test_restart_dashboard_completion`** — Create restart-dashboard command → simulate agent execution → command transitions pending→running→completed
31. **`test_lock_cleanup_false_positive_prevention`** — Mock advisory lock from host with running job → cleanup skips that connection

## File Summary

| File | Action | Lines |
|------|--------|-------|
| `pipeline/models.py` | Modify | +~50 (2 new models, expanded COMMAND_CHOICES) |
| `pipeline/management/commands/run_orchestrator.py` | Modify | +~150 (command agent + timeout classification) |
| `pipeline/views.py` | Modify | +~300 (13 view classes + helpers) |
| `pipeline/urls.py` | Modify | +12 (URL patterns) |
| `pipeline/context_processors.py` | New | ~15 |
| `pipeline/templates/pipeline/control_panel.html` | New | ~130 |
| `pipeline/templates/pipeline/partials/confirm_bulk_action.html` | New | ~40 |
| `pipeline/templates/pipeline/partials/control_health.html` | New | ~50 |
| `pipeline/templates/pipeline/partials/control_hosts.html` | New | ~90 |
| `pipeline/templates/pipeline/partials/host_command_status.html` | New | ~30 |
| `pipeline/templates/pipeline/base.html` | Modify | +5 (nav link + pause banner) |
| `pipeline/tests/test_control_panel.py` | New | ~450 |
| `dashboard/settings.py` | Modify | +1 (context processor) |
| Migration file | New | auto-generated |

Total: ~1300 lines, 6 modified files, 8 new files.

## Acceptance Criteria Mapping

| AC | Phase | How verified |
|----|-------|-------------|
| AC1 (pause/resume) | 2, 3 | Tests 1-2 |
| AC2 (bulk cancel) | 3 | Tests 3-8 |
| AC3 (bulk retry) | 3 | Tests 9-10 |
| AC4 (clear queue) | 3 | Test 11 |
| AC5 (stale detection) | 3 | Tests 12-14 |
| AC6 (orphan locks) | 3 | Tests 15, 31 |
| AC7 (host cards) | 3, 4 | Test 25 + manual |
| AC8 (service restart) | 2 | Tests 16, 30 + manual |
| AC9 (audit trail) | 3 | Tests 21-22 |
| AC10 (separate host) | All | Architecture (DB-polling, no localhost assumptions) |
| AC11 (pause banner) | 4 | Test 23 |
| AC12 (agent in orchestrator) | 2 | Tests 27-29 |

## Implementation Order

The phases are sequential — each builds on the prior:

1. **Models & Migration** — foundation, must be first
2. **Command Agent** — orchestrator changes, can be tested independently
3. **Backend Views** — depends on models
4. **Templates** — depends on views (note: URLs are wired in the same commit as views/templates, not separately, so each phase is independently testable)
5. **URLs & Wiring** — wire up all URL patterns (can be done alongside Phase 3/4 if preferred, but listed separately for clarity)
6. **Tests** — written alongside each phase, run at end

The builder should implement and commit in phase order, running existing tests after each phase to avoid regressions. URLs should be wired as soon as their corresponding views are created to keep each phase independently runnable.

## Consultation Log

### Red Team Security Review (2026-05-14)

**Gemini**: REQUEST_CHANGES. 0 CRITICAL, 2 HIGH, 1 LOW.

1. **HIGH — Incomplete secret redaction**: `_sanitize_result()` regex too narrow (AWS-only).
   - **Disposition**: Fixed. Expanded to 5 pattern rules covering AWS keys, generic secret key-value pairs, home directory paths, and environment variable exports.

2. **HIGH — `_client_ip()` XFF spoofing**: `X-Forwarded-For` header is spoofable, audit trail unreliable.
   - **Disposition**: Fixed. Changed to use `REMOTE_ADDR` directly — no reverse proxy in this deployment. XFF is untrusted and ignored.

3. **LOW — Output encoding for result_text**: Django auto-escaping must be maintained.
   - **Disposition**: Fixed. Added explicit documentation that `result_text` must always be rendered with Django auto-escaping. Never use `|safe` or `{% autoescape off %}` for system-generated output.

### First Consultation (2026-05-14)

**Gemini**: APPROVE (HIGH confidence). No key issues.

**Codex**: REQUEST_CHANGES (HIGH confidence). 10 findings:

1. Missing start/stop commands (spec Goal 3 says "start, stop, and restart") → **Fixed**: Added `start-*` and `stop-*` to COMMAND_CHOICES, `_exec_service_action()` dispatcher, and sudoers.
2. `HostCommandStatusPartial` endpoint missing from plan → **Fixed**: Added view (Step 3.10), template (Step 4.5), and URL pattern.
3. Bulk cancel status filter misaligned with spec → **Fixed**: Changed from `include_running` checkbox to proper `status` filter dimension (pending / running / pending+running).
4. Destructive action confirmation not server-backed → **Fixed**: All destructive actions now use two-step flow: preview POST returns HTMX partial with count, confirm POST with `confirmed=1` executes. New `confirm_bulk_action.html` partial template (Step 4.2).
5. Host card status freshness underspecified → **Fixed**: Added "unknown" vs "stopped" derivation rules, offline host button disabling.
6. Command timeout classification missing → **Fixed**: Added `_expire_stale_commands()` method (Step 2.5) that marks pending > 60s and running > 120s as failed.
7. Command claiming not atomic → **Fixed**: Wrapped `select_for_update` + status update in `transaction.atomic()` block.
8. `restart-dashboard` uses blocking subprocess.run, contradicts spec → **Fixed**: All service commands now use non-blocking `subprocess.Popen` per spec contract. Dashboard commands poll `systemctl is-active` for up to 10s to confirm.
9. Outcome logging incomplete → **Fixed**: All audit log entries now include `outcome` field (success/partial/failed/denied). Denied attempts (invalid command, unknown host) also logged.
10. Missing test coverage → **Fixed**: Added 9 new test cases (27-31 for timeout/claiming/completion, 7-8 for preview/partial success, 19 for offline host, 21-22 for outcome logging). Total: 31 tests.

Secondary fixes:
- Phase count "5" → "6" in overview
- File summary updated to match actual file list (8 new files, not 5)
- Implementation order note: URLs should land with views for independent testability

import socket

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import models
from django.db.models import Count, Max, Q
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView


class HtmxLoginRequiredMixin(LoginRequiredMixin):
    """LoginRequiredMixin that returns HX-Redirect for HTMX requests."""

    def handle_no_permission(self):
        if self.request.headers.get("HX-Request"):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = self.get_login_url()
            return response
        return super().handle_no_permission()

from .models import (
    COMMAND_CHOICES,
    CrawledOrg,
    FilingIndex,
    HostCommand,
    IndexRefreshLog,
    Job,
    JobEvent,
    NonprofitSeed,
    OrgProvenance,
    Person,
    PipelineAuditLog,
    PipelineConfig,
    PipelineProcess,
    Report,
    Worker,
)
from .orchestrator import (
    DuplicateJobError,
    InvalidParameterError,
    cancel_job,
    create_990_index_job,
    create_990_parse_job,
    create_classify_job,
    create_crawl_job,
    create_phone_enrich_job,
    create_resolve_job,
    create_state_jobs,
    create_v3_job,
    retry_job,
)
from .process_manager import check_process, read_log_tail, start_process, stop_process


def _log_audit(request, action, process_name, parameters=None):
    ip = request.META.get("REMOTE_ADDR", "127.0.0.1")
    PipelineAuditLog.objects.create(
        action=action,
        process_name=process_name,
        parameters=parameters or {},
        source_ip=ip,
    )


def _get_hostname():
    return socket.gethostname()


def _expand_llm_preset(config: dict) -> dict:
    """Replace llm_preset key with llm_url, llm_model, llm_api_key_ssm."""
    from .forms import LLM_PRESETS
    preset_key = config.pop("llm_preset", None)
    if preset_key and preset_key in LLM_PRESETS:
        config.update(LLM_PRESETS[preset_key])
    return config


def _resolve_depends_on(request):
    """Parse depends_on from POST, return Job instance or None."""
    raw = request.POST.get("depends_on", "").strip()
    if not raw:
        return None
    try:
        return Job.objects.get(pk=int(raw), status__in=["running", "pending"])
    except (ValueError, Job.DoesNotExist):
        return None


def _get_dependency_choices(phase=None):
    """Return running/pending/recently-completed jobs suitable as dependency targets."""
    from datetime import timedelta
    cutoff = timezone.now() - timedelta(hours=24)
    qs = Job.objects.filter(
        Q(status__in=["running", "pending"]) |
        Q(status="completed", finished_at__gte=cutoff)
    ).select_related("depends_on").order_by("-created_at")
    if phase:
        qs = qs.filter(phase=phase)
    choices = []
    worker_names = dict(
        Worker.objects.filter(is_active=True).values_list("hostname", "display_name")
    )
    for j in qs[:20]:
        host_label = worker_names.get(j.host) or j.host.split("-")[-1]
        status_display = j.status
        if j.status == "completed" and j.finished_at:
            mins_ago = int((timezone.now() - j.finished_at).total_seconds() // 60)
            status_display = f"done {mins_ago}m ago"
        choices.append((j.pk, f"#{j.pk} {j.phase} {j.state_code or 'global'} [{status_display}] @ {host_label}"))
    return choices


# ---------------------------------------------------------------------------
# Shared helpers for job display
# ---------------------------------------------------------------------------

_CONFIG_ALLOWLIST = {
    "seed": ["states", "target", "ntee_majors"],
    "resolve": ["state", "search_engines", "llm_model", "brave_qps", "search_qps", "consumer_threads", "limit"],
    "crawl": ["state", "limit", "classifier_backend"],
    "classify": ["state", "llm_model", "definition", "limit", "re_classify"],
    "enrich-phone": ["state", "search_engines", "limit"],
    "990-index": ["filing_year"],
    "990-parse": ["filing_year", "limit"],
    "extract-context": ["state", "ein", "limit", "reextract"],
    "reclassify": ["run_tag", "backend", "state", "ein", "sample", "definition", "dry_run", "allow_fallback"],
    "compare-classify": ["run_tag", "state"],
    "resolve-disagree": ["run_tag", "backend", "state", "sample", "dry_run"],
    "promote-classify": ["run_tag", "state", "confirm"],
}


def _format_job_config(job):
    if not job or not job.config_json:
        return []
    allowed = _CONFIG_ALLOWLIST.get(job.phase, [])
    parts = []
    for key in allowed:
        val = job.config_json.get(key)
        if val is not None and val != "":
            parts.append(f"{key}={val}")
    return parts


def _annotate_running_jobs(jobs):
    now = timezone.now()
    annotated = []
    for job in jobs:
        job.config_display = _format_job_config(job)
        if job.started_at:
            mins = int((now - job.started_at).total_seconds() // 60)
            job.elapsed = f"{mins}m ago" if mins > 0 else "just started"
        else:
            job.elapsed = "pending"
        stats = (job.config_json or {}).get("stats", {})
        job.stats_processed = stats.get("processed", 0)
        job.stats_extracted = stats.get("extracted", 0)
        job.stats_failed = stats.get("failed", 0)
        annotated.append(job)
    return annotated


def _job_duration(job):
    if job.finished_at and job.started_at:
        total_secs = int((job.finished_at - job.started_at).total_seconds())
        if total_secs < 60:
            return f"{total_secs}s"
        mins = total_secs // 60
        return f"{mins}m {total_secs % 60}s"
    return None


def _annotate_recent_jobs(jobs):
    for job in jobs:
        job.duration_display = _job_duration(job)
    return list(jobs)


def _annotate_host_display(jobs):
    worker_names = dict(
        Worker.objects.filter(is_active=True).values_list("hostname", "display_name")
    )
    for job in jobs:
        job.host_display_name = worker_names.get(job.host, "")
    return jobs


def _elapsed_since(dt):
    if not dt:
        return "never"
    delta = int((timezone.now() - dt).total_seconds())
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    return f"{delta // 3600}h ago"


def _get_worker_choices():
    workers = Worker.objects.filter(is_active=True).order_by("hostname")
    current = _get_hostname()
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


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "pipeline/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(_dashboard_stats())
        return ctx


class DashboardStatsPartial(HtmxLoginRequiredMixin, TemplateView):
    template_name = "pipeline/partials/dashboard_stats.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(_dashboard_stats())
        return ctx


def _dashboard_stats():
    from django.db import connections

    with connections["pipeline"].cursor() as cursor:
        cursor.execute("""
            SELECT
                s.state,
                COUNT(*) as seeded,
                SUM(CASE WHEN s.resolver_status = 'resolved' THEN 1 ELSE 0 END) as resolved,
                COUNT(DISTINCT co.ein) as crawled,
                SUM(CASE WHEN s.phone IS NOT NULL AND s.phone != '' THEN 1 ELSE 0 END) as has_phone
            FROM nonprofits_seed s
            LEFT JOIN crawled_orgs co ON s.ein = co.ein
            GROUP BY s.state
            ORDER BY COUNT(*) DESC
        """)
        columns = [col[0] for col in cursor.description]
        state_rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

        cursor.execute("""
            SELECT
                s.state,
                COUNT(DISTINCT c.content_sha256) as total_reports,
                COUNT(DISTINCT CASE WHEN c.classification IS NOT NULL THEN c.content_sha256 END) as classified
            FROM nonprofits_seed s
            JOIN corpus c ON s.ein = c.source_org_ein
            GROUP BY s.state
        """)
        report_rows = {row[0]: {"total_reports": row[1], "classified": row[2]} for row in cursor.fetchall()}

    for row in state_rows:
        rpt = report_rows.get(row["state"], {})
        row["total_reports"] = rpt.get("total_reports", 0)
        row["classified"] = rpt.get("classified", 0)
        row["resolved_pct"] = round(row["resolved"] / row["seeded"] * 100) if row["seeded"] > 0 else 0

    running_jobs = _annotate_running_jobs(
        Job.objects.filter(status="running")
    )

    running_states = set()
    pending_states = set()
    for job in Job.objects.filter(status__in=["running", "pending"]):
        if job.state_code:
            if job.status == "running":
                running_states.add(job.state_code)
            else:
                pending_states.add(job.state_code)

    def sort_key(r):
        if r["state"] in running_states:
            return (0, -r["seeded"])
        if r["state"] in pending_states:
            return (1, -r["seeded"])
        return (2, -r["seeded"])

    state_rows.sort(key=sort_key)
    for row in state_rows:
        row["is_running"] = row["state"] in running_states
        row["is_pending"] = row["state"] in pending_states

    recent_jobs = _annotate_recent_jobs(Job.objects.order_by("-created_at")[:10])
    _annotate_host_display(recent_jobs)

    workers_qs = list(
        Worker.objects.filter(is_active=True)
        .values("hostname", "display_name", "status", "last_heartbeat", "capabilities")
    )
    for w in workers_qs:
        w["active_jobs"] = Job.objects.filter(
            host=w["hostname"], status__in=["running", "pending"]
        ).count()
        w["phases"] = (w["capabilities"] or {}).get("phases", ["all"])
        w["last_heartbeat_display"] = _elapsed_since(w["last_heartbeat"]) if w["last_heartbeat"] else "never"

    return {
        "state_rows": state_rows,
        "running_jobs": running_jobs,
        "recent_jobs": recent_jobs,
        "workers": workers_qs,
        "show_phase": True,
        "show_host": True,
    }


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

class SeederView(LoginRequiredMixin, TemplateView):
    template_name = "pipeline/seeder.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from .forms import RunStateForm
        ctx["form"] = RunStateForm()
        ctx["running_job"] = Job.objects.filter(phase="seed", status="running").first()
        ctx["recent_jobs"] = Job.objects.filter(phase="seed").order_by("-created_at")[:20]
        ctx["seed_stats"] = (
            NonprofitSeed.objects.values("state")
            .annotate(count=Count("ein"))
            .order_by("state")
        )
        workers = _get_worker_choices()
        ctx["worker_choices"] = workers
        ctx["show_host_selector"] = len(workers) > 1
        return ctx


class JobListView(HtmxLoginRequiredMixin, TemplateView):
    template_name = "pipeline/jobs.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["active_jobs"] = Job.objects.filter(status="running").order_by("-started_at")
        ctx["pending_jobs"] = Job.objects.filter(status="pending").order_by("created_at")
        from django.db.models import F
        ctx["history_jobs"] = Job.objects.filter(
            status__in=["completed", "failed", "cancelled"]
        ).order_by(F("finished_at").desc(nulls_last=True))[:50]
        ctx["show_host"] = True
        return ctx


class JobDetailView(LoginRequiredMixin, DetailView):
    model = Job
    template_name = "pipeline/job_detail.html"
    context_object_name = "job"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["log_content"] = read_log_tail(self.object.log_file)
        ctx["hostname"] = _get_hostname()
        from .models import JobEvent
        ctx["events"] = JobEvent.objects.filter(job=self.object).order_by("-timestamp")[:200]
        return ctx


class JobCreateView(LoginRequiredMixin, View):
    def post(self, request):
        from .forms import RunStateForm
        form = RunStateForm(request.POST)
        if not form.is_valid():
            messages.error(request, f"Invalid form: {form.errors.as_text()}")
            return redirect("seeder")

        state_codes = form.cleaned_data["state_codes"]
        phases = form.cleaned_data["phases"]
        config = {k: v for k, v in form.cleaned_data.items() if k not in ("state_codes", "phases") and v not in (None, "", False)}
        config = _expand_llm_preset(config)
        host = request.POST.get("host", _get_hostname())

        try:
            jobs = create_state_jobs(state_codes, phases, config, host)
            _log_audit(request, "job_create", "state_jobs", {
                "states": state_codes, "phases": phases, "job_ids": [j.pk for j in jobs], "host": host,
            })
            messages.success(request, f"Created {len(jobs)} job(s)")
        except DuplicateJobError as e:
            messages.error(request, str(e))
        except InvalidParameterError as e:
            messages.error(request, str(e))

        return redirect("seeder")


class CrawlJobCreateView(LoginRequiredMixin, View):
    def post(self, request):
        from .forms import RunCrawlForm
        form = RunCrawlForm(request.POST)
        if not form.is_valid():
            messages.error(request, f"Invalid form: {form.errors.as_text()}")
            return redirect("crawler")

        config = {k: v for k, v in form.cleaned_data.items() if v not in (None, "", False)}
        if "async_mode" in config:
            config["async"] = config.pop("async_mode")
        host = request.POST.get("host", _get_hostname())
        depends_on = _resolve_depends_on(request)
        try:
            job = create_crawl_job(config, host, depends_on=depends_on)
            _log_audit(request, "job_create", "crawl", {"job_id": job.pk, "host": host})
            msg = f"Created crawl job #{job.pk}"
            if depends_on:
                msg += f" (queued after #{depends_on.pk})"
            messages.success(request, msg)
        except (DuplicateJobError, InvalidParameterError) as e:
            messages.error(request, str(e))

        return redirect("crawler")


class ResolveJobCreateView(LoginRequiredMixin, View):
    def post(self, request):
        from .forms import ResolverForm
        form = ResolverForm(request.POST)
        if not form.is_valid():
            messages.error(request, f"Invalid form: {form.errors.as_text()}")
            return redirect("resolver")

        config = {k: v for k, v in form.cleaned_data.items() if v not in (None, "", False)}
        config = _expand_llm_preset(config)
        raw_engines = config.get("search_engines", "brave")
        config["search_engines"] = raw_engines.replace("_", ",")
        host = request.POST.get("host", _get_hostname())
        depends_on = _resolve_depends_on(request)
        try:
            job = create_resolve_job(config, host, depends_on=depends_on)
            _log_audit(request, "job_create", "resolve", {"job_id": job.pk, "host": host})
            msg = f"Created resolve job #{job.pk}"
            if depends_on:
                msg += f" (queued after #{depends_on.pk})"
            messages.success(request, msg)
        except (DuplicateJobError, InvalidParameterError) as e:
            messages.error(request, str(e))

        return redirect("resolver")


class ClassifyJobCreateView(LoginRequiredMixin, View):
    def post(self, request):
        from .forms import ClassifierForm
        form = ClassifierForm(request.POST)
        if not form.is_valid():
            messages.error(request, f"Invalid form: {form.errors.as_text()}")
            return redirect("classifier")

        config = {k: v for k, v in form.cleaned_data.items() if v not in (None, "", False)}
        config = _expand_llm_preset(config)
        host = request.POST.get("host", _get_hostname())
        depends_on = _resolve_depends_on(request)
        try:
            job = create_classify_job(config, host, depends_on=depends_on)
            _log_audit(request, "job_create", "classify", {"job_id": job.pk, "host": host})
            msg = f"Created classify job #{job.pk}"
            if depends_on:
                msg += f" (queued after #{depends_on.pk})"
            messages.success(request, msg)
        except (DuplicateJobError, InvalidParameterError) as e:
            messages.error(request, str(e))

        return redirect("classifier")


class JobCancelView(LoginRequiredMixin, View):
    def post(self, request, pk):
        job = get_object_or_404(Job, pk=pk)
        cancel_job(job)
        _log_audit(request, "job_cancel", job.phase, {"job_id": job.pk})
        messages.success(request, f"Cancelled job #{job.pk}")
        next_url = request.GET.get("next", "")
        if next_url and next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
        return redirect("job_detail", pk=pk)


class JobRetryView(LoginRequiredMixin, View):
    def post(self, request, pk):
        job = get_object_or_404(Job, pk=pk)
        try:
            new_job = retry_job(job)
            _log_audit(request, "job_retry", job.phase, {"old_job_id": job.pk, "new_job_id": new_job.pk})
            messages.success(request, f"Created retry job #{new_job.pk}")
            return redirect("job_detail", pk=new_job.pk)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect("job_detail", pk=pk)


class JobProgressPartial(HtmxLoginRequiredMixin, TemplateView):
    template_name = "pipeline/partials/job_progress.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        job = get_object_or_404(Job, pk=self.kwargs["pk"])
        ctx["job"] = job
        ctx["progress"] = _get_job_progress(job)
        return ctx


class JobLogPartial(HtmxLoginRequiredMixin, View):
    def get(self, request, pk):
        from pathlib import Path
        job = get_object_or_404(Job, pk=pk)
        current_host = _get_hostname()

        if job.host and job.host != current_host:
            if not job.log_file or not Path(job.log_file).exists():
                if job.log_tail:
                    return HttpResponse(
                        f'<pre class="text-xs bg-gray-900 text-green-400 p-4 rounded overflow-auto max-h-96">{_escape(job.log_tail)}</pre>'
                    )
                return HttpResponse(
                    f'<pre class="text-xs bg-gray-900 text-yellow-400 p-4 rounded">'
                    f'Log file is on remote host: {_escape(job.host)}\n'
                    f'Path: {_escape(job.log_file or "unknown")}</pre>'
                )

        content = read_log_tail(job.log_file)
        return HttpResponse(
            f'<pre class="text-xs bg-gray-900 text-green-400 p-4 rounded overflow-auto max-h-96">{_escape(content)}</pre>'
        )


def _escape(text):
    """HTML-escape text for safe rendering."""
    from django.utils.html import escape
    return escape(text)


def _get_job_progress(job):
    """Get live progress for a job based on its phase."""
    if job.status != "running":
        return {"current": job.progress_current, "total": job.progress_total}

    if job.phase == "seed":
        current = NonprofitSeed.objects.filter(state=job.state_code).count() if job.state_code else 0
        return {"current": current, "total": None}

    if job.phase == "resolve" and job.started_at and job.state_code:
        current = NonprofitSeed.objects.filter(
            state=job.state_code,
            resolver_updated_at__gte=job.started_at,
        ).count()
        total = job.progress_total
        return {"current": current, "total": total}

    if job.phase == "crawl":
        current = CrawledOrg.objects.count()
        total = job.progress_total
        return {"current": current, "total": total}

    if job.phase == "classify":
        current = Report.objects.filter(classification__isnull=False).count()
        total = job.progress_total
        return {"current": current, "total": total}

    return {"current": job.progress_current, "total": job.progress_total}


# ---------------------------------------------------------------------------
# Pipeline Controls
# ---------------------------------------------------------------------------

class ResolverView(LoginRequiredMixin, TemplateView):
    template_name = "pipeline/resolver.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        running = Job.objects.filter(phase="resolve", status="running").first()
        if running:
            running = _annotate_running_jobs([running])[0]
        ctx["running_job"] = running
        ctx["pending_job"] = Job.objects.filter(phase="resolve", status="pending").first()
        ctx["recent_results"] = NonprofitSeed.objects.filter(
            resolver_updated_at__isnull=False
        ).order_by("-resolver_updated_at")[:50]
        ctx["recent_jobs"] = _annotate_recent_jobs(
            Job.objects.filter(phase="resolve").order_by("-created_at")[:20]
        )
        resolve_stats = list(
            NonprofitSeed.objects.values("state")
            .annotate(
                total=Count("ein"),
                resolved=Count("ein", filter=models.Q(resolver_status="resolved")),
            )
            .order_by("state")
        )
        for stat in resolve_stats:
            stat["pct"] = round(stat["resolved"] / stat["total"] * 100) if stat["total"] > 0 else 0
        ctx["resolve_stats"] = resolve_stats
        from .forms import ResolverForm
        ctx["form"] = ResolverForm()
        ctx["dependency_choices"] = _get_dependency_choices()
        workers = _get_worker_choices()
        ctx["worker_choices"] = workers
        ctx["show_host_selector"] = len(workers) > 1
        return ctx


class CrawlerView(LoginRequiredMixin, TemplateView):
    template_name = "pipeline/crawler.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        try:
            ctx["process"] = check_process("crawl")
        except PipelineProcess.DoesNotExist:
            ctx["process"] = None
        running = Job.objects.filter(phase="crawl", status="running").first()
        if running:
            running = _annotate_running_jobs([running])[0]
        ctx["running_job"] = running
        ctx["recent_orgs"] = CrawledOrg.objects.order_by("-last_crawled_at")[:50]
        ctx["recent_reports"] = Report.objects.order_by("-archived_at")[:50]
        ctx["recent_jobs"] = _annotate_recent_jobs(
            Job.objects.filter(phase="crawl").order_by("-created_at")[:20]
        )
        from django.db import connections
        with connections["pipeline"].cursor() as cursor:
            cursor.execute("""
                SELECT s.state,
                       SUM(CASE WHEN s.resolver_status = 'resolved' THEN 1 ELSE 0 END) as resolved,
                       COUNT(DISTINCT co.ein) as crawled
                FROM nonprofits_seed s
                LEFT JOIN crawled_orgs co ON s.ein = co.ein
                WHERE s.resolver_status = 'resolved'
                GROUP BY s.state ORDER BY s.state
            """)
            crawl_stats = []
            for row in cursor.fetchall():
                pct = round(row[2] / row[1] * 100) if row[1] > 0 else 0
                crawl_stats.append({"state": row[0], "resolved": row[1], "crawled": row[2], "pct": pct})
        ctx["crawl_stats"] = crawl_stats
        from .forms import CrawlerForm, RunCrawlForm
        ctx["form"] = CrawlerForm()
        ctx["crawl_job_form"] = RunCrawlForm()
        ctx["dependency_choices"] = _get_dependency_choices()
        workers = _get_worker_choices()
        ctx["worker_choices"] = workers
        ctx["show_host_selector"] = len(workers) > 1
        return ctx


class ClassifierView(LoginRequiredMixin, TemplateView):
    template_name = "pipeline/classifier.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        try:
            ctx["process"] = check_process("classify")
        except PipelineProcess.DoesNotExist:
            ctx["process"] = None
        running = Job.objects.filter(phase="classify", status="running").first()
        if running:
            running = _annotate_running_jobs([running])[0]
        ctx["running_job"] = running
        ctx["recent_results"] = Report.objects.filter(
            classification__isnull=False
        ).order_by("-archived_at")[:50]
        ctx["recent_jobs"] = _annotate_recent_jobs(
            Job.objects.filter(phase="classify").order_by("-created_at")[:20]
        )
        from django.db import connections
        with connections["pipeline"].cursor() as cursor:
            cursor.execute("""
                SELECT s.state,
                       COUNT(DISTINCT c.content_sha256) as total_reports,
                       COUNT(DISTINCT CASE WHEN c.classification IS NOT NULL THEN c.content_sha256 END) as classified
                FROM nonprofits_seed s
                JOIN corpus c ON s.ein = c.source_org_ein
                GROUP BY s.state ORDER BY s.state
            """)
            classify_stats = []
            for row in cursor.fetchall():
                pct = round(row[2] / row[1] * 100) if row[1] > 0 else 0
                classify_stats.append({"state": row[0], "total_reports": row[1], "classified": row[2], "pct": pct})
        ctx["classify_stats"] = classify_stats
        from .forms import ClassifierForm
        ctx["form"] = ClassifierForm()
        ctx["dependency_choices"] = _get_dependency_choices()
        workers = _get_worker_choices()
        ctx["worker_choices"] = workers
        ctx["show_host_selector"] = len(workers) > 1
        return ctx


_V3_PHASES = ("extract-context", "reclassify", "compare-classify", "resolve-disagree", "promote-classify")


def _scan_v3_logs(limit=20):
    """List recent v3 pipeline log files from LOG_DIR, sorted by mtime."""
    import datetime as _dt
    from .orchestrator import LOG_DIR
    if not LOG_DIR.exists():
        return []
    tz = timezone.get_current_timezone()
    entries = []
    for phase in _V3_PHASES:
        for path in LOG_DIR.glob(f"{phase}_*.log"):
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append({
                "filename": path.name,
                "phase": phase,
                "mtime": _dt.datetime.fromtimestamp(stat.st_mtime, tz=tz),
                "size": stat.st_size,
            })
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries[:limit]


class ClassifierV3View(LoginRequiredMixin, TemplateView):
    template_name = "pipeline/classifier_v3.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from .forms import (
            CompareClassifyForm,
            ExtractContextForm,
            PromoteClassifyForm,
            ReclassifyForm,
            ResolveDisagreementsForm,
        )
        ctx["extract_form"] = ExtractContextForm()
        ctx["reclassify_form"] = ReclassifyForm()
        ctx["compare_form"] = CompareClassifyForm()
        ctx["resolve_form"] = ResolveDisagreementsForm()
        ctx["promote_form"] = PromoteClassifyForm()

        for phase in _V3_PHASES:
            key = phase.replace("-", "_")
            running = list(Job.objects.filter(phase=phase, status="running").order_by("state_code"))
            _annotate_running_jobs(running)
            _annotate_host_display(running)
            pending_count = Job.objects.filter(phase=phase, status="pending").count()
            recent = _annotate_recent_jobs(
                Job.objects.filter(phase=phase).order_by("-created_at")[:5]
            )
            _annotate_host_display(recent)
            ctx[f"{key}_running"] = running
            ctx[f"{key}_pending_count"] = pending_count
            ctx[f"{key}_recent"] = recent

        ctx["worker_choices"] = _get_worker_choices()
        ctx["dependency_choices"] = _get_dependency_choices()
        ctx["recent_logs"] = _scan_v3_logs()
        return ctx


class ProcessLogPartial(HtmxLoginRequiredMixin, View):
    def get(self, request, filename):
        from pathlib import Path
        from .orchestrator import LOG_DIR
        log_path = (LOG_DIR / filename).resolve()
        allowed_dir = LOG_DIR.resolve()
        if not str(log_path).startswith(str(allowed_dir) + "/"):
            return HttpResponse("forbidden", status=403)
        if not Path(log_path).exists():
            return HttpResponse(
                f'<pre class="text-xs bg-gray-900 text-yellow-400 p-4 rounded">[log not found: {_escape(filename)}]</pre>'
            )
        content = read_log_tail(str(log_path))
        return HttpResponse(
            f'<pre class="text-xs bg-gray-900 text-green-400 p-4 rounded overflow-auto max-h-96">{_escape(content)}</pre>'
        )


class ClassifierV3StatusPartial(HtmxLoginRequiredMixin, TemplateView):
    template_name = "pipeline/partials/classifier_v3_status.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from django.db import connections
        steps = []
        for phase, label in [
            ("extract-context", "Extract Context"),
            ("reclassify", "Reclassify"),
            ("compare-classify", "Compare"),
            ("resolve-disagree", "Resolve"),
            ("promote-classify", "Promote"),
        ]:
            running = list(Job.objects.filter(phase=phase, status="running").order_by("state_code"))
            _annotate_running_jobs(running)
            _annotate_host_display(running)
            pending_jobs = list(
                Job.objects.filter(phase=phase, status="pending")
                .select_related("depends_on")
                .order_by("created_at")
            )
            _annotate_host_display(pending_jobs)
            last_completed = Job.objects.filter(
                phase=phase, status__in=["completed", "failed"]
            ).order_by("-finished_at").first()
            steps.append({
                "phase": phase, "label": label,
                "running": running,
                "pending_count": len(pending_jobs),
                "pending_jobs": pending_jobs,
                "last_completed": last_completed,
                "last_duration": _job_duration(last_completed) if last_completed else None,
            })

        runs = []
        with connections["default"].cursor() as cur:
            cur.execute("""
                SELECT r.id, r.run_tag, r.started_at, r.finished_at,
                       (SELECT COUNT(*) FROM lava_corpus.classification_results
                        WHERE run_id = r.id) AS results
                FROM lava_corpus.classification_runs r
                ORDER BY r.id DESC LIMIT 5
            """)
            for row in cur.fetchall():
                runs.append({
                    "id": row[0], "run_tag": row[1],
                    "started_at": row[2], "finished_at": row[3],
                    "results": row[4],
                })
        ctx["steps"] = steps
        ctx["runs"] = runs
        ctx["recent_logs"] = _scan_v3_logs(limit=10)
        return ctx


_PHASE_LIST = [
    ("extract-context", "extracted", True),
    ("reclassify", "reclassified", True),
    ("compare-classify", None, False),
    ("resolve-disagree", None, False),
    ("promote-classify", "promoted", True),
]


def _v3_state_grid():
    from django.db import connections

    with connections["pipeline"].cursor() as cur:
        cur.execute("""
            SELECT
                s.state,
                COUNT(DISTINCT c.content_sha256) AS total_docs,
                COUNT(DISTINCT cc.content_sha256) AS extracted,
                COUNT(DISTINCT cr.content_sha256) AS reclassified,
                COUNT(DISTINCT CASE WHEN c.v3_material_type IS NOT NULL
                      THEN c.content_sha256 END) AS promoted
            FROM nonprofits_seed s
            LEFT JOIN corpus c
                ON s.ein = c.source_org_ein AND c.content_type = 'application/pdf'
            LEFT JOIN classification_context cc
                ON c.content_sha256 = cc.content_sha256
            LEFT JOIN classification_results cr
                ON c.content_sha256 = cr.content_sha256
                AND cr.run_id = (SELECT MAX(id) FROM classification_runs
                                 WHERE finished_at IS NOT NULL)
            GROUP BY s.state
            ORDER BY s.state
        """)
        columns = [col[0] for col in cur.description]
        state_rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    latest_per_phase_state = (
        Job.objects
        .filter(phase__in=_V3_PHASES)
        .exclude(status__in=["pending", "cancelled"])
        .values("phase", "state_code")
        .annotate(latest_id=Max("id"))
    )
    job_ids = [e["latest_id"] for e in latest_per_phase_state]
    jobs = Job.objects.filter(pk__in=job_ids).values("id", "phase", "state_code", "status")

    state_job_map = {}
    nationwide_job_map = {}
    for j in jobs:
        if j["state_code"] is None:
            nationwide_job_map[j["phase"]] = j["status"]
        else:
            state_job_map[(j["phase"], j["state_code"])] = j["status"]

    for state_row in state_rows:
        cells = []
        for phase, doc_key, is_pct in _PHASE_LIST:
            state_specific = state_job_map.get((phase, state_row["state"]))
            nationwide = nationwide_job_map.get(phase)
            job_status = state_specific if state_specific is not None else nationwide

            if job_status == "running":
                cells.append({"status": "running"})
            elif job_status == "failed":
                cells.append({"status": "failed"})
            elif is_pct:
                total = state_row["total_docs"]
                done = state_row.get(doc_key, 0)
                if total == 0:
                    cells.append({"status": "none"})
                else:
                    pct = round(done / total * 100)
                    if pct >= 100:
                        cells.append({"status": "complete", "pct": 100})
                    elif pct > 0:
                        cells.append({"status": "partial", "pct": pct})
                    else:
                        cells.append({"status": "none"})
            else:
                if job_status == "completed":
                    cells.append({"status": "complete"})
                else:
                    cells.append({"status": "none"})
        state_row["cells"] = cells

    def _action_score(row):
        statuses = [c["status"] for c in row["cells"]]
        if "failed" in statuses:
            return 0
        if "running" in statuses:
            return 1
        has_progress = any(s in ("complete", "partial") for s in statuses)
        has_none = any(s == "none" for s in statuses)
        if has_progress and has_none:
            return 2
        if has_progress:
            return 3
        return 4

    state_rows.sort(key=lambda r: (_action_score(r), -r["total_docs"]))
    return {"grid_rows": state_rows}


class ClassifierV3StateGridPartial(HtmxLoginRequiredMixin, TemplateView):
    template_name = "pipeline/partials/classifier_v3_state_grid.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(_v3_state_grid())
        return ctx


class ClassifierV3JobCreateView(LoginRequiredMixin, View):
    _FORM_MAP = {
        "extract-context": "ExtractContextForm",
        "reclassify": "ReclassifyForm",
        "compare-classify": "CompareClassifyForm",
        "resolve-disagree": "ResolveDisagreementsForm",
        "promote-classify": "PromoteClassifyForm",
    }

    def post(self, request):
        phase = request.POST.get("phase", "")
        if phase not in _V3_PHASES:
            messages.error(request, f"Invalid phase: {phase}")
            return redirect("classifier_v3")

        from . import forms
        form_cls = getattr(forms, self._FORM_MAP[phase])
        form = form_cls(request.POST)
        if not form.is_valid():
            messages.error(request, f"Invalid form: {form.errors.as_text()}")
            return redirect("classifier_v3")

        config = {k: v for k, v in form.cleaned_data.items() if v not in (None, "", False)}
        host = request.POST.get("host") or _get_hostname()

        depends_on = None
        depends_on_raw = request.POST.get("depends_on", "").strip()
        if depends_on_raw:
            try:
                dep_id = int(depends_on_raw)
                dep_job = Job.objects.get(pk=dep_id)
            except (ValueError, Job.DoesNotExist):
                messages.error(request, f"Dependency job #{depends_on_raw} not found")
                return redirect("classifier_v3")
            if dep_job.status in ("failed", "cancelled"):
                messages.error(
                    request,
                    f"Dependency job #{dep_id} is {dep_job.status} "
                    f"— cannot depend on a failed or cancelled job",
                )
                return redirect("classifier_v3")
            depends_on = dep_job

        try:
            job = create_v3_job(phase, config, host, depends_on=depends_on)
            _log_audit(request, "queue_v3", phase, config)
            state_label = config.get("state") or "nationwide"
            messages.success(request, f"Queued {phase} job #{job.pk} for {state_label}")
        except DuplicateJobError as e:
            messages.error(request, str(e))
        except InvalidParameterError as e:
            messages.error(request, str(e))

        return redirect("classifier_v3")


class ProcessStartView(LoginRequiredMixin, View):
    def post(self, request, phase):
        form_map = {
            "resolve": "ResolverForm",
            "crawl": "CrawlerForm",
            "classify": "ClassifierForm",
        }
        redirect_map = {
            "resolve": "resolver",
            "crawl": "crawler",
            "classify": "classifier",
        }
        if phase not in form_map:
            messages.error(request, f"Unknown phase: {phase}")
            return redirect("dashboard")

        from . import forms
        form_cls = getattr(forms, form_map[phase])
        form = form_cls(request.POST)
        if not form.is_valid():
            messages.error(request, f"Invalid form: {form.errors.as_text()}")
            return redirect(redirect_map[phase])

        config = {k: v for k, v in form.cleaned_data.items() if v not in (None, "", False)}
        try:
            start_process(phase, config)
            _log_audit(request, "start", phase, config)
            messages.success(request, f"Started {phase}")
        except RuntimeError as e:
            messages.error(request, str(e))

        return redirect(redirect_map[phase])


class ProcessStopView(LoginRequiredMixin, View):
    def post(self, request, phase):
        redirect_map = {
            "resolve": "resolver",
            "crawl": "crawler",
            "classify": "classifier",
        }
        try:
            stop_process(phase)
            _log_audit(request, "stop", phase)
            messages.success(request, f"Stopped {phase}")
        except Exception as e:
            messages.error(request, str(e))

        return redirect(redirect_map.get(phase, "dashboard"))


# ---------------------------------------------------------------------------
# Org Browser
# ---------------------------------------------------------------------------

class OrgListView(LoginRequiredMixin, ListView):
    model = NonprofitSeed
    template_name = "pipeline/orgs.html"
    context_object_name = "orgs"
    paginate_by = 50

    def get_queryset(self):
        qs = NonprofitSeed.objects.all().order_by("ein")
        ein = self.request.GET.get("ein")
        state = self.request.GET.get("state")
        status = self.request.GET.get("resolver_status")
        method = self.request.GET.get("resolver_method")
        if ein:
            qs = qs.filter(ein=ein.strip())
        if state:
            qs = qs.filter(state=state)
        if status:
            qs = qs.filter(resolver_status=status)
        if method:
            qs = qs.filter(resolver_method=method)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filter_ein"] = self.request.GET.get("ein", "")
        ctx["filter_state"] = self.request.GET.get("state", "")
        ctx["filter_status"] = self.request.GET.get("resolver_status", "")
        ctx["filter_method"] = self.request.GET.get("resolver_method", "")
        return ctx


class OrgDetailView(LoginRequiredMixin, DetailView):
    model = NonprofitSeed
    template_name = "pipeline/org_detail.html"
    context_object_name = "org"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ein = self.object.ein

        filings = FilingIndex.objects.using("pipeline").filter(
            ein=ein
        ).order_by("-tax_period", "-object_id")
        ctx["filings"] = filings

        selected_oid = self.request.GET.get("filing")
        selected = None
        if selected_oid:
            selected = filings.filter(object_id=selected_oid).first()
        if selected is None:
            selected = filings.first()
        ctx["selected_filing"] = selected

        if selected:
            people_qs = Person.objects.using("pipeline").filter(
                ein=ein, object_id=selected.object_id
            )
            ctx["officers"] = people_qs.exclude(
                person_type="contractor"
            ).order_by("-reportable_comp", "person_name")
            ctx["contractors"] = people_qs.filter(
                person_type="contractor"
            ).order_by("-reportable_comp")
            ctx["schedule_j"] = people_qs.filter(
                total_comp_sch_j__isnull=False
            ).order_by("-total_comp_sch_j")

        if filings.count() > 1:
            all_people = Person.objects.using("pipeline").filter(ein=ein)
            comparison = {}
            for p in all_people:
                row = comparison.setdefault(p.person_name, {})
                existing = row.get(p.object_id)
                if existing is None or (p.reportable_comp or 0) > (existing or 0):
                    row[p.object_id] = p.reportable_comp
            ctx["comparison"] = comparison
            tp_counts = {}
            for f in filings:
                tp_counts[f.tax_period] = tp_counts.get(f.tax_period, 0) + 1
            ctx["filing_headers"] = [
                {
                    "object_id": f.object_id,
                    "label": f.tax_period + (
                        " (amended)" if f.is_amended or tp_counts[f.tax_period] > 1
                        else ""
                    ),
                }
                for f in filings
            ]

        # Pipeline provenance status
        try:
            ctx["provenance"] = OrgProvenance.objects.get(ein=ein)
        except OrgProvenance.DoesNotExist:
            ctx["provenance"] = None

        return ctx


# ---------------------------------------------------------------------------
# 990 Pipeline Controls
# ---------------------------------------------------------------------------


class EnrichIndexView(LoginRequiredMixin, TemplateView):
    template_name = "pipeline/990_index.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        running = Job.objects.filter(phase="990-index", status="running").first()
        if running:
            running = _annotate_running_jobs([running])[0]
        ctx["running_job"] = running
        ctx["pending_job"] = Job.objects.filter(phase="990-index", status="pending").first()
        ctx["recent_jobs"] = _annotate_recent_jobs(
            Job.objects.filter(phase="990-index").order_by("-created_at")[:20]
        )
        from .forms import EnrichIndexForm
        ctx["form"] = EnrichIndexForm()
        workers = _get_worker_choices()
        ctx["worker_choices"] = workers
        ctx["show_host_selector"] = len(workers) > 1

        qs = FilingIndex.objects.using("pipeline").all()
        ein = self.request.GET.get("ein")
        if ein:
            qs = qs.filter(ein=ein)
        ctx["status_counts"] = list(qs.values("status").annotate(count=Count("status")))
        ctx["total_filings"] = qs.count()

        try:
            last_refresh = IndexRefreshLog.objects.using("pipeline").order_by(
                "-refreshed_at"
            ).first()
            ctx["last_refresh"] = last_refresh
        except Exception:
            ctx["last_refresh"] = None

        ctx["refresh_by_year"] = list(
            IndexRefreshLog.objects.using("pipeline")
            .values("filing_year")
            .annotate(
                last_refresh=models.Max("refreshed_at"),
                total_inserted=models.Sum("rows_inserted"),
            )
            .order_by("-filing_year")
        )
        return ctx


class EnrichIndexJobCreateView(LoginRequiredMixin, View):
    def post(self, request):
        from .forms import EnrichIndexForm
        form = EnrichIndexForm(request.POST)
        if not form.is_valid():
            messages.error(request, f"Invalid form: {form.errors.as_text()}")
            return redirect("enrich_index")

        config = {k: v for k, v in form.cleaned_data.items() if v not in (None, "", False)}
        host = request.POST.get("host", _get_hostname())
        try:
            job = create_990_index_job(config, host)
            _log_audit(request, "job_create", "990-index", {"job_id": job.pk, "host": host})
            messages.success(request, f"Created 990 index job #{job.pk}")
        except (DuplicateJobError, InvalidParameterError) as e:
            messages.error(request, str(e))

        params = {}
        if config.get("ein"):
            params["ein"] = config["ein"]
        if params:
            from urllib.parse import urlencode
            return redirect(f"{reverse('enrich_index')}?{urlencode(params)}")
        return redirect("enrich_index")


class EnrichParseView(LoginRequiredMixin, TemplateView):
    template_name = "pipeline/990_parse.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        running = Job.objects.filter(phase="990-parse", status="running").first()
        if running:
            running = _annotate_running_jobs([running])[0]
        ctx["running_job"] = running
        ctx["pending_job"] = Job.objects.filter(phase="990-parse", status="pending").first()
        ctx["recent_jobs"] = _annotate_recent_jobs(
            Job.objects.filter(phase="990-parse").order_by("-created_at")[:20]
        )
        from .forms import EnrichParseForm
        ctx["form"] = EnrichParseForm()
        workers = _get_worker_choices()
        ctx["worker_choices"] = workers
        ctx["show_host_selector"] = len(workers) > 1

        qs = FilingIndex.objects.using("pipeline").all()
        ein = self.request.GET.get("ein")
        if ein:
            qs = qs.filter(ein=ein)
        ctx["status_counts"] = list(qs.values("status").annotate(count=Count("status")))
        ctx["total_filings"] = qs.count()

        people_qs = Person.objects.using("pipeline").all()
        if ein:
            people_qs = people_qs.filter(ein=ein)
        ctx["people_count"] = people_qs.count()

        return ctx


class EnrichParseJobCreateView(LoginRequiredMixin, View):
    def post(self, request):
        from .forms import EnrichParseForm
        form = EnrichParseForm(request.POST)
        if not form.is_valid():
            messages.error(request, f"Invalid form: {form.errors.as_text()}")
            return redirect("enrich_parse")

        config = {k: v for k, v in form.cleaned_data.items() if v not in (None, "", False)}
        host = request.POST.get("host", _get_hostname())
        try:
            job = create_990_parse_job(config, host)
            _log_audit(request, "job_create", "990-parse", {"job_id": job.pk, "host": host})
            messages.success(request, f"Created 990 parse job #{job.pk}")
        except (DuplicateJobError, InvalidParameterError) as e:
            messages.error(request, str(e))

        params = {}
        if config.get("ein"):
            params["ein"] = config["ein"]
        if params:
            from urllib.parse import urlencode
            return redirect(f"{reverse('enrich_parse')}?{urlencode(params)}")
        return redirect("enrich_parse")


# ---------------------------------------------------------------------------
# Phone Enrichment
# ---------------------------------------------------------------------------


class PhoneEnrichView(LoginRequiredMixin, TemplateView):
    template_name = "pipeline/phone_enrich.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        running = Job.objects.filter(phase="enrich-phone", status="running").first()
        if running:
            running = _annotate_running_jobs([running])[0]
        ctx["running_job"] = running
        ctx["pending_job"] = Job.objects.filter(phase="enrich-phone", status="pending").first()
        from .forms import PhoneEnrichForm
        ctx["form"] = PhoneEnrichForm()
        workers = _get_worker_choices()
        ctx["worker_choices"] = workers
        ctx["show_host_selector"] = len(workers) > 1
        ctx["phone_count"] = NonprofitSeed.objects.exclude(phone__isnull=True).exclude(phone="").count()
        ctx["resolved_no_phone"] = NonprofitSeed.objects.filter(
            resolver_status="resolved", phone__isnull=True,
        ).count()
        ctx["recent_jobs"] = _annotate_recent_jobs(
            Job.objects.filter(phase="enrich-phone").order_by("-created_at")[:20]
        )
        phone_stats = list(
            NonprofitSeed.objects.filter(resolver_status="resolved")
            .values("state")
            .annotate(
                resolved=Count("ein"),
                has_phone=Count("ein", filter=models.Q(phone__isnull=False) & ~models.Q(phone="")),
            )
            .order_by("state")
        )
        for stat in phone_stats:
            stat["pct"] = round(stat["has_phone"] / stat["resolved"] * 100) if stat["resolved"] > 0 else 0
        ctx["phone_stats"] = phone_stats
        return ctx


class PhoneEnrichJobCreateView(LoginRequiredMixin, View):
    def post(self, request):
        from .forms import PhoneEnrichForm
        form = PhoneEnrichForm(request.POST)
        if not form.is_valid():
            messages.error(request, f"Invalid form: {form.errors.as_text()}")
            return redirect("phone_enrich")

        config = {k: v for k, v in form.cleaned_data.items() if v not in (None, "", False)}
        raw_engines = config.get("search_engines", "brave")
        config["search_engines"] = raw_engines.replace("_", ",")
        host = request.POST.get("host", _get_hostname())
        try:
            job = create_phone_enrich_job(config, host)
            _log_audit(request, "job_create", "enrich-phone", {"job_id": job.pk, "host": host})
            messages.success(request, f"Created phone enrich job #{job.pk}")
        except (DuplicateJobError, InvalidParameterError) as e:
            messages.error(request, str(e))

        return redirect("phone_enrich")


# ---------------------------------------------------------------------------
# Worker Management
# ---------------------------------------------------------------------------


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
            w.phases_display = (w.capabilities or {}).get("phases", [])
            w.notes_display = (w.capabilities or {}).get("notes", "")
        ctx["workers"] = workers
        return ctx


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


# ---------------------------------------------------------------------------
# Reports Browser
# ---------------------------------------------------------------------------

class ReportListView(LoginRequiredMixin, ListView):
    model = Report
    template_name = "pipeline/reports.html"
    context_object_name = "reports"
    paginate_by = 50

    def get_queryset(self):
        qs = Report.objects.all().order_by("-archived_at")
        org = self.request.GET.get("org")
        material_type = self.request.GET.get("material_type")
        year = self.request.GET.get("report_year")
        date_from = self.request.GET.get("date_from")
        date_to = self.request.GET.get("date_to")
        if org:
            matching_eins = NonprofitSeed.objects.filter(
                Q(ein__icontains=org) | Q(name__icontains=org)
            ).values_list("ein", flat=True)[:1000]
            qs = qs.filter(source_org_ein__in=matching_eins)
        if material_type:
            qs = qs.filter(material_type=material_type)
        if year:
            try:
                qs = qs.filter(report_year=int(year))
            except ValueError:
                pass
        if date_from:
            qs = qs.filter(archived_at__gte=date_from)
        if date_to:
            qs = qs.filter(archived_at__lte=date_to + "T23:59:59")
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filter_org"] = self.request.GET.get("org", "")
        ctx["filter_material_type"] = self.request.GET.get("material_type", "")
        ctx["filter_year"] = self.request.GET.get("report_year", "")
        ctx["filter_date_from"] = self.request.GET.get("date_from", "")
        ctx["filter_date_to"] = self.request.GET.get("date_to", "")
        ctx["material_type_choices"] = (
            Report.objects.filter(material_type__isnull=False)
            .values_list("material_type", flat=True)
            .distinct()
            .order_by("material_type")
        )
        return ctx


class ReportDetailView(LoginRequiredMixin, DetailView):
    model = Report
    template_name = "pipeline/report_detail.html"
    context_object_name = "report"
    slug_field = "content_sha256"
    slug_url_kwarg = "sha"


class ReportDownloadView(LoginRequiredMixin, View):
    def get(self, request, sha):
        report = get_object_or_404(Report, content_sha256=sha)

        import boto3
        from django.conf import settings

        s3 = boto3.client("s3")
        key = f"pdfs/{report.content_sha256}.pdf"
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.S3_COLLATERAL_BUCKET, "Key": key},
            ExpiresIn=300,
        )
        return HttpResponseRedirect(url)


class ProvenanceView(LoginRequiredMixin, ListView):
    model = OrgProvenance
    template_name = "pipeline/provenance.html"
    context_object_name = "orgs"
    paginate_by = 50

    def get_queryset(self):
        from datetime import timedelta
        from .stages import STAGE_REGISTRY
        from .orchestrator import US_STATES

        qs = OrgProvenance.objects.all()

        state = self.request.GET.get("state")
        if state and state in US_STATES:
            qs = qs.filter(ein__in=NonprofitSeed.objects.filter(state=state).values("ein"))

        stage_name = self.request.GET.get("stage")
        status_filter = self.request.GET.get("status")
        if stage_name and stage_name in STAGE_REGISTRY:
            stage = STAGE_REGISTRY[stage_name]
            if stage.provenance_column and status_filter:
                qs = qs.filter(**{stage.provenance_column: status_filter})

        stuck = self.request.GET.get("stuck")
        if stuck:
            from django.db.models import Q
            seven_days_ago = timezone.now() - timedelta(days=7)
            stuck_q = Q()
            for stage in STAGE_REGISTRY.values():
                col = stage.provenance_column
                if col:
                    stuck_q |= Q(**{col: "failed"})
                    stuck_q |= Q(**{col: "in_progress", "updated_at__lt": seven_days_ago})
            qs = qs.filter(stuck_q)

        return qs.order_by("-updated_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from .stages import STAGE_REGISTRY
        from .orchestrator import US_STATES

        ctx["provenance_stages"] = [
            s for s in STAGE_REGISTRY.values() if s.provenance_column
        ]
        ctx["state_choices"] = US_STATES
        # Preserve filter params for pagination links
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx["filter_params"] = params.urlencode()
        return ctx


# ---------------------------------------------------------------------------
# Control Panel
# ---------------------------------------------------------------------------


def _client_ip(request):
    return request.META.get("REMOTE_ADDR", "127.0.0.1")


class ControlPanelView(LoginRequiredMixin, TemplateView):
    template_name = "pipeline/control_panel.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["config"] = PipelineConfig.get()
        ctx["phases"] = [c[0] for c in Job.PHASE_CHOICES]
        ctx["states"] = list(
            Job.objects.filter(state_code__isnull=False)
            .values_list("state_code", flat=True).distinct().order_by("state_code")
        )
        ctx["hosts"] = list(
            Worker.objects.filter(is_active=True)
            .values_list("hostname", flat=True).order_by("hostname")
        )
        from .scheduler_config import load_config
        sched = load_config()
        ctx["scheduler_config"] = {
            "max_concurrent_heavy": sched.max_concurrent_heavy,
            "memory_ceiling_pct": sched.memory_ceiling_pct,
            "cpu_ceiling_pct": sched.cpu_ceiling_pct,
            "cool_down_after_failure_s": sched.cool_down_after_failure_s,
            "starvation_boost_after_minutes": sched.starvation_boost_after_minutes,
            "eta_lookahead": sched.eta_lookahead,
            "stage_weights": sched.stage_weights,
        }
        return ctx


class QueuePauseView(LoginRequiredMixin, View):
    def post(self, request):
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


class BulkCancelView(LoginRequiredMixin, View):
    def _build_queryset(self, request):
        phase = request.POST.get("phase") or None
        state = request.POST.get("state") or None
        host = request.POST.get("host") or None
        status_filter = request.POST.get("status") or "pending"
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
            count = qs.count()
            return render(request, "pipeline/partials/confirm_bulk_action.html", {
                "action": "cancel", "count": count, "filters": filters,
                "action_url": reverse("bulk_cancel"),
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
        msg = f"Cancelled {cancelled} of {len(jobs)} jobs"
        if skipped:
            msg += f" ({skipped} already completed)"
        messages.success(request, msg)
        return redirect("control_panel")


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

        filters = {"phase": phase, "state": state, "host": host}

        if not confirmed:
            return render(request, "pipeline/partials/confirm_bulk_action.html", {
                "action": "retry", "count": qs.count(), "filters": filters,
                "action_url": reverse("bulk_retry"),
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
            parameters={**filters, "retried": retried,
                        "skipped_existing_retry": skipped, "outcome": outcome},
            source_ip=_client_ip(request))
        msg = f"Retried {retried} jobs"
        if skipped:
            msg += f" (skipped {skipped} with existing retry)"
        messages.success(request, msg)
        return redirect("control_panel")


class ClearQueueView(LoginRequiredMixin, View):
    def post(self, request):
        pending = Job.objects.filter(status="pending")
        confirmed = request.POST.get("confirmed") == "1"

        if not confirmed:
            return render(request, "pipeline/partials/confirm_bulk_action.html", {
                "action": "clear_queue", "count": pending.count(), "filters": {},
                "action_url": reverse("clear_queue"),
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
        msg = f"Cancelled {cancelled} pending jobs"
        if skipped:
            msg += f" ({skipped} already changed)"
        messages.success(request, msg)
        return redirect("control_panel")


class HealthCheckPartial(HtmxLoginRequiredMixin, TemplateView):
    template_name = "pipeline/partials/control_health.html"

    def get_context_data(self, **kwargs):
        from datetime import timedelta
        ctx = super().get_context_data(**kwargs)
        ten_min_ago = timezone.now() - timedelta(minutes=10)
        offline_hosts = set(
            Worker.objects.filter(
                is_active=True, status__in=["stale", "offline"]
            ).values_list("hostname", flat=True)
        )
        stale_jobs = list(
            Job.objects.filter(status="running").filter(
                Q(last_heartbeat__lt=ten_min_ago) |
                Q(host__in=offline_hosts)
            )
        )
        ctx["stale_jobs"] = stale_jobs

        from django.db import connections
        try:
            with connections["default"].cursor() as cur:
                cur.execute("""
                    SELECT l.pid, a.client_addr, a.state, a.backend_start
                    FROM pg_locks l
                    JOIN pg_stat_activity a ON a.pid = l.pid
                    WHERE l.locktype = 'advisory' AND a.state = 'idle'
                      AND a.usename = current_user
                """)
                lock_rows = cur.fetchall()
        except Exception:
            lock_rows = []

        running_host_ips = set(
            Worker.objects.filter(
                hostname__in=Job.objects.filter(status="running")
                    .values_list("host", flat=True),
                is_active=True
            ).values_list("ip_address", flat=True)
        )
        running_host_ips_str = {str(ip) for ip in running_host_ips if ip}
        orphan_locks = []
        for pid, client_addr, state, backend_start in lock_rows:
            if str(client_addr) not in running_host_ips_str:
                orphan_locks.append({
                    "pid": pid, "client_addr": client_addr,
                    "backend_start": backend_start,
                })
        ctx["orphan_locks"] = orphan_locks
        return ctx


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


class HostStatusPartial(HtmxLoginRequiredMixin, TemplateView):
    template_name = "pipeline/partials/control_hosts.html"

    def get_context_data(self, **kwargs):
        import json
        ctx = super().get_context_data(**kwargs)
        workers = list(Worker.objects.filter(is_active=True).order_by("hostname"))
        now = timezone.now()
        for w in workers:
            w.running_count = Job.objects.filter(host=w.hostname, status="running").count()
            w.pending_count = Job.objects.filter(host=w.hostname, status="pending").count()
            if w.last_heartbeat:
                age = (now - w.last_heartbeat).total_seconds()
                w.heartbeat_stale = age > 60
                w.heartbeat_offline = age > 300
            else:
                w.heartbeat_stale = True
                w.heartbeat_offline = True
            latest_report = (
                HostCommand.objects.filter(
                    host=w.hostname, command="report-status", status="completed"
                ).order_by("-finished_at").first()
            )
            w.service_status = None
            w.disk_info = None
            if latest_report and latest_report.result_text:
                try:
                    data = json.loads(latest_report.result_text)
                    w.service_status = {
                        "orchestrator": data.get("lavandula-orchestrator", "unknown"),
                        "dashboard": data.get("lavandula-dashboard", "unknown"),
                    }
                    if "disk_used_pct" in data:
                        w.disk_info = {
                            "used_pct": data["disk_used_pct"],
                            "free_gb": data.get("disk_free_gb", "?"),
                        }
                except (json.JSONDecodeError, ValueError):
                    pass
        ctx["workers"] = workers
        return ctx


class HostCommandView(LoginRequiredMixin, View):
    def post(self, request, hostname):
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

        if worker.status == "offline" and command != "report-status":
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
        messages.success(request, f"Command '{valid_commands[command]}' sent to {hostname}")
        return redirect("control_panel")


class HostCommandStatusPartial(HtmxLoginRequiredMixin, TemplateView):
    template_name = "pipeline/partials/host_command_status.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        hostname = self.kwargs["hostname"]
        recent_commands = list(
            HostCommand.objects.filter(host=hostname)
            .order_by("-created_at")[:10]
        )
        ctx["commands"] = recent_commands
        ctx["hostname"] = hostname
        return ctx

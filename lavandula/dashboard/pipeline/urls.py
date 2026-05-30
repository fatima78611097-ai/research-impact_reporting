from django.urls import path

from . import views

urlpatterns = [
    # Dashboard
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("stats/", views.DashboardStatsPartial.as_view(), name="dashboard_stats"),

    # Seeder
    path("seeder/", views.SeederView.as_view(), name="seeder"),
    path("seeder/queue/", views.JobCreateView.as_view(), name="job_create"),

    # Jobs
    path("jobs/", views.JobListView.as_view(), name="job_list"),
    path("jobs/<int:pk>/", views.JobDetailView.as_view(), name="job_detail"),
    path("jobs/<int:pk>/cancel/", views.JobCancelView.as_view(), name="job_cancel"),
    path("jobs/<int:pk>/retry/", views.JobRetryView.as_view(), name="job_retry"),
    path("jobs/<int:pk>/progress/", views.JobProgressPartial.as_view(), name="job_progress"),
    path("jobs/<int:pk>/log/", views.JobLogPartial.as_view(), name="job_log"),

    # Pipeline Controls
    path("resolver/", views.ResolverView.as_view(), name="resolver"),
    path("resolver/queue/", views.ResolveJobCreateView.as_view(), name="resolve_job_create"),
    path("crawler/", views.CrawlerView.as_view(), name="crawler"),
    path("crawler/queue/", views.CrawlJobCreateView.as_view(), name="crawl_job_create"),
    path("classifier/", views.ClassifierView.as_view(), name="classifier"),
    path("classifier/queue/", views.ClassifyJobCreateView.as_view(), name="classify_job_create"),

    # Classifier v3 Pipeline
    path("classifier-v3/", views.ClassifierV3View.as_view(), name="classifier_v3"),
    path("classifier-v3/status/", views.ClassifierV3StatusPartial.as_view(), name="classifier_v3_status"),
    path("classifier-v3/queue/", views.ClassifierV3JobCreateView.as_view(), name="classifier_v3_job_create"),
    path("classifier-v3/state-grid/", views.ClassifierV3StateGridPartial.as_view(), name="classifier_v3_state_grid"),
    path("classifier-v3/log/<str:filename>/", views.ProcessLogPartial.as_view(), name="process_log_partial"),

    # Phone Enrichment
    path("phone-enrich/", views.PhoneEnrichView.as_view(), name="phone_enrich"),
    path("phone-enrich/queue/", views.PhoneEnrichJobCreateView.as_view(), name="phone_enrich_job_create"),

    # Parse (Docling GPU)
    path("parse/", views.ParseView.as_view(), name="parse"),
    path("parse/queue/", views.ParseJobCreateView.as_view(), name="parse_job_create"),
    path("parse/progress/", views.ParseProgressPartial.as_view(), name="parse_progress"),
    path("parse/stop/", views.ParseStopView.as_view(), name="parse_stop"),

    # 990 Pipeline Controls
    path("990-index/", views.EnrichIndexView.as_view(), name="enrich_index"),
    path("990-index/queue/", views.EnrichIndexJobCreateView.as_view(), name="enrich_index_job_create"),
    path("990-parse/", views.EnrichParseView.as_view(), name="enrich_parse"),
    path("990-parse/queue/", views.EnrichParseJobCreateView.as_view(), name="enrich_parse_job_create"),

    path("process/<str:phase>/start/", views.ProcessStartView.as_view(), name="process_start"),
    path("process/<str:phase>/stop/", views.ProcessStopView.as_view(), name="process_stop"),

    # Workers
    path("workers/", views.WorkerListView.as_view(), name="worker_list"),
    path("workers/<int:pk>/edit/", views.WorkerEditView.as_view(), name="worker_edit"),

    # Org Browser
    path("orgs/", views.OrgListView.as_view(), name="org_list"),
    path("orgs/<str:pk>/", views.OrgDetailView.as_view(), name="org_detail"),

    # Provenance
    path("provenance/", views.ProvenanceView.as_view(), name="provenance"),

    # Reports Browser
    path("reports/", views.ReportListView.as_view(), name="report_list"),
    path("reports/<str:sha>/", views.ReportDetailView.as_view(), name="report_detail"),
    path("reports/<str:sha>/download/", views.ReportDownloadView.as_view(), name="report_download"),
    path("reports/<str:sha>/pdf/", views.ReportPdfProxyView.as_view(), name="report_pdf"),

    # Extraction QA Viewer (Spec 0052)
    path("reports/<str:sha>/qa/", views.ExtractionQAView.as_view(), name="extraction_qa"),
    path("orgs/<str:ein>/qa/", views.OrgExtractionQARedirectView.as_view(), name="org_extraction_qa"),

    # LLM Impact Extraction (Spec 0051)
    path("llm-extract/", views.LlmExtractView.as_view(), name="llm_extract"),
    path("llm-extract/queue/", views.LlmExtractJobCreateView.as_view(), name="llm_extract_job_create"),
    path("llm-extract/status/", views.LlmExtractStatusPartial.as_view(), name="llm_extract_status"),
    path("llm-extract/stop/", views.LlmExtractStopView.as_view(), name="llm_extract_stop"),

    # Faithfulness Verification Gate (Spec 0057)
    path("faithfulness/", views.FaithfulnessView.as_view(), name="faithfulness"),
    path("faithfulness/verify/", views.FaithfulnessVerifyView.as_view(), name="faithfulness_verify"),
    path("faithfulness/status/", views.FaithfulnessStatusPartial.as_view(), name="faithfulness_status"),

    # pdftotext Backfill & Re-verify (Spec 0060)
    path("faithfulness/pdftotext-backfill/", views.PdftextBackfillView.as_view(), name="pdftotext_backfill"),
    path("faithfulness/pdftotext-status/", views.PdftextBackfillStatusPartial.as_view(), name="pdftotext_backfill_status"),

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
]

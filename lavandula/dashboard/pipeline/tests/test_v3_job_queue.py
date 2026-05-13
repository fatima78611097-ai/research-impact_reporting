"""Tests for Spec 0040: Classifier V3 Job Queue Integration."""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from pipeline.models import Job, PipelineProcess, Worker
from pipeline.orchestrator import (
    COMMAND_MAP,
    DuplicateJobError,
    InvalidParameterError,
    _PER_STATE_PHASES,
    _V3_PHASES,
    build_argv,
    cancel_job,
    create_v3_job,
    get_eligible_jobs,
    retry_job,
)
from pipeline.stages import STAGE_REGISTRY, validate_registry


# ---------------------------------------------------------------------------
# Registry & Model
# ---------------------------------------------------------------------------

class StageRegistryV3Test(TestCase):
    def test_all_v3_phases_in_registry(self):
        for phase in _V3_PHASES:
            self.assertIn(phase, STAGE_REGISTRY, f"{phase} missing from STAGE_REGISTRY")

    def test_validate_registry_passes(self):
        errors = validate_registry()
        self.assertEqual(errors, [], f"Registry validation errors: {errors}")

    def test_conflict_groups_per_state(self):
        for phase in _V3_PHASES:
            self.assertEqual(
                STAGE_REGISTRY[phase].conflict_group, "per-state",
                f"{phase} should use per-state conflict group",
            )

    def test_resource_classes(self):
        self.assertEqual(STAGE_REGISTRY["extract-context"].resource_class, "heavy")
        self.assertEqual(STAGE_REGISTRY["reclassify"].resource_class, "heavy")
        self.assertEqual(STAGE_REGISTRY["compare-classify"].resource_class, "light")
        self.assertEqual(STAGE_REGISTRY["resolve-disagree"].resource_class, "medium")
        self.assertEqual(STAGE_REGISTRY["promote-classify"].resource_class, "light")

    def test_predecessors(self):
        self.assertEqual(STAGE_REGISTRY["extract-context"].predecessors, ["crawl"])
        self.assertEqual(STAGE_REGISTRY["reclassify"].predecessors, ["extract-context"])
        self.assertEqual(STAGE_REGISTRY["compare-classify"].predecessors, ["reclassify"])
        self.assertEqual(STAGE_REGISTRY["resolve-disagree"].predecessors, ["compare-classify"])
        self.assertEqual(STAGE_REGISTRY["promote-classify"].predecessors, ["resolve-disagree"])

    def test_reclassify_no_where_param(self):
        self.assertNotIn("where", STAGE_REGISTRY["reclassify"].parameters)


class PhaseChoicesV3Test(TestCase):
    def test_all_v3_phases_in_choices(self):
        phase_keys = {c[0] for c in Job.PHASE_CHOICES}
        for phase in _V3_PHASES:
            self.assertIn(phase, phase_keys, f"{phase} missing from PHASE_CHOICES")


# ---------------------------------------------------------------------------
# COMMAND_MAP
# ---------------------------------------------------------------------------

class CommandMapV3Test(TestCase):
    def test_reclassify_no_where(self):
        self.assertNotIn("where", COMMAND_MAP["reclassify"]["params"])

    def test_compare_has_state(self):
        self.assertIn("state", COMMAND_MAP["compare-classify"]["params"])

    def test_promote_has_state(self):
        self.assertIn("state", COMMAND_MAP["promote-classify"]["params"])

    def test_build_argv_extract_context(self):
        argv = build_argv("extract-context", {"state": "TX", "limit": 100})
        self.assertIn("--state", argv)
        self.assertIn("TX", argv)
        self.assertIn("--limit", argv)

    def test_build_argv_reclassify_rejects_where(self):
        with self.assertRaises(InvalidParameterError):
            build_argv("reclassify", {"run_tag": "test1", "where": "1=1"})


# ---------------------------------------------------------------------------
# _PER_STATE_PHASES
# ---------------------------------------------------------------------------

class PerStatePhasesV3Test(TestCase):
    def test_v3_phases_in_per_state(self):
        for phase in _V3_PHASES:
            self.assertIn(phase, _PER_STATE_PHASES, f"{phase} missing from _PER_STATE_PHASES")


# ---------------------------------------------------------------------------
# create_v3_job()
# ---------------------------------------------------------------------------

class CreateV3JobTest(TestCase):
    def test_creates_all_phases(self):
        for phase in _V3_PHASES:
            config = {"state": "TX"}
            if phase in ("reclassify", "compare-classify", "resolve-disagree", "promote-classify"):
                config["run_tag"] = "test-run"
            job = create_v3_job(phase, config, "localhost")
            self.assertEqual(job.phase, phase)
            self.assertEqual(job.status, "pending")
            self.assertEqual(job.state_code, "TX")

    def test_per_state_conflict_pending(self):
        create_v3_job("reclassify", {"state": "TX", "run_tag": "r1"}, "localhost")
        with self.assertRaises(DuplicateJobError):
            create_v3_job("reclassify", {"state": "TX", "run_tag": "r2"}, "localhost")

    def test_per_state_conflict_scheduled(self):
        job = create_v3_job("reclassify", {"state": "TX", "run_tag": "r1"}, "localhost")
        job.status = "scheduled"
        job.save()
        with self.assertRaises(DuplicateJobError):
            create_v3_job("reclassify", {"state": "TX", "run_tag": "r2"}, "localhost")

    def test_per_state_conflict_running(self):
        job = create_v3_job("reclassify", {"state": "TX", "run_tag": "r1"}, "localhost")
        job.status = "running"
        job.save()
        with self.assertRaises(DuplicateJobError):
            create_v3_job("reclassify", {"state": "TX", "run_tag": "r2"}, "localhost")

    def test_different_states_ok(self):
        j1 = create_v3_job("reclassify", {"state": "TX", "run_tag": "r1"}, "localhost")
        j2 = create_v3_job("reclassify", {"state": "CA", "run_tag": "r2"}, "localhost")
        self.assertEqual(j1.state_code, "TX")
        self.assertEqual(j2.state_code, "CA")

    def test_nationwide_and_per_state_ok(self):
        j1 = create_v3_job("extract-context", {}, "localhost")
        j2 = create_v3_job("extract-context", {"state": "TX"}, "localhost")
        self.assertIsNone(j1.state_code)
        self.assertEqual(j2.state_code, "TX")

    def test_invalid_phase_rejected(self):
        with self.assertRaises(InvalidParameterError):
            create_v3_job("seed", {}, "localhost")

    def test_run_tag_mismatch_rejected(self):
        upstream = create_v3_job("extract-context", {"state": "TX"}, "localhost")
        upstream.status = "completed"
        upstream.config_json = {"run_tag": "tag-A"}
        upstream.save()
        with self.assertRaises(InvalidParameterError) as ctx:
            create_v3_job(
                "reclassify",
                {"state": "TX", "run_tag": "tag-B"},
                "localhost",
                depends_on=upstream,
            )
        self.assertIn("run_tag mismatch", str(ctx.exception))

    def test_run_tag_match_ok(self):
        upstream = create_v3_job("extract-context", {"state": "TX"}, "localhost")
        upstream.config_json = {"run_tag": "tag-A"}
        upstream.save()
        job = create_v3_job(
            "reclassify",
            {"state": "TX", "run_tag": "tag-A"},
            "localhost",
            depends_on=upstream,
        )
        self.assertEqual(job.depends_on, upstream)

    def test_host_validation_rejects_invalid(self):
        Worker.objects.create(hostname="cloud2", status="online", is_active=True)
        with self.assertRaises(InvalidParameterError):
            create_v3_job("extract-context", {"state": "TX"}, "nonexistent-host")

    def test_completed_does_not_block(self):
        j1 = create_v3_job("extract-context", {"state": "TX"}, "localhost")
        j1.status = "completed"
        j1.save()
        j2 = create_v3_job("extract-context", {"state": "TX"}, "localhost")
        self.assertEqual(j2.status, "pending")


# ---------------------------------------------------------------------------
# Dependency validation
# ---------------------------------------------------------------------------

class V3DependencyValidationTest(TestCase):
    def test_invalid_predecessor_phase(self):
        upstream = Job.objects.create(
            phase="compare-classify", status="pending", host="localhost",
            state_code="TX", config_json={"run_tag": "r1"},
        )
        with self.assertRaises(InvalidParameterError) as ctx:
            create_v3_job(
                "reclassify", {"state": "TX", "run_tag": "r1"},
                "localhost", depends_on=upstream,
            )
        self.assertIn("cannot depend on", str(ctx.exception))

    def test_valid_predecessor_phase(self):
        upstream = Job.objects.create(
            phase="extract-context", status="pending", host="localhost",
            state_code="TX",
        )
        job = create_v3_job(
            "reclassify", {"state": "TX", "run_tag": "r1"},
            "localhost", depends_on=upstream,
        )
        self.assertEqual(job.depends_on, upstream)

    def test_nationwide_depends_on_per_state_rejected(self):
        upstream = Job.objects.create(
            phase="reclassify", status="pending", host="localhost",
            state_code="TX", config_json={"run_tag": "r1"},
        )
        with self.assertRaises(InvalidParameterError) as ctx:
            create_v3_job(
                "compare-classify", {"run_tag": "r1"},
                "localhost", depends_on=upstream,
            )
        self.assertIn("Nationwide", str(ctx.exception))

    def test_per_state_depends_on_nationwide_ok(self):
        upstream = Job.objects.create(
            phase="extract-context", status="pending", host="localhost",
        )
        job = create_v3_job(
            "reclassify", {"state": "TX", "run_tag": "r1"},
            "localhost", depends_on=upstream,
        )
        self.assertEqual(job.state_code, "TX")

    def test_state_mismatch_rejected(self):
        upstream = Job.objects.create(
            phase="extract-context", status="pending", host="localhost",
            state_code="TX",
        )
        with self.assertRaises(InvalidParameterError) as ctx:
            create_v3_job(
                "reclassify", {"state": "CA", "run_tag": "r1"},
                "localhost", depends_on=upstream,
            )
        self.assertIn("State mismatch", str(ctx.exception))


# ---------------------------------------------------------------------------
# get_eligible_jobs() with v3 phases
# ---------------------------------------------------------------------------

class GetEligibleJobsV3Test(TestCase):
    def test_v3_job_eligible(self):
        Job.objects.create(
            phase="extract-context", status="pending", host="localhost", state_code="TX",
        )
        eligible = get_eligible_jobs("localhost")
        self.assertEqual(eligible.count(), 1)

    def test_same_phase_same_state_blocks(self):
        Job.objects.create(
            phase="reclassify", status="running", host="localhost", state_code="TX",
        )
        Job.objects.create(
            phase="reclassify", status="pending", host="localhost", state_code="TX",
        )
        eligible = get_eligible_jobs("localhost")
        self.assertEqual(eligible.count(), 0)

    def test_same_phase_different_state_ok(self):
        Job.objects.create(
            phase="reclassify", status="running", host="localhost", state_code="TX",
        )
        Job.objects.create(
            phase="reclassify", status="pending", host="localhost", state_code="CA",
        )
        eligible = get_eligible_jobs("localhost")
        self.assertEqual(eligible.count(), 1)
        self.assertEqual(eligible.first().state_code, "CA")


# ---------------------------------------------------------------------------
# View tests
# ---------------------------------------------------------------------------

class ClassifierV3JobCreateViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("testuser", password="testpassword1234")
        self.client = Client()
        self.client.login(username="testuser", password="testpassword1234")

    def test_valid_post_creates_job(self):
        resp = self.client.post(reverse("classifier_v3_job_create"), {
            "phase": "extract-context",
            "state": "TX",
            "limit": 100,
        })
        self.assertEqual(resp.status_code, 302)
        job = Job.objects.filter(phase="extract-context").first()
        self.assertIsNotNone(job)
        self.assertEqual(job.state_code, "TX")
        self.assertEqual(job.status, "pending")

    def test_invalid_phase_rejected(self):
        resp = self.client.post(reverse("classifier_v3_job_create"), {
            "phase": "seed",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Job.objects.filter(phase="seed").count(), 0)

    def test_invalid_depends_on_rejected(self):
        resp = self.client.post(reverse("classifier_v3_job_create"), {
            "phase": "extract-context",
            "depends_on": "99999",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Job.objects.filter(phase="extract-context").count(), 0)

    def test_completed_depends_on_accepted(self):
        dep = Job.objects.create(
            phase="extract-context", status="completed", host="localhost",
        )
        resp = self.client.post(reverse("classifier_v3_job_create"), {
            "phase": "reclassify",
            "run_tag": "test1",
            "backend": "deepseek",
            "depends_on": str(dep.pk),
        })
        self.assertEqual(resp.status_code, 302)
        job = Job.objects.filter(phase="reclassify").first()
        self.assertIsNotNone(job)
        self.assertEqual(job.depends_on, dep)

    def test_failed_depends_on_rejected(self):
        dep = Job.objects.create(
            phase="extract-context", status="failed", host="localhost",
        )
        resp = self.client.post(reverse("classifier_v3_job_create"), {
            "phase": "reclassify",
            "run_tag": "test1",
            "backend": "deepseek",
            "depends_on": str(dep.pk),
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Job.objects.filter(phase="reclassify").count(), 0)

    def test_cancelled_depends_on_rejected(self):
        dep = Job.objects.create(
            phase="extract-context", status="cancelled", host="localhost",
        )
        resp = self.client.post(reverse("classifier_v3_job_create"), {
            "phase": "reclassify",
            "run_tag": "test1",
            "backend": "deepseek",
            "depends_on": str(dep.pk),
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Job.objects.filter(phase="reclassify").count(), 0)

    def test_duplicate_returns_error(self):
        self.client.post(reverse("classifier_v3_job_create"), {
            "phase": "extract-context",
            "state": "TX",
        })
        resp = self.client.post(reverse("classifier_v3_job_create"), {
            "phase": "extract-context",
            "state": "TX",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Job.objects.filter(phase="extract-context", state_code="TX").count(), 1)

    def test_requires_auth(self):
        self.client.logout()
        resp = self.client.post(reverse("classifier_v3_job_create"), {
            "phase": "extract-context",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp.url)


class ProcessStartRejectsV3Test(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("testuser", password="testpassword1234")
        self.client = Client()
        self.client.login(username="testuser", password="testpassword1234")

    def test_reclassify_rejected(self):
        resp = self.client.post(reverse("process_start", args=["reclassify"]), {
            "run_tag": "test",
            "backend": "deepseek",
        })
        self.assertEqual(resp.status_code, 302)

    def test_extract_context_rejected(self):
        resp = self.client.post(reverse("process_start", args=["extract-context"]), {})
        self.assertEqual(resp.status_code, 302)

    def test_promote_classify_rejected(self):
        resp = self.client.post(reverse("process_start", args=["promote-classify"]), {
            "run_tag": "test",
        })
        self.assertEqual(resp.status_code, 302)


# ---------------------------------------------------------------------------
# Cancel / Retry for v3 jobs
# ---------------------------------------------------------------------------

class V3CancelRetryTest(TestCase):
    def test_cancel_cascades_v3_chain(self):
        extract = Job.objects.create(
            phase="extract-context", status="pending", host="localhost", state_code="TX",
        )
        reclassify = Job.objects.create(
            phase="reclassify", status="pending", host="localhost",
            state_code="TX", depends_on=extract,
        )
        cancel_job(extract)
        extract.refresh_from_db()
        reclassify.refresh_from_db()
        self.assertEqual(extract.status, "cancelled")
        self.assertEqual(reclassify.status, "cancelled")

    def test_retry_v3_job(self):
        job = Job.objects.create(
            phase="reclassify", status="failed", host="localhost",
            state_code="TX", config_json={"run_tag": "r1"},
        )
        new_job = retry_job(job)
        self.assertEqual(new_job.status, "pending")
        self.assertEqual(new_job.phase, "reclassify")
        self.assertEqual(new_job.retry_of, job)
        self.assertEqual(new_job.attempt_number, 2)


# ---------------------------------------------------------------------------
# Cleanup command
# ---------------------------------------------------------------------------

class CleanupV3PipelineProcessesTest(TestCase):
    def test_dead_pid_marked_stopped(self):
        proc = PipelineProcess.objects.create(
            name="reclassify", status="running", pid=999999,
        )
        from django.core.management import call_command
        call_command("cleanup_v3_pipeline_processes")
        proc.refresh_from_db()
        self.assertEqual(proc.status, "stopped")

    def test_non_running_ignored(self):
        proc = PipelineProcess.objects.create(
            name="reclassify", status="stopped",
        )
        from django.core.management import call_command
        call_command("cleanup_v3_pipeline_processes")
        proc.refresh_from_db()
        self.assertEqual(proc.status, "stopped")


# ---------------------------------------------------------------------------
# Spec 0041: check_phase_conflict with scheduled status
# ---------------------------------------------------------------------------

from pipeline.orchestrator import check_phase_conflict


class CheckPhaseConflictScheduledTest(TestCase):
    def test_scheduled_triggers_conflict(self):
        Job.objects.create(phase="crawl", status="scheduled", host="localhost")
        self.assertTrue(check_phase_conflict("crawl"))

    def test_scheduled_per_state_same_state(self):
        Job.objects.create(
            phase="extract-context", status="scheduled",
            state_code="TX", host="localhost",
        )
        self.assertTrue(check_phase_conflict("extract-context", "TX"))

    def test_scheduled_per_state_different_state(self):
        Job.objects.create(
            phase="extract-context", status="scheduled",
            state_code="TX", host="localhost",
        )
        self.assertFalse(check_phase_conflict("extract-context", "CA"))

    def test_no_conflict_when_only_completed(self):
        Job.objects.create(phase="crawl", status="completed", host="localhost")
        self.assertFalse(check_phase_conflict("crawl"))


# ---------------------------------------------------------------------------
# Spec 0041: JobCancelView redirect with next parameter
# ---------------------------------------------------------------------------

class JobCancelViewRedirectTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("testuser", password="testpassword1234")
        self.client = Client()
        self.client.login(username="testuser", password="testpassword1234")

    def test_cancel_redirects_to_next(self):
        job = Job.objects.create(
            phase="extract-context", status="pending", host="localhost",
        )
        resp = self.client.post(
            f"/pipeline/jobs/{job.pk}/cancel/?next=/pipeline/classifier-v3/"
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/pipeline/classifier-v3/")

    def test_cancel_rejects_absolute_next(self):
        job = Job.objects.create(
            phase="extract-context", status="pending", host="localhost",
        )
        resp = self.client.post(
            f"/pipeline/jobs/{job.pk}/cancel/?next=https://evil.com/"
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn(f"/pipeline/jobs/{job.pk}/", resp.url)

    def test_cancel_rejects_protocol_relative_next(self):
        job = Job.objects.create(
            phase="extract-context", status="pending", host="localhost",
        )
        resp = self.client.post(
            f"/pipeline/jobs/{job.pk}/cancel/?next=//evil.com/"
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn(f"/pipeline/jobs/{job.pk}/", resp.url)

    def test_cancel_without_next_goes_to_job_detail(self):
        job = Job.objects.create(
            phase="extract-context", status="pending", host="localhost",
        )
        resp = self.client.post(f"/pipeline/jobs/{job.pk}/cancel/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn(f"/pipeline/jobs/{job.pk}/", resp.url)

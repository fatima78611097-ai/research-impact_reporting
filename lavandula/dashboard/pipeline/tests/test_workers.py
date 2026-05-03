from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from pipeline.models import Job, PipelineProcess, Worker
from pipeline.orchestrator import (
    InvalidParameterError,
    _validate_host,
    check_phase_conflict,
    create_crawl_job,
    create_resolve_job,
)


class PhaseConflictTest(TestCase):

    def test_same_state_conflict(self):
        Job.objects.create(phase="resolve", state_code="CA", status="running", host="h1")
        self.assertTrue(check_phase_conflict("resolve", "CA"))

    def test_different_state_no_conflict(self):
        Job.objects.create(phase="resolve", state_code="CA", status="running", host="h1")
        self.assertFalse(check_phase_conflict("resolve", "NY"))

    def test_null_state_global_conflict(self):
        Job.objects.create(phase="resolve", state_code="CA", status="running", host="h1")
        self.assertTrue(check_phase_conflict("resolve", None))

    def test_crawl_always_global(self):
        Job.objects.create(phase="crawl", state_code=None, status="running", host="h1")
        self.assertTrue(check_phase_conflict("crawl", None))

    def test_crawl_no_running(self):
        self.assertFalse(check_phase_conflict("crawl", None))

    def test_adhoc_process_blocks(self):
        PipelineProcess.objects.create(name="resolve", status="running", pid=12345)
        self.assertTrue(check_phase_conflict("resolve", "CA"))

    def test_seed_different_state_no_conflict(self):
        Job.objects.create(phase="seed", state_code="TX", status="running", host="h1")
        self.assertFalse(check_phase_conflict("seed", "NY"))

    def test_classify_different_state_no_conflict(self):
        Job.objects.create(phase="classify", state_code="CA", status="running", host="h1")
        self.assertFalse(check_phase_conflict("classify", "NY"))

    def test_enrich_phone_different_state_no_conflict(self):
        Job.objects.create(phase="enrich-phone", state_code="CA", status="running", host="h1")
        self.assertFalse(check_phase_conflict("enrich-phone", "NY"))

    def test_990_index_always_global(self):
        Job.objects.create(phase="990-index", state_code=None, status="running", host="h1")
        self.assertTrue(check_phase_conflict("990-index", None))

    def test_completed_job_no_conflict(self):
        Job.objects.create(phase="resolve", state_code="CA", status="completed", host="h1")
        self.assertFalse(check_phase_conflict("resolve", "CA"))


class WorkerModelTest(TestCase):

    def test_update_or_create_on_restart(self):
        Worker.objects.create(hostname="h1", status="offline")
        Worker.objects.update_or_create(
            hostname="h1", defaults={"status": "online"}
        )
        self.assertEqual(Worker.objects.filter(hostname="h1").count(), 1)
        self.assertEqual(Worker.objects.get(hostname="h1").status, "online")

    def test_soft_delete_preserves_row(self):
        w = Worker.objects.create(hostname="h1", is_active=True)
        w.is_active = False
        w.save()
        self.assertEqual(Worker.objects.filter(is_active=True).count(), 0)
        self.assertTrue(Worker.objects.filter(hostname="h1").exists())

    def test_hostname_unique(self):
        Worker.objects.create(hostname="h1")
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Worker.objects.create(hostname="h1")

    def test_str_with_display_name(self):
        w = Worker(hostname="h1", display_name="GPU Box", status="online")
        self.assertEqual(str(w), "GPU Box (online)")

    def test_str_without_display_name(self):
        w = Worker(hostname="h1", status="offline")
        self.assertEqual(str(w), "h1 (offline)")


class WorkerHealthTransitionTest(TestCase):

    def _run_health_check(self):
        from pipeline.management.commands.run_orchestrator import (
            HEARTBEAT_OFFLINE_THRESHOLD,
            HEARTBEAT_STALE_THRESHOLD,
        )
        now = timezone.now()
        stale_cutoff = now - timedelta(seconds=HEARTBEAT_STALE_THRESHOLD)
        offline_cutoff = now - timedelta(seconds=HEARTBEAT_OFFLINE_THRESHOLD)

        Worker.objects.filter(
            is_active=True, status="stale", last_heartbeat__lt=offline_cutoff,
        ).update(status="offline")

        Worker.objects.filter(
            is_active=True, status="online", last_heartbeat__lt=stale_cutoff,
        ).update(status="stale")

    def test_online_to_stale(self):
        Worker.objects.create(
            hostname="h1", status="online", is_active=True,
            last_heartbeat=timezone.now() - timedelta(seconds=400),
        )
        self._run_health_check()
        self.assertEqual(Worker.objects.get(hostname="h1").status, "stale")

    def test_stale_to_offline(self):
        Worker.objects.create(
            hostname="h1", status="stale", is_active=True,
            last_heartbeat=timezone.now() - timedelta(seconds=2000),
        )
        self._run_health_check()
        self.assertEqual(Worker.objects.get(hostname="h1").status, "offline")

    def test_stale_recovery(self):
        Worker.objects.create(
            hostname="h1", status="stale", is_active=True,
            last_heartbeat=timezone.now() - timedelta(seconds=400),
        )
        Worker.objects.filter(hostname="h1").update(
            status="online", last_heartbeat=timezone.now(),
        )
        self.assertEqual(Worker.objects.get(hostname="h1").status, "online")

    def test_inactive_worker_not_checked(self):
        Worker.objects.create(
            hostname="h1", status="online", is_active=False,
            last_heartbeat=timezone.now() - timedelta(seconds=400),
        )
        self._run_health_check()
        self.assertEqual(Worker.objects.get(hostname="h1").status, "online")

    def test_recent_heartbeat_stays_online(self):
        Worker.objects.create(
            hostname="h1", status="online", is_active=True,
            last_heartbeat=timezone.now() - timedelta(seconds=10),
        )
        self._run_health_check()
        self.assertEqual(Worker.objects.get(hostname="h1").status, "online")


class ValidateHostTest(TestCase):

    def test_unknown_host_raises(self):
        Worker.objects.create(hostname="h1", status="online", is_active=True)
        with self.assertRaises(InvalidParameterError):
            _validate_host("unknown-host")

    def test_offline_host_raises(self):
        Worker.objects.create(hostname="h1", status="offline", is_active=True)
        with self.assertRaises(InvalidParameterError):
            _validate_host("h1")

    def test_stale_host_raises(self):
        Worker.objects.create(hostname="h1", status="stale", is_active=True)
        with self.assertRaises(InvalidParameterError):
            _validate_host("h1")

    def test_online_host_passes(self):
        Worker.objects.create(hostname="h1", status="online", is_active=True)
        self.assertEqual(_validate_host("h1"), "h1")

    def test_inactive_host_raises(self):
        Worker.objects.create(hostname="h1", status="online", is_active=False)
        with self.assertRaises(InvalidParameterError):
            _validate_host("h1")


class HostValidationInCreateJobTest(TestCase):

    def test_no_workers_skips_validation(self):
        self.assertEqual(Worker.objects.count(), 0)
        job = create_resolve_job({"state": "CA"}, "any-host")
        self.assertEqual(job.host, "any-host")

    def test_with_workers_validates(self):
        Worker.objects.create(hostname="good-host", status="online", is_active=True)
        with self.assertRaises(InvalidParameterError):
            create_resolve_job({"state": "CA"}, "bad-host")

    def test_with_workers_online_host_passes(self):
        Worker.objects.create(hostname="good-host", status="online", is_active=True)
        job = create_resolve_job({"state": "CA"}, "good-host")
        self.assertEqual(job.host, "good-host")

    def test_crawl_validates_host(self):
        Worker.objects.create(hostname="good-host", status="online", is_active=True)
        with self.assertRaises(InvalidParameterError):
            create_crawl_job({}, "bad-host")

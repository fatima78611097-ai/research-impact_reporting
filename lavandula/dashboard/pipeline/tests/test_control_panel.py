from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from pipeline.models import (
    HostCommand,
    Job,
    PipelineAuditLog,
    PipelineConfig,
    Worker,
)


class ControlPanelTestBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("testuser", password="testpassword1234")
        self.client = Client()
        self.client.login(username="testuser", password="testpassword1234")


class PipelineConfigSingletonTest(TestCase):
    def test_get_creates_singleton(self):
        config = PipelineConfig.get()
        self.assertEqual(config.pk, 1)
        self.assertFalse(config.queue_paused)

    def test_get_returns_same_row(self):
        c1 = PipelineConfig.get()
        c2 = PipelineConfig.get()
        self.assertEqual(c1.pk, c2.pk)


class QueuePauseResumeTest(ControlPanelTestBase):
    def test_queue_pause(self):
        resp = self.client.post(reverse("queue_pause"))
        self.assertEqual(resp.status_code, 302)
        config = PipelineConfig.get()
        self.assertTrue(config.queue_paused)
        self.assertEqual(config.paused_by, "testuser")
        self.assertIsNotNone(config.paused_at)

    def test_queue_resume(self):
        config = PipelineConfig.get()
        config.queue_paused = True
        config.paused_by = "someone"
        config.save()

        resp = self.client.post(reverse("queue_resume"))
        self.assertEqual(resp.status_code, 302)
        config.refresh_from_db()
        self.assertFalse(config.queue_paused)
        self.assertEqual(config.paused_by, "")

    def test_pause_blocks_scheduling(self):
        config = PipelineConfig.get()
        config.queue_paused = True
        config.save()
        self.assertTrue(PipelineConfig.get().queue_paused)

    def test_pause_audit_log(self):
        self.client.post(reverse("queue_pause"))
        self.assertTrue(PipelineAuditLog.objects.filter(action="queue_pause").exists())

    def test_resume_audit_log(self):
        self.client.post(reverse("queue_resume"))
        self.assertTrue(PipelineAuditLog.objects.filter(action="queue_resume").exists())


class BulkCancelTest(ControlPanelTestBase):
    def test_bulk_cancel_preview_shows_count(self):
        Job.objects.create(phase="seed", state_code="TX", status="pending", host="h1")
        Job.objects.create(phase="seed", state_code="CA", status="pending", host="h1")
        resp = self.client.post(reverse("bulk_cancel"), {
            "phase": "seed", "status": "pending",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "2 job")
        self.assertEqual(Job.objects.filter(status="pending").count(), 2)

    def test_bulk_cancel_by_phase(self):
        Job.objects.create(phase="seed", state_code="TX", status="pending", host="h1")
        Job.objects.create(phase="seed", state_code="TX", status="pending", host="h1")
        Job.objects.create(phase="resolve", state_code="TX", status="pending", host="h1")
        Job.objects.create(phase="seed", state_code="CA", status="pending", host="h1")

        resp = self.client.post(reverse("bulk_cancel"), {
            "phase": "seed", "status": "pending", "confirmed": "1",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Job.objects.filter(status="cancelled", phase="seed").count(), 3)
        self.assertEqual(Job.objects.filter(status="pending", phase="resolve").count(), 1)

    def test_bulk_cancel_cascade(self):
        a = Job.objects.create(phase="seed", state_code="TX", status="pending", host="h1")
        b = Job.objects.create(phase="resolve", state_code="TX", status="pending", host="h1", depends_on=a)
        c = Job.objects.create(phase="crawl", state_code="TX", status="pending", host="h1", depends_on=b)

        self.client.post(reverse("bulk_cancel"), {
            "phase": "seed", "status": "pending", "confirmed": "1",
        })
        for job in [a, b, c]:
            job.refresh_from_db()
        self.assertEqual(a.status, "cancelled")
        self.assertEqual(b.status, "cancelled")
        self.assertEqual(c.status, "cancelled")

    def test_bulk_cancel_excludes_running_by_default(self):
        Job.objects.create(phase="seed", status="pending", host="h1")
        Job.objects.create(phase="seed", status="running", host="h1")
        self.client.post(reverse("bulk_cancel"), {
            "phase": "seed", "status": "pending", "confirmed": "1",
        })
        self.assertEqual(Job.objects.filter(status="cancelled").count(), 1)
        self.assertEqual(Job.objects.filter(status="running").count(), 1)

    def test_bulk_cancel_includes_running_when_selected(self):
        Job.objects.create(phase="seed", status="pending", host="h1")
        Job.objects.create(phase="seed", status="running", host="h1")
        self.client.post(reverse("bulk_cancel"), {
            "phase": "seed", "status": "pending+running", "confirmed": "1",
        })
        self.assertEqual(Job.objects.filter(status="cancelled").count(), 2)

    def test_bulk_cancel_partial_success(self):
        j1 = Job.objects.create(phase="seed", status="pending", host="h1")
        j2 = Job.objects.create(phase="seed", status="pending", host="h1")
        j3 = Job.objects.create(phase="seed", status="pending", host="h1")
        # Complete one between preview and confirm
        j2.status = "completed"
        j2.save(update_fields=["status"])

        resp = self.client.post(reverse("bulk_cancel"), {
            "phase": "seed", "status": "pending", "confirmed": "1",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Job.objects.filter(status="cancelled").count(), 2)

    def test_bulk_cancel_audit_log_success(self):
        Job.objects.create(phase="seed", status="pending", host="h1")
        self.client.post(reverse("bulk_cancel"), {
            "phase": "seed", "status": "pending", "confirmed": "1",
        })
        log = PipelineAuditLog.objects.filter(action="bulk_cancel").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.parameters["outcome"], "success")


class BulkRetryTest(ControlPanelTestBase):
    def test_bulk_retry(self):
        Job.objects.create(phase="seed", state_code="TX", status="failed", host="h1")
        Job.objects.create(phase="seed", state_code="CA", status="failed", host="h1")
        Job.objects.create(phase="seed", state_code="NY", status="failed", host="h1")
        self.client.post(reverse("bulk_retry"), {"confirmed": "1"})
        self.assertEqual(Job.objects.filter(status="pending").count(), 3)
        self.assertTrue(
            all(j.retry_of_id is not None for j in Job.objects.filter(status="pending"))
        )

    def test_bulk_retry_skips_existing(self):
        j1 = Job.objects.create(phase="seed", status="failed", host="h1")
        Job.objects.create(phase="seed", status="pending", host="h1", retry_of=j1)
        j2 = Job.objects.create(phase="seed", status="failed", host="h1")

        self.client.post(reverse("bulk_retry"), {"confirmed": "1"})
        new_pending = Job.objects.filter(status="pending", retry_of=j2)
        self.assertEqual(new_pending.count(), 1)
        log = PipelineAuditLog.objects.filter(action="bulk_retry").first()
        self.assertEqual(log.parameters["skipped_existing_retry"], 1)

    def test_bulk_retry_preview(self):
        Job.objects.create(phase="seed", status="failed", host="h1")
        resp = self.client.post(reverse("bulk_retry"), {})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "1 failed job")


class ClearQueueTest(ControlPanelTestBase):
    def test_clear_queue(self):
        for i in range(5):
            Job.objects.create(phase="seed", status="pending", host="h1")
        Job.objects.create(phase="seed", status="running", host="h1")
        Job.objects.create(phase="seed", status="running", host="h1")

        self.client.post(reverse("clear_queue"), {"confirmed": "1"})
        self.assertEqual(Job.objects.filter(status="cancelled").count(), 5)
        self.assertEqual(Job.objects.filter(status="running").count(), 2)

    def test_clear_queue_preview(self):
        Job.objects.create(phase="seed", status="pending", host="h1")
        resp = self.client.post(reverse("clear_queue"), {})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "1 pending job")


class StaleJobDetectionTest(ControlPanelTestBase):
    def test_stale_job_detected_by_heartbeat(self):
        Job.objects.create(
            phase="seed", status="running", host="h1",
            last_heartbeat=timezone.now() - timedelta(minutes=15),
        )
        resp = self.client.get(
            reverse("control_health"),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["stale_jobs"]), 1)

    def test_stale_job_detected_by_offline_host(self):
        Worker.objects.create(hostname="h1", status="offline", is_active=True)
        Job.objects.create(
            phase="seed", status="running", host="h1",
            last_heartbeat=timezone.now(),
        )
        resp = self.client.get(
            reverse("control_health"),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["stale_jobs"]), 1)

    def test_fix_stale_marks_failed(self):
        job = Job.objects.create(
            phase="seed", status="running", host="h1",
            last_heartbeat=timezone.now() - timedelta(minutes=15),
        )
        self.client.post(reverse("fix_stale_jobs"), {"job_ids": [str(job.pk)]})
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIn("stale", job.error_message)

    def test_fix_stale_cascades(self):
        job = Job.objects.create(phase="seed", status="running", host="h1")
        dep = Job.objects.create(phase="resolve", status="pending", host="h1", depends_on=job)
        self.client.post(reverse("fix_stale_jobs"), {"job_ids": [str(job.pk)]})
        dep.refresh_from_db()
        self.assertEqual(dep.status, "cancelled")


class ReleaseLocksTest(ControlPanelTestBase):
    @patch("django.db.connections")
    def test_release_locks_calls_terminate(self, mock_conns):
        mock_cursor = mock_conns.__getitem__.return_value.cursor.return_value.__enter__.return_value
        mock_cursor.fetchone.return_value = (True,)

        self.client.post(reverse("release_locks"), {"pids": ["12345"]})
        mock_cursor.execute.assert_called_with(
            "SELECT pg_terminate_backend(%s)", [12345]
        )
        log = PipelineAuditLog.objects.filter(action="release_locks").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.parameters["released"], 1)


class HostCommandCreationTest(ControlPanelTestBase):
    def setUp(self):
        super().setUp()
        self.worker = Worker.objects.create(
            hostname="cloud1", status="online", is_active=True
        )

    def test_create_restart_orchestrator(self):
        resp = self.client.post(
            reverse("host_command", kwargs={"hostname": "cloud1"}),
            {"command": "restart-orchestrator"},
        )
        self.assertEqual(resp.status_code, 302)
        cmd = HostCommand.objects.first()
        self.assertEqual(cmd.host, "cloud1")
        self.assertEqual(cmd.command, "restart-orchestrator")
        self.assertEqual(cmd.requested_by, self.user)

    def test_invalid_command_rejected(self):
        self.client.post(
            reverse("host_command", kwargs={"hostname": "cloud1"}),
            {"command": "rm-rf"},
        )
        self.assertEqual(HostCommand.objects.count(), 0)
        log = PipelineAuditLog.objects.filter(action="host_command").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.parameters["outcome"], "denied")

    def test_invalid_host_rejected(self):
        self.client.post(
            reverse("host_command", kwargs={"hostname": "unknown-host"}),
            {"command": "restart-orchestrator"},
        )
        self.assertEqual(HostCommand.objects.count(), 0)
        log = PipelineAuditLog.objects.filter(action="host_command").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.parameters["reason"], "unknown host")

    def test_offline_host_blocked(self):
        self.worker.status = "offline"
        self.worker.save()
        resp = self.client.post(
            reverse("host_command", kwargs={"hostname": "cloud1"}),
            {"command": "restart-orchestrator"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(HostCommand.objects.count(), 0)

    def test_kill_process_validates_args(self):
        resp = self.client.post(
            reverse("host_command", kwargs={"hostname": "cloud1"}),
            {"command": "kill-process"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(HostCommand.objects.count(), 0)

    def test_report_status_allowed_for_offline_host(self):
        self.worker.status = "offline"
        self.worker.save()
        self.client.post(
            reverse("host_command", kwargs={"hostname": "cloud1"}),
            {"command": "report-status"},
        )
        self.assertEqual(HostCommand.objects.count(), 1)


class HostCommandAuditTest(ControlPanelTestBase):
    def setUp(self):
        super().setUp()
        Worker.objects.create(hostname="cloud1", status="online", is_active=True)

    def test_submitted_command_logged(self):
        self.client.post(
            reverse("host_command", kwargs={"hostname": "cloud1"}),
            {"command": "report-status"},
        )
        log = PipelineAuditLog.objects.filter(action="host_command").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.parameters["outcome"], "submitted")


class PauseBannerContextProcessorTest(ControlPanelTestBase):
    def test_banner_visible_when_paused(self):
        config = PipelineConfig.get()
        config.queue_paused = True
        config.paused_by = "testuser"
        config.paused_at = timezone.now()
        config.save()

        resp = self.client.get(reverse("job_list"), HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context.get("queue_paused"))

    def test_banner_not_visible_when_running(self):
        PipelineConfig.get()  # ensure row exists
        resp = self.client.get(reverse("job_list"), HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context.get("queue_paused"))


class ControlPanelAccessTest(TestCase):
    def test_requires_login(self):
        client = Client()
        resp = client.get(reverse("control_panel"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp.url)


class ControlPanelRenderTest(ControlPanelTestBase):
    def test_control_panel_renders(self):
        resp = self.client.get(reverse("control_panel"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Control Panel")

    def test_context_keys(self):
        resp = self.client.get(reverse("control_panel"))
        self.assertIn("config", resp.context)
        self.assertIn("phases", resp.context)
        self.assertIn("scheduler_config", resp.context)


class HtmxPartialsTest(ControlPanelTestBase):
    def test_health_partial_is_fragment(self):
        resp = self.client.get(
            reverse("control_health"),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "<html")

    def test_hosts_partial_is_fragment(self):
        resp = self.client.get(
            reverse("control_hosts"),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "<html")

    def test_host_command_status_partial_is_fragment(self):
        Worker.objects.create(hostname="cloud1", status="online", is_active=True)
        resp = self.client.get(
            reverse("host_command_status", kwargs={"hostname": "cloud1"}),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "<html")


class HostStatusPartialTest(ControlPanelTestBase):
    def test_shows_workers(self):
        Worker.objects.create(
            hostname="cloud1", status="online", is_active=True,
            cpu_pct=50.0, mem_pct=60.0,
            last_heartbeat=timezone.now(),
        )
        resp = self.client.get(
            reverse("control_hosts"),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["workers"]), 1)

    def test_heartbeat_freshness(self):
        Worker.objects.create(
            hostname="stale-host", status="online", is_active=True,
            last_heartbeat=timezone.now() - timedelta(minutes=3),
        )
        resp = self.client.get(
            reverse("control_hosts"),
            HTTP_HX_REQUEST="true",
        )
        w = resp.context["workers"][0]
        self.assertTrue(w.heartbeat_stale)
        self.assertFalse(w.heartbeat_offline)


class CommandTimeoutTest(TestCase):
    def test_pending_command_timeout(self):
        from pipeline.management.commands.run_orchestrator import Command as OrchestratorCommand
        cmd = HostCommand.objects.create(
            host="testhost", command="report-status", status="pending",
        )
        # Backdate created_at
        HostCommand.objects.filter(pk=cmd.pk).update(
            created_at=timezone.now() - timedelta(seconds=90)
        )
        orch = OrchestratorCommand()
        orch.hostname = "testhost"
        orch._expire_stale_commands()
        cmd.refresh_from_db()
        self.assertEqual(cmd.status, "failed")
        self.assertIn("pending for > 60 seconds", cmd.result_text)

    def test_running_command_timeout(self):
        from pipeline.management.commands.run_orchestrator import Command as OrchestratorCommand
        cmd = HostCommand.objects.create(
            host="testhost", command="report-status", status="running",
            started_at=timezone.now() - timedelta(seconds=150),
        )
        orch = OrchestratorCommand()
        orch.hostname = "testhost"
        orch._expire_stale_commands()
        cmd.refresh_from_db()
        self.assertEqual(cmd.status, "failed")
        self.assertIn("running for > 120 seconds", cmd.result_text)

    def test_restart_orchestrator_excluded_from_running_timeout(self):
        from pipeline.management.commands.run_orchestrator import Command as OrchestratorCommand
        cmd = HostCommand.objects.create(
            host="testhost", command="restart-orchestrator", status="running",
            started_at=timezone.now() - timedelta(seconds=150),
        )
        orch = OrchestratorCommand()
        orch.hostname = "testhost"
        orch._expire_stale_commands()
        cmd.refresh_from_db()
        # Should NOT be expired — handled by _check_restart_recovery
        self.assertEqual(cmd.status, "running")


class CommandClaimingTest(TestCase):
    def test_claims_one_at_a_time(self):
        from pipeline.management.commands.run_orchestrator import Command as OrchestratorCommand
        HostCommand.objects.create(
            host="testhost", command="report-status", status="pending",
        )
        HostCommand.objects.create(
            host="testhost", command="report-status", status="pending",
        )
        orch = OrchestratorCommand()
        orch.hostname = "testhost"

        with patch.object(orch, '_execute_host_command', return_value="ok"):
            with patch.object(orch, '_expire_stale_commands'):
                with patch.object(orch, '_check_restart_recovery'):
                    orch._poll_host_commands()

        # One should be completed, one still pending
        self.assertEqual(HostCommand.objects.filter(status="completed").count(), 1)
        self.assertEqual(HostCommand.objects.filter(status="pending").count(), 1)


class SanitizeResultTest(TestCase):
    def test_redacts_aws_key(self):
        from pipeline.management.commands.run_orchestrator import _sanitize_result
        text = "key=AKIAIOSFODNN7EXAMPLE"
        result = _sanitize_result(text)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", result)

    def test_redacts_password(self):
        from pipeline.management.commands.run_orchestrator import _sanitize_result
        text = "password=mysecret123"
        result = _sanitize_result(text)
        self.assertNotIn("mysecret123", result)

    def test_truncates_to_4096(self):
        from pipeline.management.commands.run_orchestrator import _sanitize_result
        text = "x" * 10000
        result = _sanitize_result(text)
        self.assertEqual(len(result), 4096)

    def test_redacts_home_dir(self):
        from pipeline.management.commands.run_orchestrator import _sanitize_result
        text = "path is /home/ubuntu/.ssh/id_rsa"
        result = _sanitize_result(text)
        self.assertNotIn("/home/ubuntu", result)


class CSRFControlPanelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("testuser", password="testpassword1234")
        self.client = Client(enforce_csrf_checks=True)
        self.client.login(username="testuser", password="testpassword1234")

    def test_queue_pause_requires_csrf(self):
        resp = self.client.post(reverse("queue_pause"))
        self.assertEqual(resp.status_code, 403)

    def test_bulk_cancel_requires_csrf(self):
        resp = self.client.post(reverse("bulk_cancel"), {"confirmed": "1"})
        self.assertEqual(resp.status_code, 403)

    def test_host_command_requires_csrf(self):
        Worker.objects.create(hostname="cloud1", status="online", is_active=True)
        resp = self.client.post(
            reverse("host_command", kwargs={"hostname": "cloud1"}),
            {"command": "report-status"},
        )
        self.assertEqual(resp.status_code, 403)

"""Tests for Spec 0054: Parse Dashboard (Docling GPU Orchestration)."""
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

from pipeline.forms import ParseRunForm
from pipeline.models import Job
from pipeline.orchestrator import (
    DuplicateJobError,
    InvalidParameterError,
    build_argv,
    create_parse_job,
)
from pipeline.stages import STAGE_REGISTRY, validate_registry


def _mock_cursor_no_rows():
    """Return a context manager mock for connections["default"].cursor()
    where execute is a no-op and fetchone returns None."""
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = None
    mock_cur.fetchall.return_value = []
    mock_cur.__enter__ = lambda self: self
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    return mock_conn, mock_cur


# ---------------------------------------------------------------------------
# Unit: Stage registry
# ---------------------------------------------------------------------------

class TestParseStageRegistry(SimpleTestCase):
    def test_parse_registered(self):
        self.assertIn("parse", STAGE_REGISTRY)

    def test_registry_still_valid(self):
        errors = validate_registry()
        self.assertEqual(errors, [], f"Registry validation errors: {errors}")

    def test_parse_stage_fields(self):
        stage = STAGE_REGISTRY["parse"]
        self.assertEqual(stage.name, "parse")
        self.assertEqual(stage.display_name, "Docling Parse")
        self.assertEqual(stage.conflict_group, "global")
        self.assertEqual(stage.resource_class, "light")
        self.assertEqual(stage.retry_policy.max_attempts, 1)

    def test_parse_run_tag_positional(self):
        stage = STAGE_REGISTRY["parse"]
        self.assertEqual(stage.parameters["run_tag"].cli_flag, "positional")
        self.assertTrue(stage.parameters["run_tag"].required)


# ---------------------------------------------------------------------------
# Unit: build_argv for parse
# ---------------------------------------------------------------------------

class TestBuildArgvParse(SimpleTestCase):
    def test_minimal(self):
        argv = build_argv("parse", {"run_tag": "test-run"})
        self.assertEqual(argv[:3], ["python3", "lavandula/dashboard/manage.py", "parse_documents"])
        self.assertEqual(argv[-1], "test-run")

    def test_positional_at_end(self):
        argv = build_argv("parse", {"run_tag": "alpha", "ntee": "B%", "max_hours": 6})
        self.assertEqual(argv[-1], "alpha")
        self.assertIn("--ntee", argv)
        self.assertIn("B%", argv)
        self.assertIn("--max-hours", argv)
        self.assertIn("6", argv)

    def test_bool_flag(self):
        argv = build_argv("parse", {"run_tag": "test", "no_spot": True})
        self.assertIn("--no-spot", argv)
        self.assertEqual(argv[-1], "test")

    def test_bool_false_omitted(self):
        argv = build_argv("parse", {"run_tag": "test", "retry_errors": False})
        self.assertNotIn("--retry-errors", argv)

    def test_ami_id(self):
        argv = build_argv("parse", {"run_tag": "r1", "ami_id": "ami-0123456789abcdef0"})
        self.assertIn("--ami-id", argv)
        self.assertIn("ami-0123456789abcdef0", argv)

    def test_start_at(self):
        argv = build_argv("parse", {"run_tag": "r1", "start_at": "2026-06-01T08:00"})
        self.assertIn("--start-at", argv)
        self.assertIn("2026-06-01T08:00", argv)

    def test_unknown_param_rejected(self):
        with self.assertRaises(InvalidParameterError):
            build_argv("parse", {"run_tag": "x", "bogus": "val"})

    def test_bad_run_tag_rejected(self):
        with self.assertRaises(InvalidParameterError):
            build_argv("parse", {"run_tag": "has spaces"})

    def test_instance_type_choice(self):
        argv = build_argv("parse", {"run_tag": "t", "instance_type": "g6.4xlarge"})
        self.assertIn("--instance-type", argv)
        self.assertIn("g6.4xlarge", argv)

    def test_instance_type_bad_choice(self):
        with self.assertRaises(InvalidParameterError):
            build_argv("parse", {"run_tag": "t", "instance_type": "p4d.24xlarge"})

    def test_capacity_wait_hours(self):
        argv = build_argv("parse", {"run_tag": "t", "capacity_wait_hours": 3})
        self.assertIn("--capacity-wait-hours", argv)
        self.assertIn("3", argv)

    def test_capacity_wait_hours_out_of_range(self):
        with self.assertRaises(InvalidParameterError):
            build_argv("parse", {"run_tag": "t", "capacity_wait_hours": 99})


# ---------------------------------------------------------------------------
# Unit: ParseRunForm validation
# ---------------------------------------------------------------------------

class TestParseRunForm(SimpleTestCase):
    def _form(self, **overrides):
        data = {
            "run_tag": "test-run",
            "priority": "annual,impact",
            "instance_type": "g6.2xlarge",
            "max_hours": 24,
            "batch_size": 500,
            "workers": 1,
        }
        data.update(overrides)
        return ParseRunForm(data)

    def test_valid_minimal(self):
        self.assertTrue(self._form().is_valid())

    def test_run_tag_empty(self):
        self.assertFalse(self._form(run_tag="").is_valid())

    def test_run_tag_bad_chars(self):
        self.assertFalse(self._form(run_tag="has spaces!").is_valid())

    def test_ntee_valid(self):
        form = self._form(ntee="B%")
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["ntee"], "B%")

    def test_ntee_lowercase_rejected(self):
        self.assertFalse(self._form(ntee="b%").is_valid())

    def test_ntee_blank_becomes_none(self):
        form = self._form(ntee="")
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.cleaned_data["ntee"])

    def test_ami_id_valid(self):
        form = self._form(ami_id="ami-0123456789abcdef0")
        self.assertTrue(form.is_valid())

    def test_ami_id_bad_format(self):
        self.assertFalse(self._form(ami_id="ami-SHORT").is_valid())

    def test_ami_id_blank_ok(self):
        form = self._form(ami_id="")
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.cleaned_data["ami_id"])

    def test_max_hours_range(self):
        self.assertFalse(self._form(max_hours=0).is_valid())
        self.assertFalse(self._form(max_hours=25).is_valid())
        self.assertTrue(self._form(max_hours=12).is_valid())


# ---------------------------------------------------------------------------
# Unit: create_parse_job (mock raw SQL to lava_parse.parse_runs)
# ---------------------------------------------------------------------------

class TestCreateParseJob(TestCase):
    def _create(self, config, host="cloud2"):
        mock_conn, mock_cur = _mock_cursor_no_rows()
        with patch("django.db.connections") as mock_conns:
            mock_conns.__getitem__ = lambda self, key: mock_conn
            return create_parse_job(config, host)

    def test_creates_pending_job(self):
        job = self._create({"run_tag": "test1"})
        self.assertEqual(job.phase, "parse")
        self.assertEqual(job.status, "pending")
        self.assertIsNone(job.state_code)
        self.assertEqual(job.host, "cloud2")
        self.assertEqual(job.config_json["run_tag"], "test1")

    def test_scheduled_when_start_at(self):
        job = self._create({"run_tag": "sched1", "start_at": "2026-06-01T08:00"})
        self.assertEqual(job.status, "scheduled")

    def test_duplicate_active_rejected(self):
        self._create({"run_tag": "dup1"})
        with self.assertRaises(DuplicateJobError):
            self._create({"run_tag": "dup2"})

    def test_completed_does_not_block(self):
        job = self._create({"run_tag": "done1"})
        job.status = "completed"
        job.save(update_fields=["status"])
        job2 = self._create({"run_tag": "done2"})
        self.assertEqual(job2.status, "pending")


# ---------------------------------------------------------------------------
# Integration: View tests
# ---------------------------------------------------------------------------

_PARSE_VIEW_PATCHES = [
    patch("pipeline.views.ParseView._get_eligible_counts", return_value=[]),
    patch("pipeline.views.ParseView._get_ami_choices", return_value=[("ami-test12345678", "Test AMI")]),
    patch("pipeline.views.ParseView._get_run_history", return_value=[]),
]


def _apply_parse_view_patches(func):
    """Stack the common ParseView patches onto a test method."""
    for p in reversed(_PARSE_VIEW_PATCHES):
        func = p(func)
    return func


class ParseViewTestBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("testuser", password="testpassword1234")
        self.client = Client()
        self.client.login(username="testuser", password="testpassword1234")


class TestParseViewAuth(TestCase):
    def test_parse_requires_login(self):
        resp = Client().get(reverse("parse"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp.url)

    def test_parse_progress_requires_login(self):
        resp = Client().get(reverse("parse_progress"))
        self.assertEqual(resp.status_code, 302)

    def test_parse_stop_requires_login(self):
        resp = Client().post(reverse("parse_stop"))
        self.assertEqual(resp.status_code, 302)

    def test_parse_job_create_requires_login(self):
        resp = Client().post(reverse("parse_job_create"))
        self.assertEqual(resp.status_code, 302)


class TestParsePageLoad(ParseViewTestBase):
    @_apply_parse_view_patches
    def test_parse_page_renders(self, *mocks):
        resp = self.client.get(reverse("parse"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Docling Parse")

    @_apply_parse_view_patches
    def test_context_has_form(self, *mocks):
        resp = self.client.get(reverse("parse"))
        self.assertIn("form", resp.context)
        self.assertIsInstance(resp.context["form"], ParseRunForm)

    @_apply_parse_view_patches
    def test_no_active_run(self, *mocks):
        resp = self.client.get(reverse("parse"))
        self.assertIsNone(resp.context["active_job"])
        self.assertFalse(resp.context["has_active_run"])

    @_apply_parse_view_patches
    @patch("pipeline.views.ParseView._reconcile_parse_jobs")
    def test_has_active_run_context(self, mock_reconcile, *mocks):
        Job.objects.create(
            phase="parse", status="pending", host="localhost",
            config_json={"run_tag": "ctx-test"},
        )
        resp = self.client.get(reverse("parse"))
        self.assertTrue(resp.context["has_active_run"])
        self.assertIsNotNone(resp.context["active_job"])


class TestParseLaunch(ParseViewTestBase):
    @patch("pipeline.views.create_parse_job")
    @patch("pipeline.orchestrator.build_argv", return_value=["echo", "fake"])
    @patch("subprocess.Popen")
    def test_launch_creates_job(self, mock_popen, mock_argv, mock_create):
        mock_job = MagicMock()
        mock_job.pk = 99
        mock_create.return_value = mock_job
        mock_popen.return_value = MagicMock(pid=12345)

        resp = self.client.post(reverse("parse_job_create"), {
            "action": "launch",
            "run_tag": "test-launch",
            "priority": "annual,impact",
            "instance_type": "g6.2xlarge",
            "max_hours": 24,
            "batch_size": 500,
            "workers": 1,
        })
        self.assertEqual(resp.status_code, 302)
        mock_create.assert_called_once()
        config = mock_create.call_args[0][0]
        self.assertEqual(config["run_tag"], "test-launch")

    @patch("pipeline.views.create_parse_job", side_effect=DuplicateJobError("already active"))
    def test_double_launch_rejected(self, mock_create):
        resp = self.client.post(reverse("parse_job_create"), {
            "action": "launch",
            "run_tag": "dup",
            "priority": "annual,impact",
            "instance_type": "g6.2xlarge",
            "max_hours": 24,
            "batch_size": 500,
            "workers": 1,
        })
        self.assertEqual(resp.status_code, 302)

    def test_invalid_form_rejected(self):
        resp = self.client.post(reverse("parse_job_create"), {
            "action": "launch",
            "run_tag": "",
            "priority": "annual,impact",
            "instance_type": "g6.2xlarge",
            "max_hours": 24,
            "batch_size": 500,
            "workers": 1,
        })
        self.assertEqual(resp.status_code, 302)


class TestParseStop(ParseViewTestBase):
    def test_stop_no_active_run(self):
        resp = self.client.post(reverse("parse_stop"))
        self.assertEqual(resp.status_code, 302)

    @patch("pipeline.orchestrator._local_kill")
    def test_stop_cancels_running_job(self, mock_kill):
        job = Job.objects.create(
            phase="parse", status="running", host="localhost",
            config_json={"run_tag": "stop-test"}, pid=12345,
        )
        mock_conn, mock_cur = _mock_cursor_no_rows()
        with patch("django.db.connections") as mock_conns:
            mock_conns.__getitem__ = lambda self, key: mock_conn
            resp = self.client.post(reverse("parse_stop"))
        self.assertEqual(resp.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.status, "cancelled")
        self.assertIsNotNone(job.finished_at)

    def test_stop_scheduled_job(self):
        job = Job.objects.create(
            phase="parse", status="scheduled", host="localhost",
            config_json={"run_tag": "sched-stop"}, pid=None,
        )
        mock_conn, mock_cur = _mock_cursor_no_rows()
        with patch("django.db.connections") as mock_conns:
            mock_conns.__getitem__ = lambda self, key: mock_conn
            resp = self.client.post(reverse("parse_stop"))
        self.assertEqual(resp.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.status, "cancelled")


class TestParseProgress(ParseViewTestBase):
    def test_no_active_run(self):
        resp = self.client.get(reverse("parse_progress"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No active run")

    def test_with_pending_job(self):
        Job.objects.create(
            phase="parse", status="pending", host="localhost",
            config_json={},
        )
        resp = self.client.get(reverse("parse_progress"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Initializing")

    def test_with_scheduled_job(self):
        Job.objects.create(
            phase="parse", status="scheduled", host="localhost",
            config_json={},
        )
        resp = self.client.get(reverse("parse_progress"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Scheduled")


class TestParseReconciliation(ParseViewTestBase):
    @_apply_parse_view_patches
    @patch("pipeline.views._pid_alive", return_value=False)
    def test_dead_pid_marks_failed(self, mock_alive, *view_mocks):
        job = Job.objects.create(
            phase="parse", status="running", host="localhost",
            config_json={"run_tag": "orphan-test"}, pid=99999,
        )
        mock_conn, mock_cur = _mock_cursor_no_rows()
        with patch("django.db.connections") as mock_conns:
            mock_conns.__getitem__ = lambda self, key: mock_conn
            self.client.get(reverse("parse"))
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIsNotNone(job.finished_at)

    @_apply_parse_view_patches
    def test_finished_run_marks_completed(self, *view_mocks):
        from django.utils import timezone as tz
        job = Job.objects.create(
            phase="parse", status="running", host="localhost",
            config_json={"run_tag": "done-run"}, pid=12345,
        )
        finished_at = tz.now()
        mock_conn, mock_cur = _mock_cursor_no_rows()
        mock_cur.fetchone.return_value = (finished_at,)
        with patch("django.db.connections") as mock_conns:
            mock_conns.__getitem__ = lambda self, key: mock_conn
            self.client.get(reverse("parse"))
        job.refresh_from_db()
        self.assertEqual(job.status, "completed")


# ---------------------------------------------------------------------------
# URL resolution
# ---------------------------------------------------------------------------

class TestParseURLs(SimpleTestCase):
    def test_parse_url_resolves(self):
        url = reverse("parse")
        self.assertIn("parse", url)

    def test_parse_job_create_url(self):
        url = reverse("parse_job_create")
        self.assertIn("parse/queue", url)

    def test_parse_progress_url(self):
        url = reverse("parse_progress")
        self.assertIn("parse/progress", url)

    def test_parse_stop_url(self):
        url = reverse("parse_stop")
        self.assertIn("parse/stop", url)


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

class TestParseNavigation(ParseViewTestBase):
    @_apply_parse_view_patches
    def test_nav_link_present(self, *mocks):
        resp = self.client.get(reverse("parse"))
        self.assertContains(resp, "Docling Parse")


# ---------------------------------------------------------------------------
# Spec 0055: Multi-Instance Parse — build_argv workers flag
# ---------------------------------------------------------------------------

class TestBuildArgvParseWorkers(SimpleTestCase):
    def test_workers_flag_in_argv(self):
        argv = build_argv("parse", {"run_tag": "t", "workers": 2})
        idx = argv.index("--workers")
        self.assertEqual(argv[idx + 1], "2")

    def test_workers_default_omitted(self):
        argv = build_argv("parse", {"run_tag": "t"})
        self.assertNotIn("--workers", argv)

    def test_workers_max_valid(self):
        argv = build_argv("parse", {"run_tag": "t", "workers": 4})
        self.assertIn("--workers", argv)
        self.assertIn("4", argv)

    def test_workers_over_max_rejected(self):
        with self.assertRaises(InvalidParameterError):
            build_argv("parse", {"run_tag": "t", "workers": 5})

    def test_workers_under_min_rejected(self):
        with self.assertRaises(InvalidParameterError):
            build_argv("parse", {"run_tag": "t", "workers": 0})


# ---------------------------------------------------------------------------
# Spec 0055: Multi-Instance Parse — Form workers field
# ---------------------------------------------------------------------------

class TestParseRunFormWorkers(SimpleTestCase):
    def _form(self, **overrides):
        data = {
            "run_tag": "test-run",
            "priority": "annual,impact",
            "instance_type": "g6.2xlarge",
            "max_hours": 24,
            "batch_size": 500,
            "workers": 1,
        }
        data.update(overrides)
        return ParseRunForm(data)

    def test_workers_default_valid(self):
        self.assertTrue(self._form(workers=1).is_valid())

    def test_workers_2_valid(self):
        self.assertTrue(self._form(workers=2).is_valid())

    def test_workers_4_valid(self):
        self.assertTrue(self._form(workers=4).is_valid())

    def test_workers_0_rejected(self):
        self.assertFalse(self._form(workers=0).is_valid())

    def test_workers_5_rejected(self):
        self.assertFalse(self._form(workers=5).is_valid())

    def test_workers_negative_rejected(self):
        self.assertFalse(self._form(workers=-1).is_valid())


# ---------------------------------------------------------------------------
# Spec 0055: Multi-Instance Parse — DB function unit tests
# ---------------------------------------------------------------------------

class TestCompleteWorkItemErrorTruncation(SimpleTestCase):
    @patch("lavandula.parse.db.psycopg2")
    def test_error_truncated_to_200(self, mock_pg):
        from lavandula.parse import db as parse_db

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__enter__ = lambda self: self
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = lambda self: self
        mock_conn.__exit__ = MagicMock(return_value=False)

        long_error = "x" * 300
        parse_db.complete_work_item(mock_conn, 1, "abc123", error=long_error)

        call_args = mock_cur.execute.call_args
        params = call_args[0][1]
        self.assertEqual(len(params["error"]), 200)

    @patch("lavandula.parse.db.psycopg2")
    def test_short_error_preserved(self, mock_pg):
        from lavandula.parse import db as parse_db

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__enter__ = lambda self: self
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = lambda self: self
        mock_conn.__exit__ = MagicMock(return_value=False)

        parse_db.complete_work_item(mock_conn, 1, "abc123", error="corrupt_pdf")

        call_args = mock_cur.execute.call_args
        params = call_args[0][1]
        self.assertEqual(params["error"], "corrupt_pdf")

    @patch("lavandula.parse.db.psycopg2")
    def test_no_error_passes_none(self, mock_pg):
        from lavandula.parse import db as parse_db

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__enter__ = lambda self: self
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = lambda self: self
        mock_conn.__exit__ = MagicMock(return_value=False)

        parse_db.complete_work_item(mock_conn, 1, "abc123")

        call_args = mock_cur.execute.call_args
        params = call_args[0][1]
        self.assertIsNone(params["error"])


class TestGetQueueProgress(SimpleTestCase):
    def test_returns_dict_keys(self):
        from lavandula.parse import db as parse_db

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__enter__ = lambda self: self
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchone.return_value = (100, 30, 20, 5)
        mock_conn.cursor.return_value = mock_cur

        result = parse_db.get_queue_progress(mock_conn, 1)
        self.assertEqual(result, {
            "total": 100, "claimed": 30, "completed": 20, "errored": 5,
        })


class TestReclaimStaleClaims(SimpleTestCase):
    def test_reclaim_returns_rowcount(self):
        from lavandula.parse import db as parse_db

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__enter__ = lambda self: self
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.rowcount = 7
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = lambda self: self
        mock_conn.__exit__ = MagicMock(return_value=False)

        result = parse_db.reclaim_stale_claims(mock_conn, 1, "i-abc123")
        self.assertEqual(result, 7)

    def test_reclaim_uses_worker_id_in_query(self):
        from lavandula.parse import db as parse_db

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__enter__ = lambda self: self
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.rowcount = 0
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = lambda self: self
        mock_conn.__exit__ = MagicMock(return_value=False)

        parse_db.reclaim_stale_claims(mock_conn, 42, "i-deadbeef")

        call_args = mock_cur.execute.call_args
        params = call_args[0][1]
        self.assertEqual(params["run_id"], 42)
        self.assertEqual(params["worker"], "i-deadbeef")


class TestReclaimAllStaleClaims(SimpleTestCase):
    def test_reclaim_all_returns_rowcount(self):
        from lavandula.parse import db as parse_db

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__enter__ = lambda self: self
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.rowcount = 15
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = lambda self: self
        mock_conn.__exit__ = MagicMock(return_value=False)

        result = parse_db.reclaim_all_stale_claims(mock_conn, 1)
        self.assertEqual(result, 15)


class TestCleanupWorkQueue(SimpleTestCase):
    def test_cleanup_returns_rowcount(self):
        from lavandula.parse import db as parse_db

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.__enter__ = lambda self: self
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.rowcount = 500
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = lambda self: self
        mock_conn.__exit__ = MagicMock(return_value=False)

        result = parse_db.cleanup_work_queue(mock_conn, 1)
        self.assertEqual(result, 500)


# ---------------------------------------------------------------------------
# Spec 0055: Multi-Instance Parse — Dashboard dry run multi-estimate
# ---------------------------------------------------------------------------

class TestParseDryRunMultiEstimate(ParseViewTestBase):
    @patch.object(
        __import__("pipeline.views", fromlist=["ParseJobCreateView"]).ParseJobCreateView,
        "_get_parse_conn",
    )
    def test_dry_run_shows_worker_estimates(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (1000,)
        mock_cur.__enter__ = lambda self: self
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur
        mock_get_conn.return_value = mock_conn

        resp = self.client.post(reverse("parse_job_create"), {
            "action": "dry_run",
            "run_tag": "est-test",
            "priority": "annual,impact",
            "instance_type": "g6.2xlarge",
            "max_hours": 24,
            "batch_size": 500,
            "workers": 2,
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        msg_texts = [str(m) for m in resp.context["messages"]]
        combined = " ".join(msg_texts)
        self.assertIn("2 workers", combined)
        self.assertIn("1 worker:", combined)
        self.assertIn("4 workers:", combined)

    @patch.object(
        __import__("pipeline.views", fromlist=["ParseJobCreateView"]).ParseJobCreateView,
        "_get_parse_conn",
    )
    def test_dry_run_shows_worker_count_label(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (500,)
        mock_cur.__enter__ = lambda self: self
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur
        mock_get_conn.return_value = mock_conn

        resp = self.client.post(reverse("parse_job_create"), {
            "action": "dry_run",
            "run_tag": "w-test",
            "priority": "annual,impact",
            "instance_type": "g6.2xlarge",
            "max_hours": 24,
            "batch_size": 500,
            "workers": 3,
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        msg_texts = [str(m) for m in resp.context["messages"]]
        combined = " ".join(msg_texts)
        self.assertIn("[3 workers]", combined)


# ---------------------------------------------------------------------------
# Spec 0055: Multi-Instance Parse — Config allowlist includes workers
# ---------------------------------------------------------------------------

class TestParseConfigAllowlist(SimpleTestCase):
    def test_workers_in_parse_allowlist(self):
        from pipeline.views import _CONFIG_ALLOWLIST
        self.assertIn("workers", _CONFIG_ALLOWLIST["parse"])

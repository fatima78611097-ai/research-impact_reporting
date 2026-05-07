"""Tests for the scheduler scoring function."""
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.utils import timezone

from pipeline.scheduler import score_placement, select_next_jobs
from pipeline.scheduler_config import SchedulerConfig


def _make_job(**kwargs):
    job = MagicMock()
    job.phase = kwargs.get("phase", "crawl")
    job.pk = kwargs.get("pk", 1)
    job.created_at = kwargs.get("created_at", timezone.now())
    job.state_code = kwargs.get("state_code", "CA")
    return job


def _make_worker(**kwargs):
    worker = MagicMock()
    worker.hostname = kwargs.get("hostname", "worker1")
    worker.cpu_pct = kwargs.get("cpu_pct", 50.0)
    worker.mem_pct = kwargs.get("mem_pct", 50.0)
    worker.has_gpu = kwargs.get("has_gpu", False)
    return worker


class TestScorePlacement(SimpleTestCase):
    def setUp(self):
        self.config = SchedulerConfig()

    @patch("pipeline.scheduler.Job.objects")
    def test_cpu_ceiling_blocks(self, mock_qs):
        job = _make_job()
        worker = _make_worker(cpu_pct=95.0)
        score = score_placement(job, worker, self.config)
        self.assertIsNone(score)

    @patch("pipeline.scheduler.Job.objects")
    def test_memory_ceiling_blocks(self, mock_qs):
        job = _make_job()
        worker = _make_worker(mem_pct=80.0)
        score = score_placement(job, worker, self.config)
        self.assertIsNone(score)

    @patch("pipeline.scheduler.Job.objects")
    def test_heavy_concurrency_blocks(self, mock_qs):
        mock_qs.filter.return_value.count.return_value = 2
        mock_qs.filter.return_value.exists.return_value = False
        job = _make_job(phase="crawl")
        worker = _make_worker(mem_pct=50.0, cpu_pct=50.0)
        score = score_placement(job, worker, self.config)
        self.assertIsNone(score)

    @patch("pipeline.scheduler.Job.objects")
    def test_gpu_required_blocks_non_gpu(self, mock_qs):
        mock_qs.filter.return_value.count.return_value = 0
        mock_qs.filter.return_value.exists.return_value = False
        config = SchedulerConfig(
            stage_weights={"classify": {"gpu_preferred": True}}
        )
        job = _make_job(phase="classify")
        worker = _make_worker(has_gpu=False)
        score = score_placement(job, worker, config)
        self.assertIsNone(score)

    @patch("pipeline.scheduler.Job.objects")
    def test_cool_down_blocks(self, mock_qs):
        mock_qs.filter.return_value.count.return_value = 0
        mock_qs.filter.return_value.exists.return_value = True
        job = _make_job(phase="resolve")
        worker = _make_worker(mem_pct=50.0, cpu_pct=50.0)
        score = score_placement(job, worker, self.config)
        self.assertIsNone(score)

    @patch("pipeline.scheduler.Job.objects")
    def test_basic_scoring(self, mock_qs):
        mock_qs.filter.return_value.count.return_value = 0
        mock_qs.filter.return_value.exists.return_value = False
        job = _make_job(phase="resolve")
        worker = _make_worker(mem_pct=30.0, cpu_pct=50.0)
        score = score_placement(job, worker, self.config)
        self.assertIsNotNone(score)
        self.assertGreater(score, 1.0)

    @patch("pipeline.scheduler.Job.objects")
    def test_host_affinity_bonus(self, mock_qs):
        mock_qs.filter.return_value.count.return_value = 0
        mock_qs.filter.return_value.exists.return_value = False
        config = SchedulerConfig(
            stage_weights={"resolve": {"prefer_hosts": ["worker1"]}}
        )
        job = _make_job(phase="resolve")
        worker = _make_worker(hostname="worker1", mem_pct=50.0, cpu_pct=50.0)
        score = score_placement(job, worker, config)
        self.assertIsNotNone(score)
        self.assertGreater(score, 1.3)

    @patch("pipeline.scheduler.Job.objects")
    def test_starvation_boost(self, mock_qs):
        mock_qs.filter.return_value.count.return_value = 0
        mock_qs.filter.return_value.exists.return_value = False
        old_job = _make_job(
            phase="resolve",
            created_at=timezone.now() - timedelta(hours=2),
        )
        recent_job = _make_job(
            phase="resolve",
            pk=2,
            created_at=timezone.now(),
        )
        worker = _make_worker(mem_pct=50.0, cpu_pct=50.0)
        old_score = score_placement(old_job, worker, self.config)
        new_score = score_placement(recent_job, worker, self.config)
        self.assertIsNotNone(old_score)
        self.assertIsNotNone(new_score)
        self.assertGreater(old_score, new_score)

    def test_unknown_phase_returns_none(self):
        job = _make_job(phase="nonexistent")
        worker = _make_worker()
        score = score_placement(job, worker, self.config)
        self.assertIsNone(score)

    @patch("pipeline.scheduler.Job.objects")
    def test_null_metrics_allowed(self, mock_qs):
        mock_qs.filter.return_value.count.return_value = 0
        mock_qs.filter.return_value.exists.return_value = False
        job = _make_job(phase="resolve")
        worker = _make_worker(cpu_pct=None, mem_pct=None)
        score = score_placement(job, worker, self.config)
        self.assertIsNotNone(score)


class TestSelectNextJobs(SimpleTestCase):
    @patch("pipeline.scheduler.score_placement")
    def test_greedy_assignment(self, mock_score):
        mock_score.return_value = 1.0
        jobs = [_make_job(pk=1, phase="resolve"), _make_job(pk=2, phase="crawl")]
        workers = [_make_worker(hostname="w1"), _make_worker(hostname="w2")]
        config = SchedulerConfig()
        result = select_next_jobs(jobs, workers, config)
        self.assertEqual(len(result), 2)
        assigned_hosts = {w.hostname for _, w in result}
        self.assertEqual(len(assigned_hosts), 2)

    @patch("pipeline.scheduler.score_placement")
    def test_no_double_assignment(self, mock_score):
        mock_score.return_value = 1.0
        jobs = [_make_job(pk=1), _make_job(pk=2)]
        workers = [_make_worker(hostname="w1")]
        config = SchedulerConfig()
        result = select_next_jobs(jobs, workers, config)
        self.assertEqual(len(result), 1)

    @patch("pipeline.scheduler.score_placement")
    def test_blocked_jobs_excluded(self, mock_score):
        mock_score.return_value = None
        jobs = [_make_job(pk=1)]
        workers = [_make_worker(hostname="w1")]
        config = SchedulerConfig()
        result = select_next_jobs(jobs, workers, config)
        self.assertEqual(len(result), 0)

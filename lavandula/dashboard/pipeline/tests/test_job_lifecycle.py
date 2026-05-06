"""Tests for job lifecycle state transitions, retry chains, and cancellation."""
from django.test import SimpleTestCase

from pipeline.models import Job


class TestJobTransitions(SimpleTestCase):
    def _make_job(self, status="pending"):
        job = Job.__new__(Job)
        job.status = status
        return job

    def test_pending_to_scheduled(self):
        job = self._make_job("pending")
        self.assertTrue(job.can_transition_to("scheduled"))

    def test_pending_to_cancelled(self):
        job = self._make_job("pending")
        self.assertTrue(job.can_transition_to("cancelled"))

    def test_pending_to_running_blocked(self):
        job = self._make_job("pending")
        self.assertFalse(job.can_transition_to("running"))

    def test_scheduled_to_running(self):
        job = self._make_job("scheduled")
        self.assertTrue(job.can_transition_to("running"))

    def test_scheduled_to_failed(self):
        job = self._make_job("scheduled")
        self.assertTrue(job.can_transition_to("failed"))

    def test_scheduled_to_pending(self):
        job = self._make_job("scheduled")
        self.assertTrue(job.can_transition_to("pending"))

    def test_running_to_completed(self):
        job = self._make_job("running")
        self.assertTrue(job.can_transition_to("completed"))

    def test_running_to_failed(self):
        job = self._make_job("running")
        self.assertTrue(job.can_transition_to("failed"))

    def test_running_to_cancelled(self):
        job = self._make_job("running")
        self.assertTrue(job.can_transition_to("cancelled"))

    def test_completed_is_terminal(self):
        job = self._make_job("completed")
        self.assertFalse(job.can_transition_to("running"))
        self.assertFalse(job.can_transition_to("pending"))
        self.assertFalse(job.can_transition_to("failed"))

    def test_failed_is_terminal(self):
        job = self._make_job("failed")
        self.assertFalse(job.can_transition_to("running"))
        self.assertFalse(job.can_transition_to("pending"))

    def test_cancelled_is_terminal(self):
        job = self._make_job("cancelled")
        self.assertFalse(job.can_transition_to("running"))
        self.assertFalse(job.can_transition_to("pending"))

    def test_transition_status_raises_on_invalid(self):
        job = self._make_job("completed")
        with self.assertRaises(ValueError):
            job.transition_status("running")

    def test_transition_status_raises_pending_to_running(self):
        job = self._make_job("pending")
        with self.assertRaises(ValueError):
            job.transition_status("running")

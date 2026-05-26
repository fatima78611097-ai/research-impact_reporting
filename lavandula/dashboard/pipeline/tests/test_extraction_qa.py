from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from pipeline.models import NonprofitSeed, Report


def _create_report(sha="abc123", ein="123456789", year=2024):
    return Report.objects.create(
        content_sha256=sha,
        source_org_ein=ein,
        archived_at="2024-01-01",
        file_size_bytes=1000,
        report_year=year,
    )


SAMPLE_RUNS = [
    {"id": 10, "run_tag": "p20-v1", "created_at": "2026-05-25"},
    {"id": 9, "run_tag": "p20-test", "created_at": "2026-05-24"},
]

SAMPLE_METRICS = [
    {"metric_text": "670,017 meals prepared", "metric_type": "meals",
     "metric_value": 670017, "unit": "meals", "geo_impact": "LOCAL",
     "source_snippet": "In 2024, we prepared 670,017 meals for families."},
    {"metric_text": "$34M rental assistance", "metric_type": "USD",
     "metric_value": 34217302, "unit": "USD", "geo_impact": "STATE",
     "source_snippet": "We provided $34,217,302 in rental assistance."},
]

SAMPLE_STORIES = [
    {"story_title": "Maria's Journey", "story_summary": "After 3 months in shelter...",
     "people_mentioned": ["Maria"], "program": "Housing First",
     "themes": ["housing", "resilience"],
     "source_snippet": "Maria came to us after losing her home."},
]

MOCK_PREFIX = "pipeline.views."


def _patch_qa_helpers(runs=None, metrics=None, stories=None, org_docs=None):
    """Return a dict of patch objects for the QA data helpers."""
    return {
        "runs": patch(MOCK_PREFIX + "_qa_get_runs", return_value=runs or []),
        "metrics": patch(MOCK_PREFIX + "_qa_get_metrics", return_value=metrics or []),
        "stories": patch(MOCK_PREFIX + "_qa_get_stories", return_value=stories or []),
        "org_docs": patch(MOCK_PREFIX + "_qa_get_org_docs", return_value=org_docs or []),
        "boto": patch("boto3.client"),
    }


class ExtractionQAAuthTest(TestCase):
    databases = {"default", "pipeline"}

    def test_unauthenticated_redirects_to_login(self):
        _create_report()
        resp = Client().get(reverse("extraction_qa", args=["abc123"]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp.url)


class ExtractionQAViewTest(TestCase):
    databases = {"default", "pipeline"}

    def setUp(self):
        self.user = User.objects.create_user("testuser", password="testpassword1234")
        self.client = Client()
        self.client.login(username="testuser", password="testpassword1234")
        self.report = _create_report()
        NonprofitSeed.objects.create(ein="123456789", name="Test Org")

    def _get_qa(self, sha="abc123", query="", runs=None, metrics=None,
                stories=None, org_docs=None):
        patches = _patch_qa_helpers(runs, metrics, stories, org_docs)
        with patches["runs"], patches["metrics"], patches["stories"], \
             patches["org_docs"], patches["boto"] as mock_boto:
            mock_s3 = MagicMock()
            mock_s3.generate_presigned_url.return_value = "https://s3.example.com/test.pdf"
            mock_boto.return_value = mock_s3
            url = reverse("extraction_qa", args=[sha])
            if query:
                url += "?" + query
            return self.client.get(url)

    def test_authenticated_200(self):
        resp = self._get_qa()
        self.assertEqual(resp.status_code, 200)

    def test_nonexistent_doc_404(self):
        resp = self._get_qa(sha="nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_cache_control_header(self):
        resp = self._get_qa()
        self.assertEqual(resp["Cache-Control"], "no-store")

    def test_context_has_all_keys(self):
        resp = self._get_qa()
        for key in ["pdf_url", "metrics", "stories", "available_runs",
                     "selected_run_id", "prev_doc", "next_doc",
                     "doc_position", "doc_total", "org", "metrics_json", "stories_json"]:
            self.assertIn(key, resp.context)

    def test_no_extractions_empty_lists(self):
        resp = self._get_qa()
        self.assertEqual(resp.context["metrics"], [])
        self.assertEqual(resp.context["stories"], [])
        self.assertEqual(resp.context["metrics_json"], [])
        self.assertEqual(resp.context["stories_json"], [])
        self.assertIsNone(resp.context["selected_run_id"])

    def test_with_metrics_and_stories(self):
        resp = self._get_qa(runs=SAMPLE_RUNS, metrics=SAMPLE_METRICS,
                            stories=SAMPLE_STORIES)
        self.assertEqual(len(resp.context["metrics"]), 2)
        self.assertEqual(len(resp.context["stories"]), 1)
        self.assertEqual(resp.context["selected_run_id"], 10)

    def test_specific_run_id(self):
        resp = self._get_qa(query="run_id=9", runs=SAMPLE_RUNS,
                            metrics=SAMPLE_METRICS, stories=SAMPLE_STORIES)
        self.assertEqual(resp.context["selected_run_id"], 9)

    def test_default_latest_run(self):
        resp = self._get_qa(runs=SAMPLE_RUNS, metrics=SAMPLE_METRICS)
        self.assertEqual(resp.context["selected_run_id"], 10)

    def test_metrics_only(self):
        resp = self._get_qa(runs=SAMPLE_RUNS, metrics=SAMPLE_METRICS, stories=[])
        self.assertEqual(len(resp.context["metrics"]), 2)
        self.assertEqual(len(resp.context["stories"]), 0)

    def test_stories_only(self):
        resp = self._get_qa(runs=SAMPLE_RUNS, metrics=[], stories=SAMPLE_STORIES)
        self.assertEqual(len(resp.context["metrics"]), 0)
        self.assertEqual(len(resp.context["stories"]), 1)

    def test_available_runs_in_context(self):
        resp = self._get_qa(runs=SAMPLE_RUNS)
        self.assertEqual(len(resp.context["available_runs"]), 2)
        self.assertEqual(resp.context["available_runs"][0]["run_tag"], "p20-v1")

    def test_org_in_context(self):
        resp = self._get_qa()
        self.assertEqual(resp.context["org"].name, "Test Org")

    def test_no_extractions_shows_message(self):
        resp = self._get_qa()
        self.assertContains(resp, "No extraction data for this document")

    def test_metrics_section_shows_count(self):
        resp = self._get_qa(runs=SAMPLE_RUNS, metrics=SAMPLE_METRICS,
                            stories=SAMPLE_STORIES)
        self.assertContains(resp, "METRICS (2)")

    def test_stories_section_shows_count(self):
        resp = self._get_qa(runs=SAMPLE_RUNS, metrics=SAMPLE_METRICS,
                            stories=SAMPLE_STORIES)
        self.assertContains(resp, "STORIES (1)")

    def test_pdf_url_in_context(self):
        resp = self._get_qa()
        self.assertEqual(resp.context["pdf_url"], "https://s3.example.com/test.pdf")

    def test_json_script_data(self):
        resp = self._get_qa(runs=SAMPLE_RUNS, metrics=SAMPLE_METRICS,
                            stories=SAMPLE_STORIES)
        self.assertContains(resp, 'id="metrics-data"')
        self.assertContains(resp, 'id="stories-data"')


class ExtractionQANavigationTest(TestCase):
    databases = {"default", "pipeline"}

    def setUp(self):
        self.user = User.objects.create_user("testuser", password="testpassword1234")
        self.client = Client()
        self.client.login(username="testuser", password="testpassword1234")
        _create_report("sha_a", "123456789", 2024)
        _create_report("sha_b", "123456789", 2023)
        _create_report("sha_c", "123456789", 2022)
        NonprofitSeed.objects.create(ein="123456789", name="Test Org")

    def _get_qa(self, sha, org_docs=None):
        patches = _patch_qa_helpers(
            runs=SAMPLE_RUNS, metrics=SAMPLE_METRICS, org_docs=org_docs,
        )
        with patches["runs"], patches["metrics"], patches["stories"], \
             patches["org_docs"], patches["boto"] as mock_boto:
            mock_s3 = MagicMock()
            mock_s3.generate_presigned_url.return_value = "https://s3.example.com/test.pdf"
            mock_boto.return_value = mock_s3
            return self.client.get(reverse("extraction_qa", args=[sha]))

    def test_prev_next_with_multiple_docs(self):
        org_docs = [("sha_a", 2024), ("sha_b", 2023), ("sha_c", 2022)]
        resp = self._get_qa("sha_b", org_docs=org_docs)
        self.assertEqual(resp.context["prev_doc"], "sha_a")
        self.assertEqual(resp.context["next_doc"], "sha_c")
        self.assertEqual(resp.context["doc_position"], 2)
        self.assertEqual(resp.context["doc_total"], 3)

    def test_single_doc_no_prev_next(self):
        org_docs = [("sha_b", 2023)]
        resp = self._get_qa("sha_b", org_docs=org_docs)
        self.assertIsNone(resp.context["prev_doc"])
        self.assertIsNone(resp.context["next_doc"])
        self.assertEqual(resp.context["doc_total"], 1)

    def test_first_doc_no_prev(self):
        org_docs = [("sha_a", 2024), ("sha_b", 2023)]
        resp = self._get_qa("sha_a", org_docs=org_docs)
        self.assertIsNone(resp.context["prev_doc"])
        self.assertEqual(resp.context["next_doc"], "sha_b")

    def test_last_doc_no_next(self):
        org_docs = [("sha_a", 2024), ("sha_b", 2023)]
        resp = self._get_qa("sha_b", org_docs=org_docs)
        self.assertEqual(resp.context["prev_doc"], "sha_a")
        self.assertIsNone(resp.context["next_doc"])

    def test_doc_position_correct(self):
        org_docs = [("sha_a", 2024), ("sha_b", 2023), ("sha_c", 2022)]
        resp = self._get_qa("sha_c", org_docs=org_docs)
        self.assertEqual(resp.context["doc_position"], 3)
        self.assertEqual(resp.context["doc_total"], 3)


class OrgExtractionQARedirectTest(TestCase):
    databases = {"default", "pipeline"}

    def setUp(self):
        self.user = User.objects.create_user("testuser", password="testpassword1234")
        self.client = Client()
        self.client.login(username="testuser", password="testpassword1234")
        NonprofitSeed.objects.create(ein="123456789", name="Test Org")
        _create_report()

    def test_unauthenticated_redirects(self):
        resp = Client().get(reverse("org_extraction_qa", args=["123456789"]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp.url)

    def test_no_extractions_404(self):
        with patch(MOCK_PREFIX + "_qa_get_latest_run_for_org", return_value=None):
            resp = self.client.get(reverse("org_extraction_qa", args=["123456789"]))
        self.assertEqual(resp.status_code, 404)

    def test_redirects_to_first_doc(self):
        with patch(MOCK_PREFIX + "_qa_get_latest_run_for_org", return_value=10), \
             patch(MOCK_PREFIX + "_qa_get_org_docs", return_value=[("abc123", 2024)]):
            resp = self.client.get(reverse("org_extraction_qa", args=["123456789"]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/reports/abc123/qa/", resp.url)
        self.assertIn("run_id=10", resp.url)

    def test_run_with_empty_docs_404(self):
        with patch(MOCK_PREFIX + "_qa_get_latest_run_for_org", return_value=10), \
             patch(MOCK_PREFIX + "_qa_get_org_docs", return_value=[]):
            resp = self.client.get(reverse("org_extraction_qa", args=["123456789"]))
        self.assertEqual(resp.status_code, 404)

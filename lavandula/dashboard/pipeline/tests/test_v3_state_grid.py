"""Tests for Spec 0042: Classifier V3 State Progress Grid."""
from django.contrib.auth.models import User
from django.db import connections
from django.test import Client, TestCase
from django.urls import reverse

from pipeline.models import Job
from pipeline.views import _v3_state_grid


def _insert_seed(cur, ein, state):
    cur.execute(
        "INSERT INTO nonprofits_seed (ein, state) VALUES (%s, %s)",
        [ein, state],
    )


def _insert_corpus(cur, sha, ein, content_type="application/pdf", v3_material_type=None):
    cur.execute(
        "INSERT INTO corpus (content_sha256, source_org_ein, archived_at, "
        "file_size_bytes, content_type, v3_material_type) VALUES (%s, %s, '2024-01-01', 1000, %s, %s)",
        [sha, ein, content_type, v3_material_type],
    )


def _insert_context(cur, sha, method="llm"):
    cur.execute(
        "INSERT INTO classification_context (content_sha256, extraction_method) VALUES (%s, %s)",
        [sha, method],
    )


def _insert_run(cur, run_id, finished=True):
    cur.execute(
        "INSERT INTO classification_runs (id, run_tag, started_at, finished_at) VALUES (%s, %s, '2024-01-01', %s)",
        [run_id, f"run-{run_id}", "2024-01-01" if finished else None],
    )


def _insert_result(cur, sha, run_id):
    cur.execute(
        "INSERT INTO classification_results (content_sha256, run_id) VALUES (%s, %s)",
        [sha, run_id],
    )


class StateGridTestBase(TestCase):
    databases = "__all__"

    def setUp(self):
        self.cur = connections["pipeline"].cursor()

    def tearDown(self):
        for table in [
            "classification_results",
            "classification_runs",
            "classification_context",
            "corpus",
            "nonprofits_seed",
        ]:
            self.cur.execute(f"DELETE FROM {table}")
        self.cur.close()


class ZeroDocStateTest(StateGridTestBase):
    def test_zero_doc_state(self):
        _insert_seed(self.cur, "000000001", "ZZ")
        result = _v3_state_grid()
        rows = result["grid_rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["state"], "ZZ")
        for cell in rows[0]["cells"]:
            self.assertEqual(cell["status"], "none")


class PartialExtractionTest(StateGridTestBase):
    def test_partial_extraction(self):
        for i in range(10):
            _insert_seed(self.cur, f"10000000{i}", "TX")
            _insert_corpus(self.cur, f"sha_tx_{i}", f"10000000{i}")
        for i in range(5):
            _insert_context(self.cur, f"sha_tx_{i}")

        result = _v3_state_grid()
        rows = result["grid_rows"]
        self.assertEqual(len(rows), 1)
        extract_cell = rows[0]["cells"][0]
        self.assertEqual(extract_cell["status"], "partial")
        self.assertEqual(extract_cell["pct"], 50)


class CompleteExtractionTest(StateGridTestBase):
    def test_complete_extraction(self):
        for i in range(5):
            _insert_seed(self.cur, f"20000000{i}", "CA")
            _insert_corpus(self.cur, f"sha_ca_{i}", f"20000000{i}")
            _insert_context(self.cur, f"sha_ca_{i}")

        result = _v3_state_grid()
        rows = result["grid_rows"]
        extract_cell = rows[0]["cells"][0]
        self.assertEqual(extract_cell["status"], "complete")
        self.assertEqual(extract_cell["pct"], 100)

    def test_failed_extraction_counts_as_extracted(self):
        _insert_seed(self.cur, "200000010", "NY")
        _insert_corpus(self.cur, "sha_ny_0", "200000010")
        _insert_context(self.cur, "sha_ny_0", method="failed:encrypted")

        result = _v3_state_grid()
        ny_row = [r for r in result["grid_rows"] if r["state"] == "NY"][0]
        self.assertEqual(ny_row["cells"][0]["status"], "complete")


class RunningJobOverlayTest(StateGridTestBase):
    def test_running_job_overlay(self):
        _insert_seed(self.cur, "300000001", "FL")
        _insert_corpus(self.cur, "sha_fl_0", "300000001")
        _insert_context(self.cur, "sha_fl_0")

        Job.objects.create(
            phase="extract-context", state_code="FL", status="running", host="localhost"
        )
        result = _v3_state_grid()
        fl_row = [r for r in result["grid_rows"] if r["state"] == "FL"][0]
        self.assertEqual(fl_row["cells"][0]["status"], "running")


class FailedJobOverlayTest(StateGridTestBase):
    def test_failed_job_overlay(self):
        _insert_seed(self.cur, "400000001", "OH")
        _insert_corpus(self.cur, "sha_oh_0", "400000001")

        Job.objects.create(
            phase="reclassify", state_code="OH", status="failed", host="localhost"
        )
        result = _v3_state_grid()
        oh_row = [r for r in result["grid_rows"] if r["state"] == "OH"][0]
        self.assertEqual(oh_row["cells"][1]["status"], "failed")


class NationwideFallbackTest(StateGridTestBase):
    def test_nationwide_fallback(self):
        _insert_seed(self.cur, "500000001", "WA")
        _insert_seed(self.cur, "500000002", "OR")

        Job.objects.create(
            phase="extract-context", state_code=None, status="running", host="localhost"
        )
        result = _v3_state_grid()
        for row in result["grid_rows"]:
            self.assertEqual(row["cells"][0]["status"], "running")


class StateSpecificOverridesNationwideTest(StateGridTestBase):
    def test_state_specific_overrides_nationwide_job_based(self):
        _insert_seed(self.cur, "600000001", "GA")
        _insert_seed(self.cur, "600000002", "SC")

        Job.objects.create(
            phase="compare-classify", state_code=None, status="running", host="localhost"
        )
        Job.objects.create(
            phase="compare-classify", state_code="GA", status="completed", host="localhost"
        )
        result = _v3_state_grid()
        ga_row = [r for r in result["grid_rows"] if r["state"] == "GA"][0]
        sc_row = [r for r in result["grid_rows"] if r["state"] == "SC"][0]
        self.assertEqual(ga_row["cells"][2]["status"], "complete")
        self.assertEqual(sc_row["cells"][2]["status"], "running")

    def test_state_specific_overrides_nationwide_pct(self):
        _insert_seed(self.cur, "600000003", "TN")
        _insert_seed(self.cur, "600000004", "KY")

        Job.objects.create(
            phase="extract-context", state_code=None, status="running", host="localhost"
        )
        Job.objects.create(
            phase="extract-context", state_code="TN", status="completed", host="localhost"
        )
        result = _v3_state_grid()
        tn_row = [r for r in result["grid_rows"] if r["state"] == "TN"][0]
        ky_row = [r for r in result["grid_rows"] if r["state"] == "KY"][0]
        self.assertNotEqual(tn_row["cells"][0]["status"], "running")
        self.assertEqual(ky_row["cells"][0]["status"], "running")


class JobBasedStepsNoPartialTest(StateGridTestBase):
    def test_job_based_complete(self):
        _insert_seed(self.cur, "700000001", "IL")
        Job.objects.create(
            phase="compare-classify", state_code="IL", status="completed", host="localhost"
        )
        result = _v3_state_grid()
        il_row = [r for r in result["grid_rows"] if r["state"] == "IL"][0]
        self.assertEqual(il_row["cells"][2]["status"], "complete")

    def test_job_based_none(self):
        _insert_seed(self.cur, "700000002", "MI")
        result = _v3_state_grid()
        mi_row = [r for r in result["grid_rows"] if r["state"] == "MI"][0]
        self.assertEqual(mi_row["cells"][2]["status"], "none")
        self.assertEqual(mi_row["cells"][3]["status"], "none")

    def test_job_based_never_partial(self):
        _insert_seed(self.cur, "700000003", "WI")
        result = _v3_state_grid()
        wi_row = [r for r in result["grid_rows"] if r["state"] == "WI"][0]
        for cell in [wi_row["cells"][2], wi_row["cells"][3]]:
            self.assertNotEqual(cell["status"], "partial")


class SortingActionabilityTest(StateGridTestBase):
    def test_sorting_order(self):
        _insert_seed(self.cur, "800000001", "AA")
        _insert_seed(self.cur, "800000002", "BB")
        _insert_corpus(self.cur, "sha_bb_0", "800000002")
        _insert_context(self.cur, "sha_bb_0")
        _insert_seed(self.cur, "800000003", "CC")
        _insert_seed(self.cur, "800000004", "DD")
        _insert_seed(self.cur, "800000005", "EE")

        Job.objects.create(
            phase="extract-context", state_code="DD", status="failed", host="localhost"
        )
        Job.objects.create(
            phase="reclassify", state_code="EE", status="running", host="localhost"
        )

        result = _v3_state_grid()
        states = [r["state"] for r in result["grid_rows"]]
        dd_idx = states.index("DD")
        ee_idx = states.index("EE")
        bb_idx = states.index("BB")
        aa_idx = states.index("AA")
        cc_idx = states.index("CC")
        self.assertLess(dd_idx, ee_idx)
        self.assertLess(ee_idx, bb_idx)
        self.assertLess(bb_idx, aa_idx)
        self.assertLess(bb_idx, cc_idx)


class RowCountMatchesStatesTest(StateGridTestBase):
    def test_row_count(self):
        _insert_seed(self.cur, "900000001", "PA")
        _insert_seed(self.cur, "900000002", "NJ")
        _insert_corpus(self.cur, "sha_pa_0", "900000001")
        _insert_corpus(self.cur, "sha_nj_0", "900000002")
        _insert_seed(self.cur, "900000003", "DE")

        result = _v3_state_grid()
        states = {r["state"] for r in result["grid_rows"]}
        self.assertEqual(states, {"PA", "NJ", "DE"})
        de_row = [r for r in result["grid_rows"] if r["state"] == "DE"][0]
        self.assertEqual(de_row["total_docs"], 0)


class MultipleSeedsSameStateTest(StateGridTestBase):
    def test_multiple_seeds(self):
        for i in range(5):
            _insert_seed(self.cur, f"A0000000{i}", "MA")
            _insert_corpus(self.cur, f"sha_ma_{i}a", f"A0000000{i}")
            _insert_corpus(self.cur, f"sha_ma_{i}b", f"A0000000{i}")

        result = _v3_state_grid()
        ma_row = [r for r in result["grid_rows"] if r["state"] == "MA"][0]
        self.assertEqual(ma_row["total_docs"], 10)


class InProgressRunIgnoredTest(StateGridTestBase):
    def test_in_progress_run_ignored(self):
        _insert_seed(self.cur, "B000000001", "CO")
        _insert_corpus(self.cur, "sha_co_0", "B000000001")
        _insert_run(self.cur, 1, finished=True)
        _insert_run(self.cur, 2, finished=False)
        _insert_result(self.cur, "sha_co_0", 1)

        result = _v3_state_grid()
        co_row = [r for r in result["grid_rows"] if r["state"] == "CO"][0]
        self.assertEqual(co_row["cells"][1]["status"], "complete")
        self.assertEqual(co_row["cells"][1]["pct"], 100)


class HtmxPartialResponseTest(TestCase):
    databases = "__all__"

    def setUp(self):
        self.user = User.objects.create_user("testuser", password="testpassword1234")
        self.client = Client()
        self.client.login(username="testuser", password="testpassword1234")

    def test_partial_returns_200(self):
        resp = self.client.get(reverse("classifier_v3_state_grid"))
        self.assertEqual(resp.status_code, 200)

    def test_partial_is_fragment(self):
        resp = self.client.get(reverse("classifier_v3_state_grid"))
        content = resp.content.decode()
        self.assertNotIn("<html", content)
        self.assertIn("<table", content)

    def test_partial_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse("classifier_v3_state_grid"))
        self.assertEqual(resp.status_code, 302)


class PdfOnlyDenominatorTest(StateGridTestBase):
    def test_pdf_only(self):
        _insert_seed(self.cur, "C000000001", "AZ")
        for i in range(5):
            _insert_corpus(self.cur, f"sha_az_pdf_{i}", "C000000001", content_type="application/pdf")
        for i in range(3):
            _insert_corpus(self.cur, f"sha_az_html_{i}", "C000000001", content_type="text/html")

        result = _v3_state_grid()
        az_row = [r for r in result["grid_rows"] if r["state"] == "AZ"][0]
        self.assertEqual(az_row["total_docs"], 5)

"""Tests for summary parsing."""
from django.test import SimpleTestCase

from pipeline.summary_parser import (
    build_v0_summary,
    parse_done_line,
    parse_error_line,
    parse_progress_line,
    parse_summary_line,
)


class TestParseDoneLine(SimpleTestCase):
    def test_basic_done_line(self):
        line = "=== records_processed=3537 records_failed=50 DONE ==="
        result = parse_done_line(line)
        self.assertEqual(result["records_processed"], 3537)
        self.assertEqual(result["records_failed"], 50)

    def test_done_with_colons(self):
        line = "=== processed: 100 failed: 5 DONE ==="
        result = parse_done_line(line)
        self.assertEqual(result["processed"], 100)
        self.assertEqual(result["failed"], 5)

    def test_empty_done(self):
        line = "=== DONE ==="
        result = parse_done_line(line)
        self.assertEqual(result, {})

    def test_no_match(self):
        line = "Just a regular log line"
        result = parse_done_line(line)
        self.assertEqual(result, {})


class TestParseSummaryLine(SimpleTestCase):
    def test_basic_summary(self):
        line = "SUMMARY: duration_s=29376 records_processed=3537 records_failed=50"
        result = parse_summary_line(line)
        self.assertEqual(result["duration_s"], 29376)
        self.assertEqual(result["records_processed"], 3537)
        self.assertEqual(result["records_failed"], 50)

    def test_with_floats(self):
        line = "SUMMARY: duration_s=100.5 rate=437.2"
        result = parse_summary_line(line)
        self.assertEqual(result["duration_s"], 100.5)
        self.assertEqual(result["rate"], 437.2)

    def test_non_summary_line(self):
        line = "This is not a summary"
        result = parse_summary_line(line)
        self.assertEqual(result, {})


class TestParseProgressLine(SimpleTestCase):
    def test_basic_progress(self):
        line = "PROGRESS: current=500 total=3589 rate=437"
        result = parse_progress_line(line)
        self.assertIsNotNone(result)
        self.assertEqual(result["current"], 500)
        self.assertEqual(result["total"], 3589)
        self.assertEqual(result["rate"], 437)

    def test_partial_progress(self):
        line = "PROGRESS: current=100"
        result = parse_progress_line(line)
        self.assertIsNotNone(result)
        self.assertEqual(result["current"], 100)
        self.assertNotIn("total", result)

    def test_non_progress_line(self):
        line = "Regular log output"
        result = parse_progress_line(line)
        self.assertIsNone(result)


class TestParseErrorLine(SimpleTestCase):
    def test_basic_error(self):
        line = "ERROR: class=flush_failure detail=3 unresolved flush failures"
        result = parse_error_line(line)
        self.assertIsNotNone(result)
        self.assertEqual(result["error_class"], "flush_failure")
        self.assertEqual(result["error_detail"], "3 unresolved flush failures")

    def test_class_only(self):
        line = "ERROR: class=timeout"
        result = parse_error_line(line)
        self.assertIsNotNone(result)
        self.assertEqual(result["error_class"], "timeout")

    def test_non_error_line(self):
        line = "WARNING: something happened"
        result = parse_error_line(line)
        self.assertIsNone(result)


class TestBuildV0Summary(SimpleTestCase):
    def test_basic_summary(self):
        result = build_v0_summary(0, 100.5, "/var/log/test.log", 4096)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["duration_s"], 100)
        self.assertEqual(result["log_file"], "/var/log/test.log")
        self.assertEqual(result["log_size_bytes"], 4096)
        self.assertEqual(result["protocol"], "v0")

    def test_none_values_excluded(self):
        result = build_v0_summary(1, None, None, None)
        self.assertEqual(result["exit_code"], 1)
        self.assertNotIn("duration_s", result)
        self.assertNotIn("log_file", result)
        self.assertNotIn("log_size_bytes", result)

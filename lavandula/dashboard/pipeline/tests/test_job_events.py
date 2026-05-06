"""Tests for JobEvent model and helper functions."""
import json

from django.test import SimpleTestCase

from pipeline.models import PAYLOAD_MAX_BYTES, truncate_payload


class TestTruncatePayload(SimpleTestCase):
    def test_small_payload_unchanged(self):
        payload = {"exit_code": 0, "duration_s": 100}
        result = truncate_payload(payload)
        self.assertEqual(result, payload)

    def test_large_payload_truncated(self):
        payload = {"detail": "x" * 100000}
        result = truncate_payload(payload)
        self.assertTrue(result.get("_truncated"))
        self.assertLess(len(json.dumps(result)), PAYLOAD_MAX_BYTES * 2)

    def test_large_string_value_capped(self):
        # Must exceed 64KB to trigger truncation
        payload = {"error_detail": "a" * 70000, "exit_code": 1}
        result = truncate_payload(payload)
        self.assertTrue(result["_truncated"])
        self.assertLessEqual(len(result["error_detail"]), 504)
        self.assertEqual(result["exit_code"], 1)

    def test_non_string_values_preserved(self):
        payload = {"count": 999, "nested": {"a": 1}, "flag": True}
        result = truncate_payload(payload)
        self.assertEqual(result["count"], 999)
        self.assertEqual(result["nested"], {"a": 1})
        self.assertEqual(result["flag"], True)

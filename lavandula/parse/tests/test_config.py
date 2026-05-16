"""Unit tests for lavandula.parse.config."""
from __future__ import annotations

import pytest

from lavandula.parse.config import (
    filter_metadata,
    sanitize_error,
    validate_priority_values,
    validate_run_tag,
    validate_sha256,
)


class TestValidateSha256:
    def test_valid_sha(self):
        assert validate_sha256("a" * 64) is True
        assert validate_sha256("0123456789abcdef" * 4) is True

    def test_rejects_uppercase(self):
        assert validate_sha256("A" * 64) is False

    def test_rejects_short(self):
        assert validate_sha256("a" * 63) is False

    def test_rejects_long(self):
        assert validate_sha256("a" * 65) is False

    def test_rejects_non_hex(self):
        assert validate_sha256("g" * 64) is False

    def test_rejects_empty(self):
        assert validate_sha256("") is False

    def test_rejects_path_injection(self):
        assert validate_sha256("a" * 32 + "/../../../etc/passwd" + "a" * 12) is False


class TestValidateRunTag:
    def test_valid_tags(self):
        assert validate_run_tag("priority-batch-v1") is True
        assert validate_run_tag("smoke_test") is True
        assert validate_run_tag("run123") is True
        assert validate_run_tag("a") is True

    def test_rejects_empty(self):
        assert validate_run_tag("") is False

    def test_rejects_too_long(self):
        assert validate_run_tag("x" * 65) is False

    def test_rejects_special_chars(self):
        assert validate_run_tag("run;rm -rf /") is False
        assert validate_run_tag("run$(whoami)") is False
        assert validate_run_tag("run tag") is False


class TestValidatePriorityValues:
    def test_valid_values(self):
        assert validate_priority_values(["annual", "impact"]) is True
        assert validate_priority_values(["program_description"]) is True

    def test_rejects_uppercase(self):
        assert validate_priority_values(["Annual"]) is False

    def test_rejects_special_chars(self):
        assert validate_priority_values(["annual'; DROP TABLE"]) is False

    def test_rejects_empty_string(self):
        assert validate_priority_values([""]) is False


class TestSanitizeError:
    def test_basic_error(self):
        exc = ValueError("something went wrong")
        result = sanitize_error(exc)
        assert result == "ValueError: something went wrong"

    def test_truncates_long_messages(self):
        exc = RuntimeError("x" * 1000)
        result = sanitize_error(exc)
        assert len(result) <= 500

    def test_strips_paths(self):
        exc = FileNotFoundError("/home/ubuntu/secret/data.pdf not found")
        result = sanitize_error(exc)
        assert "/home/ubuntu" not in result
        assert "<path>" in result

    def test_strips_tracebacks(self):
        exc = RuntimeError("error\nTraceback (most recent call last):\n  File...")
        result = sanitize_error(exc)
        assert "Traceback" not in result

    def test_preserves_error_class(self):
        exc = ConnectionResetError("connection lost")
        result = sanitize_error(exc)
        assert result.startswith("ConnectionResetError:")


class TestFilterMetadata:
    def test_keeps_allowed_keys(self):
        raw = {"title": "Annual Report 2024", "year": 2024, "author": "John"}
        result = filter_metadata(raw)
        assert result == {"title": "Annual Report 2024", "year": 2024}

    def test_returns_none_for_empty(self):
        assert filter_metadata({}) is None
        assert filter_metadata(None) is None

    def test_returns_none_if_no_allowed_keys(self):
        raw = {"author": "John", "editor": "Jane", "hidden_code": "xyz"}
        assert filter_metadata(raw) is None

    def test_filters_sensitive_keys(self):
        raw = {
            "title": "Report",
            "year": 2024,
            "producer": "Adobe InDesign",
            "creator": "john.doe@company.com",
            "custom_field": "internal_code_123",
        }
        result = filter_metadata(raw)
        assert "producer" not in result
        assert "creator" not in result
        assert "custom_field" not in result

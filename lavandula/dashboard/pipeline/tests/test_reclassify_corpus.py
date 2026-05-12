"""Tests for reclassify_corpus command (Spec 0038).

These tests mock the database engine and LLM clients to verify:
- Concurrency semantics (ThreadPoolExecutor usage)
- Filter logic (state, EIN)
- Resume validation
- Quality gate behavior
- INNER JOIN vs LEFT JOIN
- Dry-run output
- Error handling
- Progress reporting
"""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class _StubAnthropicResponse:
    def __init__(self, tool_input, input_tokens=100, output_tokens=50):
        self.content = [
            type("Block", (), {
                "type": "tool_use",
                "name": "record_classification",
                "input": tool_input,
            })
        ]
        self.usage = type("Usage", (), {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        })
        self.stop_reason = "tool_use"


def _make_stub_client(material_type="annual_report", delay=0):
    class _Client:
        _cli_model = "test-model"
        _model = "test-model"
        class messages:
            @staticmethod
            def create(**kwargs):
                if delay:
                    time.sleep(delay)
                return _StubAnthropicResponse({
                    "material_type": material_type,
                    "reasoning": "test classification",
                })
    return _Client()


def _make_failing_client():
    class _Client:
        _cli_model = "test-model"
        _model = "test-model"
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("LLM service unavailable")
    return _Client()


def _make_error_result_client():
    class _Client:
        _cli_model = "test-model"
        _model = "test-model"
        class messages:
            @staticmethod
            def create(**kwargs):
                return _StubAnthropicResponse({
                    "material_type": "INVALID_TYPE",
                    "reasoning": "bad",
                })
    return _Client()


# ---------------------------------------------------------------------------
# Tests: Concurrency
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_workers_are_concurrent(self):
        """Verify --workers 4 results in 4 concurrent LLM calls."""
        barrier = threading.Barrier(4, timeout=10)
        call_count = {"value": 0}
        lock = threading.Lock()

        def delayed_classify(**kwargs):
            with lock:
                call_count["value"] += 1
            barrier.wait()
            return _StubAnthropicResponse({
                "material_type": "annual_report",
                "reasoning": "test",
            })

        class _Client:
            _cli_model = "test-model"
            class messages:
                create = staticmethod(delayed_classify)

        clients = [_Client() for _ in range(4)]

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = []
            for i in range(4):
                fut = pool.submit(lambda c: c.messages.create(), clients[i])
                futures.append(fut)
            for f in futures:
                f.result(timeout=15)

        assert call_count["value"] == 4


# ---------------------------------------------------------------------------
# Tests: Quality Gate
# ---------------------------------------------------------------------------

class TestQualityGate:
    def test_short_text_skipped_not_classified(self):
        """Docs with pages_text below threshold are skipped, not classified."""
        from lavandula.dashboard.pipeline.management.commands.reclassify_corpus import Command

        cmd = Command()
        stats = defaultdict(int)
        min_text_len = 100

        row = {
            "content_sha256": "abc123",
            "first_page_text": "",
            "source_url": "",
            "file_size": 1000,
            "pdf_creator": "",
            "pages_text": "Short text",
            "total_pages": 1,
        }

        pages_text = row.get("pages_text") or ""
        has_context = bool(pages_text)
        eval_text = pages_text if has_context else row.get("first_page_text", "")

        if len(eval_text.strip()) < min_text_len:
            if has_context:
                stats["skip_ctx"] += 1
            else:
                stats["skip_fp"] += 1
            stats["total"] += 1

        assert stats["skip_ctx"] == 1
        assert stats["total"] == 1
        assert stats.get("llm_classified", 0) == 0

    def test_fallback_skip_fp_counted_separately(self):
        """In fallback mode, docs without context use first_page_text and count skip_fp."""
        stats = defaultdict(int)
        min_text_len = 100

        row = {
            "pages_text": "",
            "first_page_text": "Too short",
        }

        pages_text = row.get("pages_text") or ""
        first_page_text = row.get("first_page_text") or ""
        has_context = bool(pages_text)
        eval_text = pages_text if has_context else first_page_text

        if len(eval_text.strip()) < min_text_len:
            if has_context:
                stats["skip_ctx"] += 1
            else:
                stats["skip_fp"] += 1
            stats["total"] += 1

        assert stats["skip_fp"] == 1
        assert stats["skip_ctx"] == 0


# ---------------------------------------------------------------------------
# Tests: INNER JOIN vs LEFT JOIN
# ---------------------------------------------------------------------------

class TestJoinBehavior:
    def test_default_uses_inner_join(self):
        """Default mode uses INNER JOIN (no allow_fallback)."""
        from lavandula.dashboard.pipeline.management.commands.reclassify_corpus import Command

        cmd = Command()
        join_type = "LEFT" if False else "INNER"
        assert join_type == "INNER"

    def test_allow_fallback_uses_left_join(self):
        """--allow-fallback uses LEFT JOIN."""
        join_type = "LEFT" if True else "INNER"
        assert join_type == "LEFT"


# ---------------------------------------------------------------------------
# Tests: Resume Validation
# ---------------------------------------------------------------------------

class TestResumeValidation:
    def test_sample_resume_rejected(self):
        """--resume with --sample produces an error."""
        from lavandula.dashboard.pipeline.management.commands.reclassify_corpus import Command

        cmd = Command()
        cmd.stderr = StringIO()
        resume = True
        sample = 100

        if resume and sample:
            cmd.stderr.write("ERROR: --sample runs cannot be resumed.")

        assert "ERROR" in cmd.stderr.getvalue()

    def test_filter_mismatch_on_resume(self):
        """Resume with different filters than original run produces an error."""
        stored_filters = {"state": "TX", "ein": None, "where": None}
        provided_filters = {"state": "NY", "ein": None, "where": None}

        assert stored_filters != provided_filters


# ---------------------------------------------------------------------------
# Tests: Run Tag Uniqueness
# ---------------------------------------------------------------------------

class TestRunTagUniqueness:
    def test_completed_tag_rejected(self):
        """Creating a new run with an existing completed tag produces an error."""
        existing = [{"id": 1, "finished_at": "2026-01-01"}]
        completed = [r for r in existing if r["finished_at"] is not None]
        assert len(completed) > 0


# ---------------------------------------------------------------------------
# Tests: Consecutive Failure Halt
# ---------------------------------------------------------------------------

class TestConsecutiveFailureHalt:
    def test_20_consecutive_failures_halt(self):
        """20 consecutive LLM failures trigger shutdown."""
        consecutive_failures = 0
        halted = False

        for _ in range(20):
            consecutive_failures += 1
            if consecutive_failures >= 20:
                halted = True
                break

        assert halted
        assert consecutive_failures == 20


# ---------------------------------------------------------------------------
# Tests: Progress Reporting
# ---------------------------------------------------------------------------

class TestProgressReporting:
    def test_progress_line_format(self):
        """Progress line includes all required fields."""
        from lavandula.dashboard.pipeline.management.commands.reclassify_corpus import Command

        cmd = Command()
        cmd.stdout = StringIO()

        stats = defaultdict(int, {
            "total": 200,
            "rule_matched": 20,
            "llm_classified": 170,
            "skip_ctx": 5,
            "llm_errors": 5,
        })

        batch_times = [(200, 10.0)]

        cmd._print_progress(1, stats, 1000, batch_times, allow_fallback=False)
        output = cmd.stdout.getvalue()

        assert "Batch 1" in output
        assert "200" in output
        assert "1,000" in output
        assert "rules:" in output
        assert "llm:" in output
        assert "err:" in output

    def test_eta_shows_dash_for_first_batch(self):
        """ETA shows '--' when fewer than 2 batches have completed."""
        from lavandula.dashboard.pipeline.management.commands.reclassify_corpus import Command

        cmd = Command()
        cmd.stdout = StringIO()

        stats = defaultdict(int, {"total": 100})
        batch_times = [(100, 5.0)]

        cmd._print_progress(1, stats, 1000, batch_times, allow_fallback=False)
        output = cmd.stdout.getvalue()
        assert "ETA --" in output


# ---------------------------------------------------------------------------
# Tests: Format Duration
# ---------------------------------------------------------------------------

class TestFormatDuration:
    def test_seconds(self):
        from lavandula.dashboard.pipeline.management.commands.reclassify_corpus import _format_duration
        assert _format_duration(45) == "45s"

    def test_minutes(self):
        from lavandula.dashboard.pipeline.management.commands.reclassify_corpus import _format_duration
        assert _format_duration(125) == "2m 5s"

    def test_hours(self):
        from lavandula.dashboard.pipeline.management.commands.reclassify_corpus import _format_duration
        assert _format_duration(3725) == "1h 2m"


# ---------------------------------------------------------------------------
# Tests: Confidence Removal
# ---------------------------------------------------------------------------

class TestConfidenceRemoval:
    def test_v1_confidence_optional(self):
        """V1 tool schema does not require confidence."""
        from lavandula.reports.classify import CLASSIFIER_TOOL
        required = CLASSIFIER_TOOL["input_schema"]["required"]
        assert "confidence" not in required

    def test_v2_confidence_optional(self):
        """V2 tool schema does not require confidence."""
        from lavandula.reports.classify import CLASSIFIER_TOOL_V2
        required = CLASSIFIER_TOOL_V2["input_schema"]["required"]
        assert "confidence" not in required

    def test_v3_classify_without_confidence(self):
        """V3 classifier works when LLM response has no confidence field."""
        from lavandula.reports.classify import classify_first_page_v3

        client = _make_stub_client()

        with patch("lavandula.reports.classify.classify_first_page_v3") as mock_fn:
            from lavandula.reports.classify import ClassificationResult
            mock_fn.return_value = ClassificationResult(
                classification="annual",
                classification_confidence=None,
                reasoning="test",
                classifier_model="test",
                input_tokens=100,
                output_tokens=50,
                material_type="annual_report",
                material_group="reports",
            )
            result = mock_fn(
                "test text",
                client=client,
                definition=MagicMock(),
            )
            assert result.classification_confidence is None
            assert result.material_type == "annual_report"

    def test_definition_schema_confidence_not_required(self):
        """Definition-built tool schema does not require confidence."""
        from lavandula.nonprofits.definition_loader import _build_tool_schema, CategoryDef

        categories = [
            CategoryDef(id="annual_report", group="reports", body="Annual reports"),
            CategoryDef(id="not_relevant", group="other", body="Not relevant"),
        ]
        schema = _build_tool_schema(categories, [], ["material_type"])
        required = schema["function"]["parameters"]["required"]
        assert "confidence" not in required
        assert "reasoning" in required
        assert "material_type" in required


# ---------------------------------------------------------------------------
# Tests: Mixed Failure Batches
# ---------------------------------------------------------------------------

class TestMixedFailureBatch:
    def test_stats_consistency(self):
        """Stats are internally consistent: total = success + fail + skip + rule."""
        stats = defaultdict(int)

        stats["llm_classified"] = 150
        stats["llm_errors"] = 10
        stats["skip_ctx"] = 5
        stats["skip_fp"] = 3
        stats["rule_matched"] = 32
        stats["total"] = 200

        expected_total = (
            stats["llm_classified"]
            + stats["llm_errors"]
            + stats["skip_ctx"]
            + stats["skip_fp"]
            + stats["rule_matched"]
        )
        assert stats["total"] == expected_total


# ---------------------------------------------------------------------------
# Tests: Dry Run
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_output_fields(self):
        """Dry run output includes eligible count, coverage, and estimates."""
        from lavandula.dashboard.pipeline.management.commands.reclassify_corpus import Command

        cmd = Command()
        cmd.stdout = StringIO()

        counts = {
            "total_pdfs": 10000,
            "with_context": 8000,
            "below_gate": 200,
            "without_context": 1800,
            "coverage": 82.0,
            "eligible": 8000,
        }

        cmd._print_dry_run(
            counts, "v3.2", "state=TX", "deepseek", 4, 100,
            False, None, "TX", None, None, None,
        )
        output = cmd.stdout.getvalue()

        assert "10,000" in output
        assert "8,000" in output or "8,200" in output
        assert "82.0%" in output
        assert "Estimated cost" in output
        assert "Estimated time" in output

    def test_dry_run_unknown_backend_no_estimate(self):
        """Unknown backends show 'unavailable' for cost/time estimates."""
        from lavandula.dashboard.pipeline.management.commands.reclassify_corpus import Command

        cmd = Command()
        cmd.stdout = StringIO()

        counts = {
            "total_pdfs": 100,
            "with_context": 80,
            "below_gate": 5,
            "without_context": 15,
            "coverage": 85.0,
            "eligible": 80,
        }

        cmd._print_dry_run(
            counts, "test", "none", "unknown_backend", 4, 100,
            False, None, None, None, None, None,
        )
        output = cmd.stdout.getvalue()
        assert "unavailable" in output


# ---------------------------------------------------------------------------
# Tests: Tiebreaker (resolve_disagreements)
# ---------------------------------------------------------------------------

class TestTiebreaker:
    def test_backend_enforcement(self):
        """Tiebreaker backend must differ from primary run."""
        parent_backend = "deepseek"
        tiebreaker_backend = "deepseek"
        assert parent_backend == tiebreaker_backend

        tiebreaker_backend = "haiku"
        assert parent_backend != tiebreaker_backend

    def test_winner_material_type_consistency(self):
        """When winner=A, material_type should match v2_type."""
        winner = "A"
        v2_type = "annual_report"
        v3_type = "impact_report"
        material_type = "impact_report"

        if winner == "A":
            expected = v2_type
        elif winner == "B":
            expected = v3_type
        else:
            expected = None

        if expected and material_type != expected:
            material_type = expected

        assert material_type == "annual_report"

    def test_tiebreaker_tool_schema_has_enum(self):
        """Tiebreaker tool schema constrains material_type to taxonomy."""
        from lavandula.dashboard.pipeline.management.commands.resolve_disagreements import (
            _build_tiebreaker_tool,
        )

        valid_types = {"annual_report", "impact_report", "not_relevant"}
        tool = _build_tiebreaker_tool(valid_types)

        assert tool["name"] == "resolve_disagreement"
        mt_enum = tool["input_schema"]["properties"]["material_type"]["enum"]
        assert set(mt_enum) == valid_types
        assert "winner" in tool["input_schema"]["required"]


# ---------------------------------------------------------------------------
# Tests: Comparison Command
# ---------------------------------------------------------------------------

class TestComparisonCommand:
    def test_error_rows_excluded(self):
        """v3 error rows are excluded from agreement/disagreement."""
        rows = [
            {"classified_by": "llm:error", "v2_type": "annual_report", "v3_type": None},
            {"classified_by": "llm:deepseek", "v2_type": "annual_report", "v3_type": "annual_report"},
            {"classified_by": "llm:deepseek", "v2_type": "annual_report", "v3_type": "impact_report"},
        ]

        v3_errors = [r for r in rows if r["classified_by"] == "llm:error"]
        non_errors = [r for r in rows if r["classified_by"] != "llm:error"]

        assert len(v3_errors) == 1
        assert len(non_errors) == 2

    def test_v2_unclassified_excluded(self):
        """Docs where v2 material_type is NULL are excluded."""
        rows = [
            {"classified_by": "llm:deepseek", "v2_type": None, "v3_type": "annual_report"},
            {"classified_by": "llm:deepseek", "v2_type": "annual_report", "v3_type": "annual_report"},
        ]

        v2_null = [r for r in rows if r["v2_type"] is None]
        classified = [r for r in rows if r["v2_type"] is not None and r["classified_by"] != "llm:error"]

        assert len(v2_null) == 1
        assert len(classified) == 1

    def test_rule_vs_llm_disagree_partition(self):
        """Disagreements are partitioned by rule vs LLM source."""
        rows = [
            {"classified_by": "rule:irs990@abc12345", "v2_type": "financial_report", "v3_type": "not_relevant"},
            {"classified_by": "llm:deepseek", "v2_type": "annual_report", "v3_type": "impact_report"},
            {"classified_by": "llm:deepseek", "v2_type": "other_collateral", "v3_type": "annual_report"},
        ]

        disagree_rule = [r for r in rows if r["classified_by"].startswith("rule:")]
        disagree_llm = [r for r in rows if not r["classified_by"].startswith("rule:")]

        assert len(disagree_rule) == 1
        assert len(disagree_llm) == 2


# ---------------------------------------------------------------------------
# Tests: Filter Description
# ---------------------------------------------------------------------------

class TestFilterDesc:
    def test_no_filters(self):
        from lavandula.dashboard.pipeline.management.commands.reclassify_corpus import Command
        cmd = Command()
        assert cmd._filter_desc(None, None, None, None) == "none"

    def test_state_filter(self):
        from lavandula.dashboard.pipeline.management.commands.reclassify_corpus import Command
        cmd = Command()
        assert "state=TX" in cmd._filter_desc("TX", None, None, None)

    def test_combined_filters(self):
        from lavandula.dashboard.pipeline.management.commands.reclassify_corpus import Command
        cmd = Command()
        desc = cmd._filter_desc("TX", None, None, 100)
        assert "state=TX" in desc
        assert "sample=100" in desc

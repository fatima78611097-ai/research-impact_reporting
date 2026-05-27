"""Tests for the stage registry."""
from copy import deepcopy
from dataclasses import replace

from django.test import SimpleTestCase

from pipeline.stages import (
    ParamSpec,
    RetryPolicy,
    STAGE_REGISTRY,
    StageDefinition,
    validate_registry,
)


class TestStageRegistry(SimpleTestCase):
    def test_registry_is_valid(self):
        errors = validate_registry()
        self.assertEqual(errors, [], f"Registry validation errors: {errors}")

    def test_all_existing_stages_registered(self):
        expected = {
            "seed", "resolve", "crawl", "classify", "990-index", "990-parse", "enrich-phone",
            "extract-context", "reclassify", "compare-classify", "resolve-disagree", "promote-classify",
            "parse",
        }
        self.assertEqual(set(STAGE_REGISTRY.keys()), expected)

    def test_stage_names_match_keys(self):
        for key, stage in STAGE_REGISTRY.items():
            self.assertEqual(key, stage.name)

    def test_all_stages_have_commands(self):
        for stage in STAGE_REGISTRY.values():
            self.assertIsInstance(stage.command, list)
            self.assertTrue(len(stage.command) >= 1)

    def test_all_stages_have_valid_resource_class(self):
        for stage in STAGE_REGISTRY.values():
            self.assertIn(stage.resource_class, ("heavy", "medium", "light"))

    def test_provenance_columns_unique(self):
        columns = [s.provenance_column for s in STAGE_REGISTRY.values() if s.provenance_column]
        self.assertEqual(len(columns), len(set(columns)))


class TestRegistryValidation(SimpleTestCase):
    def _make_registry(self, **overrides):
        reg = deepcopy(STAGE_REGISTRY)
        reg.update(overrides)
        return reg

    def test_missing_predecessor(self):
        bad_stage = StageDefinition(
            name="bad",
            display_name="Bad",
            command=["echo"],
            parameters={},
            predecessors=["nonexistent"],
            conflict_group=None,
            provenance_column=None,
            retry_policy=RetryPolicy(),
            resource_class="light",
        )
        reg = self._make_registry(bad=bad_stage)
        errors = validate_registry(reg)
        self.assertTrue(any("nonexistent" in e for e in errors))

    def test_duplicate_provenance_column(self):
        dupe = StageDefinition(
            name="dupe",
            display_name="Dupe",
            command=["echo"],
            parameters={},
            predecessors=[],
            conflict_group=None,
            provenance_column="seed_status",  # already used by seed
            retry_policy=RetryPolicy(),
            resource_class="light",
        )
        reg = self._make_registry(dupe=dupe)
        errors = validate_registry(reg)
        self.assertTrue(any("seed_status" in e for e in errors))

    def test_circular_predecessors(self):
        a = StageDefinition(
            name="a", display_name="A", command=["echo"], parameters={},
            predecessors=["b"], conflict_group=None, provenance_column=None,
            retry_policy=RetryPolicy(), resource_class="light",
        )
        b = StageDefinition(
            name="b", display_name="B", command=["echo"], parameters={},
            predecessors=["a"], conflict_group=None, provenance_column=None,
            retry_policy=RetryPolicy(), resource_class="light",
        )
        errors = validate_registry({"a": a, "b": b})
        self.assertTrue(any("Circular" in e for e in errors))

    def test_invalid_conflict_group(self):
        bad = StageDefinition(
            name="bad", display_name="Bad", command=["echo"], parameters={},
            predecessors=[], conflict_group="invalid-group", provenance_column=None,
            retry_policy=RetryPolicy(), resource_class="light",
        )
        errors = validate_registry({"bad": bad})
        self.assertTrue(any("conflict_group" in e for e in errors))

    def test_invalid_resource_class(self):
        bad = StageDefinition(
            name="bad", display_name="Bad", command=["echo"], parameters={},
            predecessors=[], conflict_group=None, provenance_column=None,
            retry_policy=RetryPolicy(), resource_class="enormous",
        )
        errors = validate_registry({"bad": bad})
        self.assertTrue(any("resource_class" in e for e in errors))

    def test_key_name_mismatch(self):
        stage = StageDefinition(
            name="actual_name", display_name="X", command=["echo"], parameters={},
            predecessors=[], conflict_group=None, provenance_column=None,
            retry_policy=RetryPolicy(), resource_class="light",
        )
        errors = validate_registry({"wrong_key": stage})
        self.assertTrue(any("doesn't match" in e for e in errors))


class TestRetryPolicy(SimpleTestCase):
    def test_max_attempts_capped_at_3(self):
        policy = RetryPolicy(max_attempts=10)
        self.assertEqual(policy.max_attempts, 3)

    def test_min_attempts_is_1(self):
        policy = RetryPolicy(max_attempts=0)
        self.assertEqual(policy.max_attempts, 1)

    def test_default_policy(self):
        policy = RetryPolicy()
        self.assertEqual(policy.max_attempts, 1)
        self.assertFalse(policy.auto_retry)
        self.assertEqual(policy.backoff_seconds, 60)
        self.assertEqual(policy.retryable_exit_codes, (1, 3))

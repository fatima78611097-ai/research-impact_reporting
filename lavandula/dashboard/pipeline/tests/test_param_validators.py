"""Tests for parameter validation and argv construction."""
from django.test import SimpleTestCase

from pipeline.param_validators import (
    ValidationError,
    build_argv,
    build_argv_for_phase,
    validate_config,
    validate_param,
)
from pipeline.stages import ParamSpec, RetryPolicy, StageDefinition, STAGE_REGISTRY


class TestValidateParam(SimpleTestCase):
    def test_state_code_valid(self):
        spec = ParamSpec(type="state_code")
        self.assertEqual(validate_param("state", "CA", spec), "CA")
        self.assertEqual(validate_param("state", "ca", spec), "CA")  # case insensitive

    def test_state_code_invalid(self):
        spec = ParamSpec(type="state_code")
        with self.assertRaises(ValidationError):
            validate_param("state", "XX", spec)

    def test_state_code_territories(self):
        spec = ParamSpec(type="state_code")
        self.assertEqual(validate_param("state", "DC", spec), "DC")
        self.assertEqual(validate_param("state", "PR", spec), "PR")
        self.assertEqual(validate_param("state", "GU", spec), "GU")

    def test_integer_valid(self):
        spec = ParamSpec(type="integer", min_value=1, max_value=100)
        self.assertEqual(validate_param("n", "50", spec), "50")
        self.assertEqual(validate_param("n", 50, spec), "50")

    def test_integer_below_min(self):
        spec = ParamSpec(type="integer", min_value=1, max_value=100)
        with self.assertRaises(ValidationError):
            validate_param("n", "0", spec)

    def test_integer_above_max(self):
        spec = ParamSpec(type="integer", min_value=1, max_value=100)
        with self.assertRaises(ValidationError):
            validate_param("n", "101", spec)

    def test_integer_not_a_number(self):
        spec = ParamSpec(type="integer")
        with self.assertRaises(ValidationError):
            validate_param("n", "abc", spec)

    def test_float_valid(self):
        spec = ParamSpec(type="float", min_value=0, max_value=50)
        self.assertEqual(validate_param("qps", "10.5", spec), "10.5")

    def test_float_invalid(self):
        spec = ParamSpec(type="float")
        with self.assertRaises(ValidationError):
            validate_param("qps", "not-a-float", spec)

    def test_boolean_true_variants(self):
        spec = ParamSpec(type="boolean")
        self.assertIsNone(validate_param("flag", True, spec))
        self.assertIsNone(validate_param("flag", "true", spec))
        self.assertIsNone(validate_param("flag", "on", spec))

    def test_boolean_false_variants(self):
        spec = ParamSpec(type="boolean")
        self.assertEqual(validate_param("flag", False, spec), "")
        self.assertEqual(validate_param("flag", "false", spec), "")

    def test_boolean_invalid(self):
        spec = ParamSpec(type="boolean")
        with self.assertRaises(ValidationError):
            validate_param("flag", "maybe", spec)

    def test_string_valid(self):
        spec = ParamSpec(type="string")
        self.assertEqual(validate_param("name", "hello-world", spec), "hello-world")

    def test_string_shell_metachar_rejected(self):
        spec = ParamSpec(type="string")
        for bad in ["; rm -rf /", "$(whoami)", "`id`", "a|b", "a&b", "a\\b"]:
            with self.assertRaises(ValidationError, msg=f"Should reject: {bad!r}"):
                validate_param("name", bad, spec)

    def test_string_null_byte_rejected(self):
        spec = ParamSpec(type="string")
        with self.assertRaises(ValidationError):
            validate_param("name", "hello\x00world", spec)

    def test_string_too_long(self):
        spec = ParamSpec(type="string")
        with self.assertRaises(ValidationError):
            validate_param("name", "x" * 201, spec)

    def test_string_pattern_match(self):
        spec = ParamSpec(type="string", pattern=r"^\d{9}$")
        self.assertEqual(validate_param("ein", "123456789", spec), "123456789")

    def test_string_pattern_mismatch(self):
        spec = ParamSpec(type="string", pattern=r"^\d{9}$")
        with self.assertRaises(ValidationError):
            validate_param("ein", "12345", spec)

    def test_choice_valid(self):
        spec = ParamSpec(type="choice", choices=["brave", "google"])
        self.assertEqual(validate_param("engine", "brave", spec), "brave")

    def test_choice_invalid(self):
        spec = ParamSpec(type="choice", choices=["brave", "google"])
        with self.assertRaises(ValidationError):
            validate_param("engine", "bing", spec)

    def test_unknown_type_rejected(self):
        spec = ParamSpec(type="unknown_type")
        with self.assertRaises(ValidationError):
            validate_param("x", "val", spec)


class TestValidateConfig(SimpleTestCase):
    def test_unknown_param_rejected(self):
        stage = STAGE_REGISTRY["resolve"]
        with self.assertRaises(ValidationError) as ctx:
            validate_config(stage, {"nonexistent_param": "value"})
        self.assertIn("Unknown parameter", str(ctx.exception))

    def test_required_param_missing(self):
        stage = STAGE_REGISTRY["resolve"]
        with self.assertRaises(ValidationError) as ctx:
            validate_config(stage, {"limit": 100})  # missing required 'state'
        self.assertIn("required", str(ctx.exception))

    def test_valid_config(self):
        stage = STAGE_REGISTRY["resolve"]
        result = validate_config(stage, {"state": "CA", "limit": 100})
        self.assertEqual(result["state"], "CA")
        self.assertEqual(result["limit"], "100")

    def test_optional_params_skipped(self):
        stage = STAGE_REGISTRY["resolve"]
        result = validate_config(stage, {"state": "WA"})
        self.assertEqual(result, {"state": "WA"})


class TestBuildArgv(SimpleTestCase):
    def test_resolve_basic(self):
        stage = STAGE_REGISTRY["resolve"]
        argv = build_argv(stage, {"state": "CA"})
        self.assertEqual(argv[:4], ["python3", "-m", "lavandula.nonprofits.tools.pipeline_resolve", "--state"])
        self.assertIn("CA", argv)

    def test_resolve_with_options(self):
        stage = STAGE_REGISTRY["resolve"]
        argv = build_argv(stage, {"state": "WA", "limit": 500, "fresh_only": True})
        self.assertIn("--state", argv)
        self.assertIn("WA", argv)
        self.assertIn("--limit", argv)
        self.assertIn("500", argv)
        self.assertIn("--fresh-only", argv)

    def test_boolean_false_not_in_argv(self):
        stage = STAGE_REGISTRY["resolve"]
        argv = build_argv(stage, {"state": "NY", "fresh_only": False})
        self.assertNotIn("--fresh-only", argv)

    def test_crawl_with_archive(self):
        stage = STAGE_REGISTRY["crawl"]
        argv = build_argv(stage, {"archive": "s3://lavandula-nonprofit-collaterals"})
        self.assertIn("--archive", argv)
        self.assertIn("s3://lavandula-nonprofit-collaterals", argv)

    def test_990_index(self):
        stage = STAGE_REGISTRY["990-index"]
        argv = build_argv(stage, {"ein": "123456789", "current_year": True})
        self.assertIn("--ein", argv)
        self.assertIn("123456789", argv)
        self.assertIn("--current-year", argv)

    def test_invalid_param_raises(self):
        stage = STAGE_REGISTRY["resolve"]
        with self.assertRaises(ValidationError):
            build_argv(stage, {"state": "INVALID"})

    def test_shell_metachar_in_param_raises(self):
        stage = STAGE_REGISTRY["crawl"]
        with self.assertRaises(ValidationError):
            build_argv(stage, {"archive": "s3://bucket; rm -rf /"})

    def test_build_argv_for_phase_convenience(self):
        argv = build_argv_for_phase("seed", {"states": "CA", "target": 1000})
        self.assertEqual(argv[0], "python3")
        self.assertIn("--states", argv)
        self.assertIn("CA", argv)
        self.assertIn("--target", argv)
        self.assertIn("1000", argv)

    def test_build_argv_unknown_phase(self):
        with self.assertRaises(ValidationError):
            build_argv_for_phase("nonexistent", {})

    def test_command_is_list_not_string(self):
        for stage in STAGE_REGISTRY.values():
            argv = build_argv(stage, self._minimal_config(stage))
            self.assertIsInstance(argv, list)
            for element in argv:
                self.assertIsInstance(element, str)

    def _minimal_config(self, stage: StageDefinition) -> dict:
        """Build minimal valid config with only required params."""
        config = {}
        for name, spec in stage.parameters.items():
            if spec.required:
                if spec.type == "state_code":
                    config[name] = "CA"
                elif spec.type == "integer":
                    config[name] = spec.min_value or 1
                elif spec.type == "string":
                    config[name] = "test"
                elif spec.type == "boolean":
                    config[name] = True
        return config

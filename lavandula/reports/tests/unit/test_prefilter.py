"""Tests for the rule-based pre-filter engine (Spec 0035 Phase 3)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml


def _write_rules(rules: list[dict], tmp_path: Path) -> Path:
    """Write rules to a temporary YAML file."""
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.dump({"rules": rules}))
    return path


@pytest.fixture
def tmp_path():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


class TestRuleEngineLoading:
    def test_missing_file_raises(self):
        from lavandula.nonprofits.prefilter import RuleEngine
        with pytest.raises(RuntimeError, match="not found"):
            RuleEngine("/nonexistent/rules.yaml")

    def test_invalid_yaml_raises(self, tmp_path):
        from lavandula.nonprofits.prefilter import RuleEngine
        path = tmp_path / "rules.yaml"
        path.write_text(": :\n  bad yaml [")
        with pytest.raises(RuntimeError, match="Invalid YAML"):
            RuleEngine(path)

    def test_missing_rules_key_raises(self, tmp_path):
        from lavandula.nonprofits.prefilter import RuleEngine
        path = tmp_path / "rules.yaml"
        path.write_text(yaml.dump({"not_rules": []}))
        with pytest.raises(RuntimeError, match="'rules' key"):
            RuleEngine(path)

    def test_missing_required_field_raises(self, tmp_path):
        from lavandula.nonprofits.prefilter import RuleEngine
        rules = [{"name": "test", "conditions": {"text_contains_any": ["x"]}}]
        path = _write_rules(rules, tmp_path)
        with pytest.raises(RuntimeError, match="missing required field"):
            RuleEngine(path)

    def test_unknown_condition_type_raises(self, tmp_path):
        from lavandula.nonprofits.prefilter import RuleEngine
        rules = [{
            "name": "test", "material_type": "not_relevant",
            "confidence": 0.99, "reasoning": "test",
            "conditions": {"unknown_cond": "value"},
        }]
        path = _write_rules(rules, tmp_path)
        with pytest.raises(RuntimeError, match="unknown condition"):
            RuleEngine(path)

    def test_invalid_regex_raises(self, tmp_path):
        from lavandula.nonprofits.prefilter import RuleEngine
        rules = [{
            "name": "test", "material_type": "not_relevant",
            "confidence": 0.99, "reasoning": "test",
            "conditions": {"url_matches": "[invalid("},
        }]
        path = _write_rules(rules, tmp_path)
        with pytest.raises(RuntimeError, match="invalid regex"):
            RuleEngine(path)

    def test_sha256_computed(self, tmp_path):
        from lavandula.nonprofits.prefilter import RuleEngine
        rules = [{
            "name": "test", "material_type": "not_relevant",
            "confidence": 0.99, "reasoning": "test",
            "conditions": {"text_contains_any": ["hello"]},
        }]
        path = _write_rules(rules, tmp_path)
        engine = RuleEngine(path)
        assert len(engine.rules_sha256) == 64


class TestTextContainsAny:
    def test_basic_match(self, tmp_path):
        from lavandula.nonprofits.prefilter import RuleEngine
        rules = [{
            "name": "test_990", "material_type": "not_relevant",
            "confidence": 0.99, "reasoning": "Test rule",
            "conditions": {"text_contains_any": ["Form 990"]},
        }]
        engine = RuleEngine(_write_rules(rules, tmp_path))
        result = engine.evaluate(text="This is IRS Form 990-EZ filing")
        assert result is not None
        assert result.material_type == "not_relevant"
        assert result.rule_name == "test_990"

    def test_case_insensitive(self, tmp_path):
        from lavandula.nonprofits.prefilter import RuleEngine
        rules = [{
            "name": "test", "material_type": "not_relevant",
            "confidence": 0.99, "reasoning": "Test",
            "conditions": {"text_contains_any": ["FORM 990"]},
        }]
        engine = RuleEngine(_write_rules(rules, tmp_path))
        result = engine.evaluate(text="form 990")
        assert result is not None

    def test_whitespace_normalized(self, tmp_path):
        from lavandula.nonprofits.prefilter import RuleEngine
        rules = [{
            "name": "test", "material_type": "not_relevant",
            "confidence": 0.99, "reasoning": "Test",
            "conditions": {"text_contains_any": ["Form 990"]},
        }]
        engine = RuleEngine(_write_rules(rules, tmp_path))
        result = engine.evaluate(text="Form   \n 990")
        assert result is not None

    def test_apostrophe_normalized(self, tmp_path):
        from lavandula.nonprofits.prefilter import RuleEngine
        rules = [{
            "name": "test", "material_type": "not_relevant",
            "confidence": 0.99, "reasoning": "Test",
            "conditions": {"text_contains_any": ["organization's report"]},
        }]
        engine = RuleEngine(_write_rules(rules, tmp_path))
        result = engine.evaluate(text="organization’s report")
        assert result is not None

    def test_no_match(self, tmp_path):
        from lavandula.nonprofits.prefilter import RuleEngine
        rules = [{
            "name": "test", "material_type": "not_relevant",
            "confidence": 0.99, "reasoning": "Test",
            "conditions": {"text_contains_any": ["Form 990"]},
        }]
        engine = RuleEngine(_write_rules(rules, tmp_path))
        result = engine.evaluate(text="Annual Report 2024")
        assert result is None


class TestUrlMatches:
    def test_url_match(self, tmp_path):
        from lavandula.nonprofits.prefilter import RuleEngine
        rules = [{
            "name": "test", "material_type": "not_relevant",
            "confidence": 0.95, "reasoning": "URL match",
            "conditions": {"url_matches": "(?i)/990[^a-z]"},
        }]
        engine = RuleEngine(_write_rules(rules, tmp_path))
        result = engine.evaluate(url="https://example.org/990/filing.pdf")
        assert result is not None

    def test_url_no_match(self, tmp_path):
        from lavandula.nonprofits.prefilter import RuleEngine
        rules = [{
            "name": "test", "material_type": "not_relevant",
            "confidence": 0.95, "reasoning": "URL match",
            "conditions": {"url_matches": "(?i)/990[^a-z]"},
        }]
        engine = RuleEngine(_write_rules(rules, tmp_path))
        result = engine.evaluate(url="https://example.org/annual-report.pdf")
        assert result is None

    def test_url_capped_at_2048(self, tmp_path):
        from lavandula.nonprofits.prefilter import RuleEngine
        rules = [{
            "name": "test", "material_type": "not_relevant",
            "confidence": 0.95, "reasoning": "URL match",
            "conditions": {"url_matches": "990_at_end"},
        }]
        engine = RuleEngine(_write_rules(rules, tmp_path))
        long_url = "https://example.org/" + "a" * 3000 + "990_at_end"
        result = engine.evaluate(url=long_url)
        assert result is None


class TestPdfCreatorAny:
    def test_creator_match(self, tmp_path):
        from lavandula.nonprofits.prefilter import RuleEngine
        rules = [{
            "name": "test", "material_type": "not_relevant",
            "confidence": 0.95, "reasoning": "Creator match",
            "conditions": {
                "pdf_creator_any": ["ProSystem fx"],
                "text_contains_any": ["Form 990"],
            },
        }]
        engine = RuleEngine(_write_rules(rules, tmp_path))
        result = engine.evaluate(text="Form 990 filing", pdf_creator="ProSystem fx 2024")
        assert result is not None

    def test_creator_no_match_without_text(self, tmp_path):
        from lavandula.nonprofits.prefilter import RuleEngine
        rules = [{
            "name": "test", "material_type": "not_relevant",
            "confidence": 0.95, "reasoning": "Creator match",
            "conditions": {
                "pdf_creator_any": ["ProSystem fx"],
                "text_contains_any": ["Form 990"],
            },
        }]
        engine = RuleEngine(_write_rules(rules, tmp_path))
        result = engine.evaluate(text="Annual Report", pdf_creator="ProSystem fx 2024")
        assert result is None


class TestTextNotContains:
    def test_negative_guard(self, tmp_path):
        from lavandula.nonprofits.prefilter import RuleEngine
        rules = [{
            "name": "test", "material_type": "financial_report",
            "confidence": 0.90, "reasoning": "Audit report",
            "conditions": {
                "text_contains_any": ["Independent Auditor"],
                "text_not_contains": ["Annual Report"],
            },
        }]
        engine = RuleEngine(_write_rules(rules, tmp_path))
        result = engine.evaluate(text="Independent Auditor Statement for Annual Report")
        assert result is None

    def test_passes_without_negative(self, tmp_path):
        from lavandula.nonprofits.prefilter import RuleEngine
        rules = [{
            "name": "test", "material_type": "financial_report",
            "confidence": 0.90, "reasoning": "Audit report",
            "conditions": {
                "text_contains_any": ["Independent Auditor"],
                "text_not_contains": ["Annual Report"],
            },
        }]
        engine = RuleEngine(_write_rules(rules, tmp_path))
        result = engine.evaluate(text="Independent Auditor Statement FY2024")
        assert result is not None
        assert result.material_type == "financial_report"


class TestPriorityOrdering:
    def test_first_match_wins(self, tmp_path):
        from lavandula.nonprofits.prefilter import RuleEngine
        rules = [
            {
                "name": "rule_a", "material_type": "not_relevant",
                "confidence": 0.99, "reasoning": "Rule A",
                "conditions": {"text_contains_any": ["Form 990"]},
            },
            {
                "name": "rule_b", "material_type": "financial_report",
                "confidence": 0.90, "reasoning": "Rule B",
                "conditions": {"text_contains_any": ["Form 990"]},
            },
        ]
        engine = RuleEngine(_write_rules(rules, tmp_path))
        result = engine.evaluate(text="Form 990 filing")
        assert result is not None
        assert result.rule_name == "rule_a"
        assert result.material_type == "not_relevant"


class TestRulesYAMLFile:
    def test_production_rules_load(self):
        from lavandula.nonprofits.prefilter import RuleEngine
        rules_path = Path(__file__).resolve().parents[3] / "nonprofits" / "definitions" / "prefilter_rules.yaml"
        if not rules_path.is_file():
            pytest.skip("Production rules file not found")
        engine = RuleEngine(rules_path)
        assert len(engine.rules_sha256) == 64

    def test_production_rules_match_990(self):
        from lavandula.nonprofits.prefilter import RuleEngine
        rules_path = Path(__file__).resolve().parents[3] / "nonprofits" / "definitions" / "prefilter_rules.yaml"
        if not rules_path.is_file():
            pytest.skip("Production rules file not found")
        engine = RuleEngine(rules_path)
        result = engine.evaluate(text="OMB No. 1545-0047 Return of Organization Exempt From Income Tax")
        assert result is not None
        assert result.material_type == "not_relevant"
        assert result.rule_name == "irs_990_text"

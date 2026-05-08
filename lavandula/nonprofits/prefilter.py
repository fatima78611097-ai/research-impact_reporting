"""Rule-based pre-filter for deterministic classification (Spec 0035).

Loads rules from a YAML file and evaluates them against document metadata.
Rules are applied in YAML order; first match wins. Returns None if no
rule matches, signaling fallthrough to the LLM classifier.

Uses:
  - pyahocorasick for multi-pattern text matching (constant time per input)
  - regex package (not stdlib re) for URL matching with per-match timeout
  - yaml.safe_load exclusively for YAML parsing
"""
from __future__ import annotations

import hashlib
import logging
import re as stdlib_re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import ahocorasick
import regex
import yaml

log = logging.getLogger(__name__)

_URL_MAX_LEN = 2048
_REGEX_TIMEOUT_SEC = 1.0
_REGEX_LOAD_TEST_TIMEOUT_MS = 100
_ADVERSARIAL_STRING = "a" * 5000

_NESTED_QUANTIFIER_RE = stdlib_re.compile(
    r"\(.*[*+].*\)[*+]"
    r"|"
    r"\(.*\)\{.*\}[*+]"
)

_APOSTROPHE_MAP = str.maketrans({
    "‘": "'",  # left single curly
    "’": "'",  # right single curly
    "‚": "'",  # single low-9
    "`": "'",  # backtick
})


def _normalize_text(text: str) -> str:
    """Lowercase, whitespace-normalize, apostrophe-normalize."""
    text = text.lower()
    text = text.translate(_APOSTROPHE_MAP)
    text = " ".join(text.split())
    return text


@dataclass(frozen=True)
class RuleMatch:
    material_type: str
    confidence: float
    reasoning: str
    rule_name: str
    rules_sha256: str


@dataclass(frozen=True)
class _Rule:
    name: str
    material_type: str
    confidence: float
    reasoning: str
    text_contains_any: list[str] | None
    text_not_contains: list[str] | None
    url_matches: str | None
    pdf_creator_any: list[str] | None


class RuleEngine:
    """Evaluate prefilter rules against document metadata."""

    def __init__(self, yaml_path: Path | str) -> None:
        yaml_path = Path(yaml_path)
        if not yaml_path.is_file():
            raise RuntimeError(f"Prefilter rules file not found: {yaml_path}")

        raw = yaml_path.read_text(encoding="utf-8")
        self._rules_sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()

        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise RuntimeError(f"Invalid YAML in prefilter rules: {exc}") from exc

        if not isinstance(data, dict) or "rules" not in data:
            raise RuntimeError("Prefilter rules YAML must contain a 'rules' key")

        rules_list = data["rules"]
        if not isinstance(rules_list, list):
            raise RuntimeError("'rules' must be a list")

        self._rules: list[_Rule] = []
        self._automata: dict[str, ahocorasick.Automaton] = {}

        for i, rule_data in enumerate(rules_list):
            rule = self._parse_rule(rule_data, i)
            self._rules.append(rule)

        self._build_automata()

    @property
    def rules_sha256(self) -> str:
        return self._rules_sha256

    def _parse_rule(self, rule_data: dict, index: int) -> _Rule:
        if not isinstance(rule_data, dict):
            raise RuntimeError(f"Rule at index {index} is not a mapping")

        for field in ("name", "material_type", "confidence", "reasoning", "conditions"):
            if field not in rule_data:
                raise RuntimeError(f"Rule at index {index} missing required field: {field}")

        name = rule_data["name"]
        conditions = rule_data["conditions"]
        if not isinstance(conditions, dict):
            raise RuntimeError(f"Rule {name!r}: conditions must be a mapping")

        known_conditions = {"text_contains_any", "text_not_contains", "url_matches",
                            "pdf_creator_any"}
        unknown = set(conditions.keys()) - known_conditions
        if unknown:
            raise RuntimeError(f"Rule {name!r}: unknown condition types: {unknown}")

        url_pattern = conditions.get("url_matches")
        if url_pattern is not None:
            self._validate_regex(url_pattern, name)

        return _Rule(
            name=name,
            material_type=rule_data["material_type"],
            confidence=float(rule_data["confidence"]),
            reasoning=rule_data["reasoning"],
            text_contains_any=conditions.get("text_contains_any"),
            text_not_contains=conditions.get("text_not_contains"),
            url_matches=url_pattern,
            pdf_creator_any=conditions.get("pdf_creator_any"),
        )

    def _validate_regex(self, pattern: str, rule_name: str) -> None:
        if _NESTED_QUANTIFIER_RE.search(pattern):
            raise RuntimeError(
                f"Rule {rule_name!r}: regex contains nested quantifiers (ReDoS risk): {pattern!r}"
            )
        try:
            compiled = regex.compile(pattern, flags=regex.IGNORECASE)
        except regex.error as exc:
            raise RuntimeError(
                f"Rule {rule_name!r}: invalid regex {pattern!r}: {exc}"
            ) from exc

        try:
            compiled.search(_ADVERSARIAL_STRING, timeout=_REGEX_LOAD_TEST_TIMEOUT_MS / 1000)
        except TimeoutError:
            raise RuntimeError(
                f"Rule {rule_name!r}: regex {pattern!r} timed out on adversarial test string"
            )

    def _build_automata(self) -> None:
        for rule in self._rules:
            if rule.text_contains_any:
                key = f"contains:{rule.name}"
                auto = ahocorasick.Automaton()
                for pattern in rule.text_contains_any:
                    normalized = _normalize_text(pattern)
                    auto.add_word(normalized, normalized)
                auto.make_automaton()
                self._automata[key] = auto

            if rule.text_not_contains:
                key = f"not_contains:{rule.name}"
                auto = ahocorasick.Automaton()
                for pattern in rule.text_not_contains:
                    normalized = _normalize_text(pattern)
                    auto.add_word(normalized, normalized)
                auto.make_automaton()
                self._automata[key] = auto

    def evaluate(
        self,
        text: str = "",
        url: str = "",
        pdf_creator: str = "",
        page_count: int | None = None,
        file_size: int | None = None,
    ) -> RuleMatch | None:
        """Evaluate rules against document metadata. First match wins."""
        normalized_text = _normalize_text(text) if text else ""
        normalized_creator = _normalize_text(pdf_creator) if pdf_creator else ""

        for rule in self._rules:
            if self._rule_matches(rule, normalized_text, url, normalized_creator):
                return RuleMatch(
                    material_type=rule.material_type,
                    confidence=rule.confidence,
                    reasoning=rule.reasoning,
                    rule_name=rule.name,
                    rules_sha256=self._rules_sha256,
                )
        return None

    def _rule_matches(
        self, rule: _Rule, normalized_text: str, url: str, normalized_creator: str,
    ) -> bool:
        conditions_met = []

        if rule.text_contains_any is not None:
            key = f"contains:{rule.name}"
            auto = self._automata.get(key)
            if auto is None:
                return False
            found = False
            if normalized_text:
                for _ in auto.iter(normalized_text):
                    found = True
                    break
            if not found:
                return False
            conditions_met.append("text_contains_any")

        if rule.text_not_contains is not None:
            key = f"not_contains:{rule.name}"
            auto = self._automata.get(key)
            if auto is not None and normalized_text:
                for _ in auto.iter(normalized_text):
                    return False

        if rule.url_matches is not None:
            capped_url = url[:_URL_MAX_LEN] if url else ""
            if not capped_url:
                return False
            try:
                match = regex.search(
                    rule.url_matches, capped_url,
                    flags=regex.IGNORECASE, timeout=_REGEX_TIMEOUT_SEC,
                )
            except TimeoutError:
                log.warning("Regex timeout for rule %s on URL", rule.name)
                return False
            if not match:
                return False
            conditions_met.append("url_matches")

        if rule.pdf_creator_any is not None:
            if not normalized_creator:
                return False
            found = False
            for creator_pattern in rule.pdf_creator_any:
                if _normalize_text(creator_pattern) in normalized_creator:
                    found = True
                    break
            if not found:
                return False
            conditions_met.append("pdf_creator_any")

        if not conditions_met and rule.text_contains_any is None and \
           rule.url_matches is None and rule.pdf_creator_any is None:
            return False

        return True


__all__ = ["RuleEngine", "RuleMatch"]

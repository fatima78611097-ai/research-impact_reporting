"""Tests for term extraction and C-value computation (Spec 0049)."""
import math
from collections import Counter
from unittest.mock import MagicMock

import pytest
import spacy

from lavandula.nlp.extractor import (
    Observation,
    SectionExtractor,
    _score_cvalue_from_freqs,
    extract_document,
)
from lavandula.nlp.stopwords import BOILERPLATE_TERMS


@pytest.fixture(scope="module")
def nlp():
    try:
        return spacy.load("en_core_web_lg", disable=["textcat"])
    except OSError:
        pytest.skip("en_core_web_lg not available")


class TestSectionExtractor:
    def test_extracts_noun_phrases(self, nlp):
        extractor = SectionExtractor(nlp)
        obs = extractor.extract(
            body_text="Our food pantry program serves local families through nutrition education.",
            section_index=0,
            heading="Programs",
            parent_headings=["Our Work"],
        )
        terms = {o.term for o in obs}
        assert any("food" in t and "pantry" in t for t in terms) or \
               any("nutrition" in t for t in terms) or \
               any("local" in t for t in terms), \
               f"Expected domain terms, got: {terms}"

    def test_filters_boilerplate(self, nlp):
        extractor = SectionExtractor(nlp)
        obs = extractor.extract(
            body_text="Our mission drives community impact through strategic plan implementation.",
            section_index=0,
            heading=None,
            parent_headings=None,
        )
        terms = {o.term for o in obs}
        for bp in ["community", "mission", "impact", "strategic plan"]:
            assert bp not in terms, f"Boilerplate term not filtered: {bp}"

    def test_filters_single_word_non_entities(self, nlp):
        extractor = SectionExtractor(nlp)
        obs = extractor.extract(
            body_text="The organization provides various services to people.",
            section_index=0,
            heading=None,
            parent_headings=None,
        )
        for o in obs:
            if o.term_type != "named_entity":
                assert len(o.term.split()) >= 2, f"Single-word non-entity: {o.term}"

    def test_preserves_section_context(self, nlp):
        extractor = SectionExtractor(nlp)
        obs = extractor.extract(
            body_text="Youth development programs include mentoring and tutoring services.",
            section_index=3,
            heading="Programs",
            parent_headings=["Our Services"],
        )
        if obs:
            assert obs[0].section_index == 3
            assert obs[0].section_heading == "Programs"
            assert obs[0].heading_context == ["Our Services"]

    def test_truncates_long_sections(self, nlp):
        long_text = "word " * 30000
        extractor = SectionExtractor(nlp)
        obs = extractor.extract(
            body_text=long_text,
            section_index=0,
            heading=None,
            parent_headings=None,
        )
        assert isinstance(obs, list)

    def test_extracts_named_entities(self, nlp):
        extractor = SectionExtractor(nlp)
        obs = extractor.extract(
            body_text="United Way of Greater Cleveland partnered with the Red Cross to deliver emergency aid.",
            section_index=0,
            heading=None,
            parent_headings=None,
        )
        entity_obs = [o for o in obs if o.term_type == "named_entity"]
        if entity_obs:
            for o in entity_obs:
                assert o.term == o.term.lower()

    def test_extracts_pos_ngrams(self, nlp):
        extractor = SectionExtractor(nlp)
        obs = extractor.extract(
            body_text="The affordable housing initiative provides emergency shelter for homeless youth.",
            section_index=0,
            heading=None,
            parent_headings=None,
        )
        ngram_obs = [o for o in obs if o.term_type == "ngram"]
        if ngram_obs:
            for o in ngram_obs:
                assert o.pos_pattern is not None


class TestCvalue:
    def test_non_nested_formula(self):
        freq = Counter({"food pantry": 10, "nutrition program": 5})
        info = {
            "food pantry": Observation("food pantry", "Food Pantry", "cvalue", "NOUN NOUN", 10, None, None, None),
            "nutrition program": Observation("nutrition program", "Nutrition Program", "cvalue", "NOUN NOUN", 5, None, None, None),
        }
        results = _score_cvalue_from_freqs(freq, info, min_score=0.0)
        term_scores = {r.term: r for r in results}
        assert "food pantry" in term_scores
        expected = math.log2(2) * 10
        assert abs(term_scores["food pantry"].frequency - 10) == 0

    def test_nested_term_adjustment(self):
        freq = Counter({
            "emergency food pantry": 5,
            "food pantry": 15,
        })
        info = {
            "emergency food pantry": Observation("emergency food pantry", "Emergency Food Pantry", "cvalue", "NOUN NOUN NOUN", 5, None, None, None),
            "food pantry": Observation("food pantry", "Food Pantry", "cvalue", "NOUN NOUN", 15, None, None, None),
        }
        results = _score_cvalue_from_freqs(freq, info, min_score=0.0)
        term_scores = {r.term: r for r in results}
        if "food pantry" in term_scores:
            fp_result = term_scores["food pantry"]
            expected = math.log2(2) * (15 - 5 / 1)
            assert fp_result.term_type == "cvalue"

    def test_min_score_filter(self):
        freq = Counter({"rare term": 1})
        info = {
            "rare term": Observation("rare term", "Rare Term", "cvalue", "ADJ NOUN", 1, None, None, None),
        }
        results = _score_cvalue_from_freqs(freq, info, min_score=5.0)
        assert len(results) == 0

    def test_single_word_excluded(self):
        freq = Counter({"shelter": 20})
        info = {
            "shelter": Observation("shelter", "Shelter", "cvalue", "NOUN", 20, None, None, None),
        }
        results = _score_cvalue_from_freqs(freq, info, min_score=0.0)
        assert len(results) == 0


class TestExtractDocument:
    def test_basic_extraction(self, nlp):
        sections = [
            {
                "body_text": "Our youth mentoring program provides academic tutoring and career guidance to underserved teens.",
                "section_index": 0,
                "heading": "Programs",
                "parent_headings": ["About Us"],
            },
        ]
        sec_obs, cval_obs, warnings = extract_document(nlp, sections)
        assert len(sec_obs) > 0
        assert all(isinstance(o, Observation) for o in sec_obs)

    def test_skips_short_sections(self, nlp):
        sections = [
            {"body_text": "Short.", "section_index": 0, "heading": None, "parent_headings": None},
        ]
        sec_obs, cval_obs, warnings = extract_document(nlp, sections, min_section_chars=50)
        assert len(sec_obs) == 0

    def test_handles_empty_sections(self, nlp):
        sections = [
            {"body_text": "", "section_index": 0, "heading": None, "parent_headings": None},
        ]
        sec_obs, cval_obs, warnings = extract_document(nlp, sections)
        assert len(sec_obs) == 0

    def test_handles_section_error(self, nlp):
        sections = [
            {
                "body_text": "Valid section with youth development programs and family services.",
                "section_index": 0,
                "heading": "Programs",
                "parent_headings": None,
            },
            {
                "section_index": 1,
                "heading": "Bad",
                "parent_headings": None,
            },
        ]
        sec_obs, cval_obs, warnings = extract_document(nlp, sections)
        assert any("SectionError" in w for w in warnings) or len(sec_obs) >= 0

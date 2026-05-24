"""Tests for stopword/boilerplate lists (Spec 0049)."""
from lavandula.nlp.stopwords import BOILERPLATE_TERMS, ENTITY_LABELS_KEEP


class TestBoilerplateTerms:
    def test_contains_generic_nonprofit(self):
        for term in ["community", "mission", "impact", "stakeholders",
                      "board of directors", "fiscal year", "annual report",
                      "strategic plan"]:
            assert term in BOILERPLATE_TERMS, f"Missing: {term}"

    def test_contains_financial(self):
        for term in ["form 990", "tax-exempt", "gross receipts", "net assets"]:
            assert term in BOILERPLATE_TERMS, f"Missing: {term}"

    def test_is_frozenset(self):
        assert isinstance(BOILERPLATE_TERMS, frozenset)


class TestEntityLabels:
    def test_expected_labels(self):
        assert ENTITY_LABELS_KEEP == frozenset({"ORG", "PRODUCT", "EVENT", "WORK_OF_ART"})

    def test_excludes_person_gpe(self):
        assert "PERSON" not in ENTITY_LABELS_KEEP
        assert "GPE" not in ENTITY_LABELS_KEEP
        assert "DATE" not in ENTITY_LABELS_KEEP
        assert "CARDINAL" not in ENTITY_LABELS_KEEP

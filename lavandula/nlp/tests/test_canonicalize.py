"""Tests for term canonicalization (Spec 0049)."""
import pytest
import spacy


@pytest.fixture(scope="module")
def nlp():
    try:
        return spacy.load("en_core_web_lg", disable=["ner", "textcat"])
    except OSError:
        pytest.skip("en_core_web_lg not available")


class TestCanonicalizeTerm:
    def test_lowercase_lemmatize_in_context(self, nlp):
        """In natural sentence context, spaCy correctly lemmatizes plurals."""
        from lavandula.nlp.canonicalize import canonicalize_term
        doc = nlp("We operate food pantries in three counties.")
        # Extract the "food pantries" span
        tokens = [t for t in doc if t.text.lower() in ("food", "pantries")]
        canonical, raw = canonicalize_term(tokens)
        assert canonical == "food pantry"

    def test_isolated_phrase_lowercased(self, nlp):
        from lavandula.nlp.canonicalize import canonicalize_term
        doc = nlp("Food Pantries")
        canonical, raw = canonicalize_term(list(doc))
        assert canonical == canonical.lower()
        assert raw == "Food Pantries"

    def test_hyphen_preserved(self, nlp):
        from lavandula.nlp.canonicalize import canonicalize_term
        doc = nlp("trauma-informed care")
        canonical, raw = canonicalize_term(list(doc))
        assert "-" in canonical
        assert "care" in canonical
        assert raw == "trauma-informed care"

    def test_possessive_stripped(self, nlp):
        from lavandula.nlp.canonicalize import canonicalize_term
        doc = nlp("children's program")
        canonical, raw = canonicalize_term(list(doc))
        assert "'s" not in canonical
        assert "’s" not in canonical

    def test_entity_skips_lemmatization(self, nlp):
        from lavandula.nlp.canonicalize import canonicalize_term
        doc = nlp("United Way")
        canonical, raw = canonicalize_term(list(doc), is_entity=True)
        assert canonical == "united way"
        assert raw == "United Way"

    def test_whitespace_collapsed(self, nlp):
        from lavandula.nlp.canonicalize import canonicalize_term
        doc = nlp("food   bank   program")
        canonical, raw = canonicalize_term(list(doc))
        assert "  " not in canonical

    def test_empty_input(self, nlp):
        from lavandula.nlp.canonicalize import canonicalize_term
        canonical, raw = canonicalize_term([])
        assert canonical == ""
        assert raw == ""


class TestCanonicalizeText:
    def test_roundtrip_lowercase(self, nlp):
        """Lowercase input ensures consistent lemmatization."""
        from lavandula.nlp.canonicalize import canonicalize_text
        canonical, raw = canonicalize_text("food pantries", nlp)
        assert canonical == "food pantry"
        assert raw == "food pantries"

    def test_result_always_lowercase(self, nlp):
        from lavandula.nlp.canonicalize import canonicalize_text
        canonical, raw = canonicalize_text("Food Pantries", nlp)
        assert canonical == canonical.lower()

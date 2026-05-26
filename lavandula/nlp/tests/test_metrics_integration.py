"""Integration tests for metric extraction (Spec 0050).

Requires database access — run against real lava_parse data.
Skip with: pytest -m "not integration"
"""
from __future__ import annotations

import pytest

from lavandula.nlp.metrics import process_document


@pytest.fixture(scope="module")
def engine():
    from lavandula.common.db import make_app_engine
    return make_app_engine()


@pytest.fixture(scope="module")
def head_start_docs(engine):
    """Select 5 Head Start documents from archetype members."""
    from sqlalchemy import text
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT d.content_sha256, d.source_org_ein, am.archetype_id
            FROM lava_parse.documents d
            JOIN lava_corpus.corpus c ON d.content_sha256 = c.content_sha256
            JOIN lava_vocab.archetype_members am ON c.source_org_ein = am.source_org_ein
            JOIN lava_vocab.archetypes a ON am.archetype_id = a.id
            WHERE d.error IS NULL
              AND (a.label ILIKE '%school readiness%' OR a.label ILIKE '%head start%'
                   OR a.label ILIKE '%enrollment%')
            LIMIT 5
        """)).fetchall()
    if not rows:
        pytest.skip("No Head Start archetype documents found in database")
    return [(r[0], r[1], r[2]) for r in rows]


@pytest.fixture(scope="module")
def term_set(engine):
    """Load a representative term set for testing."""
    from sqlalchemy import text
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT term FROM lava_vocab.tfidf_scores
            ORDER BY tfidf_within DESC
            LIMIT 500
        """)).fetchall()
    if not rows:
        pytest.skip("No TF-IDF scores found in database")
    return {r[0] for r in rows}


@pytest.mark.integration
class TestMetricExtractionIntegration:
    """T4: Integration test on known documents."""

    def test_observations_produced(self, engine, head_start_docs, term_set):
        docs_with_obs = 0
        all_observations = []

        for sha, ein, archetype_id in head_start_docs:
            obs = process_document(engine, 0, sha, ein, term_set, archetype_id)
            if obs:
                docs_with_obs += 1
            all_observations.extend(obs)

        assert docs_with_obs >= 3, (
            f"Expected at least 3/5 docs to produce observations, got {docs_with_obs}"
        )

    def test_expected_terms_found(self, engine, head_start_docs, term_set):
        all_terms = set()
        for sha, ein, archetype_id in head_start_docs:
            obs = process_document(engine, 0, sha, ein, term_set, archetype_id)
            for o in obs:
                all_terms.add(o["term"])

        has_expected = any(
            "school readiness" in t or "enrollment" in t
            for t in all_terms
        )
        assert has_expected, f"Expected school readiness or enrollment term, got: {list(all_terms)[:20]}"

    def test_snippet_bounds(self, engine, head_start_docs, term_set):
        for sha, ein, archetype_id in head_start_docs:
            obs = process_document(engine, 0, sha, ein, term_set, archetype_id)
            for o in obs:
                assert len(o["snippet"]) > 0
                assert len(o["snippet"]) <= 500

    def test_valid_confidence(self, engine, head_start_docs, term_set):
        for sha, ein, archetype_id in head_start_docs:
            obs = process_document(engine, 0, sha, ein, term_set, archetype_id)
            for o in obs:
                assert o["confidence"] in ("high", "medium", "low")

    def test_snippet_headings_present(self, engine, head_start_docs, term_set):
        for sha, ein, archetype_id in head_start_docs:
            obs = process_document(engine, 0, sha, ein, term_set, archetype_id)
            for o in obs:
                assert o.get("snippet_heading") is not None or o.get("source_type") == "table"

    def test_no_blocked_headings(self, engine, head_start_docs, term_set):
        blocked = [
            "auditor", "financial statement", "balance sheet", "form 990",
            "board of directors", "staff list", "acknowledgment",
            "table of contents", "notes to financial", "independent auditor",
            "statement of activities", "statement of position",
        ]
        for sha, ein, archetype_id in head_start_docs:
            obs = process_document(engine, 0, sha, ein, term_set, archetype_id)
            for o in obs:
                heading = (o.get("snippet_heading") or "").lower()
                for b in blocked:
                    assert b not in heading, (
                        f"Observation from blocked heading: {o.get('snippet_heading')}"
                    )

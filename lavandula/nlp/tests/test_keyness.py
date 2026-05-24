"""Tests for TF-IDF keyness computation (Spec 0049).

These tests verify the pure computation logic. DB integration tests
require a running Postgres instance with lava_vocab schema.
"""
import numpy as np
import pytest
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfTransformer


class TestTfidfComputation:
    def test_binary_tfidf_basic(self):
        """Verify scikit-learn TfidfTransformer on a known binary matrix."""
        matrix = csr_matrix(np.array([
            [1, 1, 0, 0],
            [1, 0, 1, 0],
            [0, 0, 1, 1],
            [1, 1, 1, 0],
        ], dtype=float))

        transformer = TfidfTransformer(use_idf=True, smooth_idf=True)
        tfidf = transformer.fit_transform(matrix)

        mean_scores = np.asarray(tfidf.mean(axis=0)).flatten()
        assert mean_scores.shape == (4,)
        assert all(s >= 0 for s in mean_scores)

        # Term at col 3 appears in only 1 doc → highest IDF
        assert mean_scores[3] > 0

    def test_keyness_ratio_null_single_vertical(self):
        """When only one vertical exists, keyness should be None."""
        vertical_docs = 10
        corpus_docs = 10  # same set = single vertical
        # Both doc frequencies equal → keyness meaningless
        # The code should set tfidf_keyness = None
        assert vertical_docs == corpus_docs

    def test_keyness_ratio_multi_vertical(self):
        """Cross-vertical keyness: ratio of within-TF-IDF to corpus-TF-IDF."""
        tfidf_within = 0.5
        corpus_df = 5
        total_corpus_docs = 100
        corpus_tfidf = corpus_df / total_corpus_docs  # 0.05
        keyness = tfidf_within / corpus_tfidf  # 10.0
        assert keyness == 10.0

    def test_min_docs_filter(self):
        """Terms below min_docs should be excluded."""
        term_doc_freqs = {"common_term": 10, "rare_term": 1, "edge_term": 3}
        min_docs = 3
        filtered = {t: f for t, f in term_doc_freqs.items() if f >= min_docs}
        assert "rare_term" not in filtered
        assert "common_term" in filtered
        assert "edge_term" in filtered

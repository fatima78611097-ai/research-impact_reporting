"""Tests for archetype discovery (Spec 0049)."""
import numpy as np
import pytest

from lavandula.nlp.archetypes import (
    auto_label,
    compute_lift_per_term,
    find_clusters,
)


class TestFindClusters:
    def test_deterministic_with_seed(self):
        np.random.seed(42)
        matrix = np.random.binomial(1, 0.3, size=(30, 10)).astype(float)
        labels1, k1, _ = find_clusters(matrix, seed=42)
        labels2, k2, _ = find_clusters(matrix, seed=42)
        np.testing.assert_array_equal(labels1, labels2)
        assert k1 == k2

    def test_manual_k_override(self):
        matrix = np.random.binomial(1, 0.3, size=(30, 10)).astype(float)
        labels, k, diag = find_clusters(matrix, k=3, seed=42)
        assert k == 3
        assert set(labels) <= {0, 1, 2}
        assert diag["method"] == "manual_k"

    def test_too_few_orgs(self):
        matrix = np.array([[1, 0, 1], [0, 1, 0]], dtype=float)
        labels, k, diag = find_clusters(matrix, seed=42)
        assert k == 1
        assert diag["reason"] == "too_few_orgs"

    def test_auto_k_uses_silhouette(self):
        np.random.seed(42)
        block1 = np.zeros((15, 10))
        block1[:, :5] = 1
        block2 = np.zeros((15, 10))
        block2[:, 5:] = 1
        noise = np.random.binomial(1, 0.1, size=(30, 10)).astype(float)
        matrix = np.vstack([block1, block2]) + noise
        matrix = np.clip(matrix, 0, 1)

        labels, k, diag = find_clusters(matrix, seed=42)
        assert k >= 2
        assert "silhouette" in str(diag.get("method", ""))

    def test_flat_silhouette_defaults_k5(self):
        np.random.seed(42)
        matrix = np.random.binomial(1, 0.5, size=(30, 10)).astype(float)
        labels, k, diag = find_clusters(matrix, seed=42)
        if diag.get("score_range", 1.0) < 0.05:
            assert k == 5


class TestComputeLift:
    def test_lift_computation(self):
        matrix = np.array([
            [1, 1, 0],
            [1, 1, 0],
            [1, 0, 0],
            [0, 0, 1],
            [0, 0, 1],
        ], dtype=float)
        labels = np.array([0, 0, 0, 1, 1])
        terms = ["term_a", "term_b", "term_c"]

        lifts = compute_lift_per_term(matrix, labels, 0, terms)
        lift_dict = dict(lifts)

        assert "term_a" in lift_dict
        p_cluster = 3 / 3
        p_total = 3 / 5
        expected_lift = p_cluster / p_total
        assert abs(lift_dict["term_a"] - expected_lift) < 0.01

    def test_empty_cluster(self):
        matrix = np.array([[1, 0], [0, 1]], dtype=float)
        labels = np.array([0, 0])
        lifts = compute_lift_per_term(matrix, labels, 1, ["a", "b"])
        assert lifts == []


class TestAutoLabel:
    def test_top_two_by_lift(self):
        lifts = [("food_pantry", 6.0), ("nutrition", 5.5), ("housing", 3.0)]
        assert auto_label(lifts) == "food_pantry + nutrition"

    def test_alphabetical_tiebreak(self):
        lifts = [("beta", 5.0), ("alpha", 5.0), ("gamma", 3.0)]
        label = auto_label(lifts)
        assert label == "alpha + beta"

    def test_single_term(self):
        lifts = [("only_term", 3.0)]
        assert auto_label(lifts) == "only_term"

    def test_empty(self):
        assert auto_label([]) == "unknown"

    def test_sorted_output_order(self):
        lifts = [("c_term", 3.0), ("a_term", 5.0), ("b_term", 5.0)]
        label = auto_label(lifts)
        assert label == "a_term + b_term"

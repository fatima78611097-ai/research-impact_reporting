"""FP-Growth archetype discovery with hierarchical clustering (Spec 0049 Step 3).

Discovers organizational sub-archetypes within NTEE verticals by:
1. Building binary org-term matrices from observations
2. Hierarchical clustering (cosine distance, complete linkage)
3. Per-cluster FP-Growth for association rules
4. Per-term lift scoring for archetype-specific vocabulary
"""
from __future__ import annotations

import logging
from collections import defaultdict

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import pdist
from sklearn.metrics import silhouette_score

from sqlalchemy import text as sql_text

log = logging.getLogger(__name__)


def build_org_term_matrix(
    engine,
    run_id: int,
    ntee_prefix: str,
    min_org_count: int = 3,
    min_keyness: float | None = None,
) -> tuple[np.ndarray, list[str], list[str]]:
    """Build binary org-term matrix.

    Returns (matrix, org_eins, term_labels).
    """
    with engine.connect() as conn:
        obs_rows = conn.execute(
            sql_text("""
                SELECT o.source_org_ein, o.term
                FROM lava_vocab.observations o
                JOIN lava_corpus.nonprofits_seed ns ON o.source_org_ein = ns.ein
                WHERE o.run_id = :run_id
                  AND ns.ntee_code LIKE :ntee
            """),
            {"run_id": run_id, "ntee": ntee_prefix},
        ).fetchall()

        keyness_filter_terms: set[str] | None = None
        if min_keyness is not None:
            keyness_rows = conn.execute(
                sql_text("""
                    SELECT term, tfidf_keyness
                    FROM lava_vocab.tfidf_scores
                    WHERE run_id = :run_id
                      AND ntee_prefix = :ntee_clean
                      AND tfidf_keyness IS NOT NULL
                """),
                {"run_id": run_id, "ntee_clean": ntee_prefix.rstrip("%")},
            ).fetchall()
            if keyness_rows:
                keyness_filter_terms = {r[0] for r in keyness_rows if r[1] >= min_keyness}
            else:
                log.info("No keyness scores available; skipping min_keyness filter")

    org_terms: defaultdict[str, set[str]] = defaultdict(set)
    for ein, term in obs_rows:
        if keyness_filter_terms is not None and term not in keyness_filter_terms:
            continue
        org_terms[ein].add(term)

    term_org_count: defaultdict[str, int] = defaultdict(int)
    for ein, terms in org_terms.items():
        for t in terms:
            term_org_count[t] += 1

    filtered_terms = sorted(t for t, c in term_org_count.items() if c >= min_org_count)
    if not filtered_terms:
        return np.empty((0, 0)), [], []

    term_to_idx = {t: i for i, t in enumerate(filtered_terms)}
    org_eins = sorted(org_terms.keys())
    org_to_idx = {e: i for i, e in enumerate(org_eins)}

    matrix = np.zeros((len(org_eins), len(filtered_terms)), dtype=np.float64)
    for ein, terms in org_terms.items():
        oi = org_to_idx[ein]
        for t in terms:
            if t in term_to_idx:
                matrix[oi, term_to_idx[t]] = 1.0

    return matrix, org_eins, filtered_terms


def find_clusters(
    matrix: np.ndarray,
    k: int | None = None,
    seed: int = 42,
) -> tuple[np.ndarray, int, dict]:
    """Hierarchical clustering with auto-k via silhouette.

    Returns (labels, k_chosen, diagnostics_dict).
    """
    n_orgs = matrix.shape[0]
    if n_orgs < 3:
        return np.zeros(n_orgs, dtype=int), 1, {"reason": "too_few_orgs"}

    np.random.seed(seed)

    dist = pdist(matrix, metric="cosine")
    dist = np.nan_to_num(dist, nan=1.0)
    Z = linkage(dist, method="complete")

    if k is not None:
        labels = fcluster(Z, t=k, criterion="maxclust") - 1
        return labels, k, {"method": "manual_k"}

    max_k = min(20, n_orgs // 3)
    if max_k < 2:
        labels = np.zeros(n_orgs, dtype=int)
        return labels, 1, {"reason": "max_k_too_small"}

    scores = {}
    for candidate_k in range(2, max_k + 1):
        candidate_labels = fcluster(Z, t=candidate_k, criterion="maxclust") - 1
        if len(set(candidate_labels)) < 2:
            continue
        try:
            s = silhouette_score(matrix, candidate_labels, metric="cosine")
            scores[candidate_k] = s
        except ValueError:
            continue

    if not scores:
        labels = fcluster(Z, t=min(5, max_k), criterion="maxclust") - 1
        return labels, min(5, max_k), {"reason": "no_valid_silhouette", "method": "default_k5"}

    best_k = max(scores, key=scores.get)
    score_range = max(scores.values()) - min(scores.values())

    diagnostics = {
        "method": "silhouette",
        "best_silhouette": scores[best_k],
        "score_range": score_range,
        "scores": {str(kk): round(s, 4) for kk, s in sorted(scores.items())},
    }

    if score_range < 0.05:
        best_k = min(5, max_k)
        diagnostics["warning"] = "flat_silhouette_defaulting_to_k5"
        log.warning("Silhouette scores flat (range %.3f); defaulting to k=%d", score_range, best_k)

    labels = fcluster(Z, t=best_k, criterion="maxclust") - 1
    return labels, best_k, diagnostics


def run_fp_growth(
    matrix: np.ndarray,
    term_labels: list[str],
    cluster_labels: np.ndarray,
    cluster_id: int,
    min_support: float = 0.05,
    min_lift: float = 1.5,
) -> list[dict]:
    """FP-Growth on a single cluster's subset."""
    from mlxtend.frequent_patterns import fpgrowth, association_rules
    import pandas as pd

    mask = cluster_labels == cluster_id
    subset = matrix[mask]

    if subset.shape[0] < 2:
        return []

    df = pd.DataFrame(subset.astype(bool), columns=term_labels)

    active_cols = df.columns[df.any()]
    if len(active_cols) < 2:
        return []
    df = df[active_cols]

    try:
        freq_items = fpgrowth(df, min_support=min_support, use_colnames=True)
    except Exception as e:
        log.warning("FP-Growth failed for cluster %d: %s", cluster_id, e)
        return []

    if freq_items.empty:
        return []

    try:
        rules = association_rules(freq_items, metric="lift", min_threshold=min_lift)
    except Exception as e:
        log.warning("Association rules failed for cluster %d: %s", cluster_id, e)
        return []

    result = []
    for _, row in rules.iterrows():
        conviction = row.get("conviction", None)
        if conviction is not None and np.isinf(conviction):
            conviction = 999.0
        result.append({
            "antecedent": sorted(row["antecedents"]),
            "consequent": sorted(row["consequents"]),
            "support": float(row["support"]),
            "confidence": float(row["confidence"]),
            "lift": float(row["lift"]),
            "conviction": float(conviction) if conviction is not None else None,
        })

    return result


def compute_lift_per_term(
    matrix: np.ndarray,
    cluster_labels: np.ndarray,
    cluster_id: int,
    term_labels: list[str],
) -> list[tuple[str, float]]:
    """Per-term lift vs full population for an archetype."""
    mask = cluster_labels == cluster_id
    cluster_matrix = matrix[mask]
    n_cluster = cluster_matrix.shape[0]
    n_total = matrix.shape[0]

    if n_cluster == 0 or n_total == 0:
        return []

    cluster_freq = cluster_matrix.sum(axis=0)
    total_freq = matrix.sum(axis=0)

    results = []
    for i, term in enumerate(term_labels):
        p_cluster = cluster_freq[i] / n_cluster if n_cluster > 0 else 0
        p_total = total_freq[i] / n_total if n_total > 0 else 0
        lift = p_cluster / p_total if p_total > 0 else 0.0
        if lift > 0:
            results.append((term, float(lift)))

    results.sort(key=lambda x: (-x[1], x[0]))
    return results


def auto_label(term_lifts: list[tuple[str, float]]) -> str:
    """Top 2 terms by lift, joined with ' + '. Alphabetical tiebreak."""
    if not term_lifts:
        return "unknown"

    sorted_lifts = sorted(term_lifts, key=lambda x: (-x[1], x[0]))
    top = sorted_lifts[:2]
    return " + ".join(t[0] for t in top)

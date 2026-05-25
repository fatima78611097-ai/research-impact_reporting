"""TF-IDF keyness scoring (Spec 0049 Step 2).

Computes within-vertical TF-IDF and cross-vertical keyness ratios.
Also aggregates C-value scores and computes NC-value.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict

import numpy as np
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfTransformer

from sqlalchemy import text as sql_text

log = logging.getLogger(__name__)


def compute_tfidf_scores(
    engine,
    run_id: int,
    ntee_prefix: str,
    min_docs: int = 3,
) -> list[dict]:
    """Compute TF-IDF within-vertical and cross-vertical keyness."""
    with engine.connect() as conn:
        vertical_docs = conn.execute(
            sql_text("""
                SELECT DISTINCT de.content_sha256
                FROM lava_vocab.doc_extractions de
                JOIN lava_corpus.nonprofits_seed ns ON de.source_org_ein = ns.ein
                WHERE de.run_id = :run_id
                  AND de.error IS NULL
                  AND ns.ntee_code LIKE :ntee
            """),
            {"run_id": run_id, "ntee": ntee_prefix},
        ).fetchall()
        vertical_sha_set = {r[0] for r in vertical_docs}

        if not vertical_sha_set:
            log.warning("No documents found for NTEE prefix %s in run %d", ntee_prefix, run_id)
            return []

        obs_rows = conn.execute(
            sql_text("""
                SELECT content_sha256, term
                FROM lava_vocab.observations
                WHERE run_id = :run_id
                  AND content_sha256 = ANY(:shas)
            """),
            {"run_id": run_id, "shas": list(vertical_sha_set)},
        ).fetchall()

        corpus_freq_rows = conn.execute(
            sql_text("""
                SELECT term, COUNT(DISTINCT content_sha256) AS doc_freq
                FROM lava_vocab.observations
                WHERE run_id = :run_id
                GROUP BY term
            """),
            {"run_id": run_id},
        ).fetchall()
        corpus_doc_freq = {r[0]: r[1] for r in corpus_freq_rows}

        distinct_ntee_count = conn.execute(
            sql_text("""
                SELECT COUNT(DISTINCT LEFT(ns.ntee_code, 2))
                FROM lava_vocab.doc_extractions de
                JOIN lava_corpus.nonprofits_seed ns ON de.source_org_ein = ns.ein
                WHERE de.run_id = :run_id AND de.error IS NULL
            """),
            {"run_id": run_id},
        ).scalar()

    doc_term_pairs: defaultdict[str, set[str]] = defaultdict(set)
    for sha, term in obs_rows:
        doc_term_pairs[term].add(sha)

    term_doc_freq = {term: len(docs) for term, docs in doc_term_pairs.items()}
    terms = [t for t, df in term_doc_freq.items() if df >= min_docs]

    if not terms:
        log.warning("No terms meet min_docs=%d threshold", min_docs)
        return []

    term_to_idx = {t: i for i, t in enumerate(terms)}
    doc_list = sorted(vertical_sha_set)
    doc_to_idx = {d: i for i, d in enumerate(doc_list)}

    n_docs = len(doc_list)
    n_terms = len(terms)
    rows, cols, data = [], [], []

    for sha, term in obs_rows:
        if term in term_to_idx and sha in doc_to_idx:
            rows.append(doc_to_idx[sha])
            cols.append(term_to_idx[term])
            data.append(1)

    binary_matrix = csr_matrix(
        (data, (rows, cols)), shape=(n_docs, n_terms), dtype=np.float64,
    )
    # Deduplicate: clip to binary
    binary_matrix.data[:] = 1.0

    transformer = TfidfTransformer(use_idf=True, smooth_idf=True)
    tfidf_matrix = transformer.fit_transform(binary_matrix)

    mean_tfidf = np.asarray(tfidf_matrix.mean(axis=0)).flatten()

    total_corpus_docs = sum(1 for _ in vertical_sha_set)
    with engine.connect() as conn:
        total_corpus_docs = conn.execute(
            sql_text("""
                SELECT COUNT(*) FROM lava_vocab.doc_extractions
                WHERE run_id = :run_id AND error IS NULL
            """),
            {"run_id": run_id},
        ).scalar() or 0

    single_vertical = (distinct_ntee_count or 1) <= 1

    results = []
    for i, term in enumerate(terms):
        tfidf_within = float(mean_tfidf[i])
        doc_freq = term_doc_freq[term]
        corpus_df = corpus_doc_freq.get(term, doc_freq)

        if single_vertical:
            keyness = None
        else:
            corpus_tfidf = (corpus_df / total_corpus_docs) if total_corpus_docs > 0 else 0
            keyness = (tfidf_within / corpus_tfidf) if corpus_tfidf > 0 else None

        results.append({
            "run_id": run_id,
            "term": term,
            "ntee_prefix": ntee_prefix.rstrip("%"),
            "doc_frequency": doc_freq,
            "corpus_doc_frequency": corpus_df,
            "tfidf_within": tfidf_within,
            "tfidf_keyness": keyness,
        })

    return results


def aggregate_cvalue_scores(
    engine,
    run_id: int,
    ntee_prefix: str,
) -> list[dict]:
    """Aggregate C-value scores across docs in the vertical. Compute NC-value."""
    with engine.connect() as conn:
        vertical_shas = conn.execute(
            sql_text("""
                SELECT DISTINCT de.content_sha256
                FROM lava_vocab.doc_extractions de
                JOIN lava_corpus.nonprofits_seed ns ON de.source_org_ein = ns.ein
                WHERE de.run_id = :run_id
                  AND de.error IS NULL
                  AND ns.ntee_code LIKE :ntee
            """),
            {"run_id": run_id, "ntee": ntee_prefix},
        ).fetchall()
        sha_set = {r[0] for r in vertical_shas}

        if not sha_set:
            return []

        cvalue_rows = conn.execute(
            sql_text("""
                SELECT term, term_raw, frequency, content_sha256, section_index
                FROM lava_vocab.observations
                WHERE run_id = :run_id
                  AND term_type = 'cvalue'
                  AND content_sha256 = ANY(:shas)
            """),
            {"run_id": run_id, "shas": list(sha_set)},
        ).fetchall()

        all_obs_rows = conn.execute(
            sql_text("""
                SELECT term, content_sha256
                FROM lava_vocab.observations
                WHERE run_id = :run_id
                  AND content_sha256 = ANY(:shas)
            """),
            {"run_id": run_id, "shas": list(sha_set)},
        ).fetchall()

    term_total_freq: Counter[str] = Counter()
    term_doc_set: defaultdict[str, set[str]] = defaultdict(set)
    term_word_count: dict[str, int] = {}

    for term, _raw, freq, sha, _sec in cvalue_rows:
        term_total_freq[term] += freq
        term_doc_set[term].add(sha)
        if term not in term_word_count:
            term_word_count[term] = len(term.split())

    if not term_total_freq:
        return []

    context_words: defaultdict[str, Counter[str]] = defaultdict(Counter)
    all_terms_by_doc: defaultdict[str, set[str]] = defaultdict(set)
    for term, sha in all_obs_rows:
        all_terms_by_doc[sha].add(term)

    for sha, terms_in_doc in all_terms_by_doc.items():
        for cterm in term_total_freq:
            if cterm in terms_in_doc:
                for other in terms_in_doc:
                    if other != cterm:
                        context_words[cterm][other] += 1

    ntee_clean = ntee_prefix.rstrip("%")
    results = []
    for term, total_freq in term_total_freq.items():
        wc = term_word_count.get(term, len(term.split()))
        import math
        cvalue = math.log2(wc) * total_freq if wc >= 2 else float(total_freq)
        doc_freq = len(term_doc_set[term])

        ctx = context_words.get(term, Counter())
        if ctx:
            ctx_weight = sum(
                count / sum(ctx.values()) * count
                for _w, count in ctx.most_common(10)
            )
            nc_value = 0.8 * cvalue + 0.2 * ctx_weight
        else:
            nc_value = cvalue

        results.append({
            "run_id": run_id,
            "term": term,
            "ntee_prefix": ntee_clean,
            "word_count": wc,
            "cvalue": cvalue,
            "nc_value": nc_value,
            "doc_frequency": doc_freq,
        })

    return results

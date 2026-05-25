"""spaCy-based term extraction and C-value scoring (Spec 0049 Step 1).

Extracts domain vocabulary from document sections using:
- Noun phrase chunks (multi-word only)
- Named entities (ORG, PRODUCT, EVENT, WORK_OF_ART)
- POS-pattern n-grams: (ADJ NOUN), (NOUN NOUN), (ADJ NOUN NOUN)
- C-value multi-word term scoring
"""
from __future__ import annotations

import logging
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .canonicalize import canonicalize_term
from .stopwords import BOILERPLATE_TERMS, ENTITY_LABELS_KEEP

log = logging.getLogger(__name__)

_MAX_SECTION_CHARS = 100_000
_MAX_DOC_CHARS = 500_000

_POS_PATTERNS = [
    ("ADJ", "NOUN"),
    ("NOUN", "NOUN"),
    ("ADJ", "NOUN", "NOUN"),
]


@dataclass
class Observation:
    term: str
    term_raw: str
    term_type: str  # 'noun_phrase' | 'named_entity' | 'ngram' | 'cvalue'
    pos_pattern: str | None
    frequency: int
    section_index: int | None
    section_heading: str | None
    heading_context: list[str] | None


class SectionExtractor:
    """Extracts terms from a single section's body_text."""

    def __init__(self, nlp, boilerplate: frozenset[str] | None = None):
        self._nlp = nlp
        self._boilerplate = boilerplate or BOILERPLATE_TERMS

    def extract(
        self,
        body_text: str,
        section_index: int,
        heading: str | None,
        parent_headings: list[str] | None,
    ) -> list[Observation]:
        if len(body_text) > _MAX_SECTION_CHARS:
            log.warning(
                "Section %d truncated from %d to %d chars",
                section_index, len(body_text), _MAX_SECTION_CHARS,
            )
            body_text = body_text[:_MAX_SECTION_CHARS]

        doc = self._nlp(body_text)

        raw_terms: list[tuple[str, str, str, str | None]] = []

        for chunk in doc.noun_chunks:
            tokens = [t for t in chunk if not t.is_stop and not t.is_punct]
            if len(tokens) < 2:
                continue
            canonical, raw = canonicalize_term(tokens)
            if canonical:
                pos = " ".join(t.pos_ for t in tokens)
                raw_terms.append((canonical, raw, "noun_phrase", pos))

        for ent in doc.ents:
            if ent.label_ not in ENTITY_LABELS_KEEP:
                continue
            tokens = list(ent)
            canonical, raw = canonicalize_term(tokens, is_entity=True)
            if canonical:
                raw_terms.append((canonical, raw, "named_entity", None))

        raw_terms.extend(self._extract_pos_ngrams(doc))

        merged: dict[str, tuple[str, str, str | None, int]] = {}
        for canonical, raw, term_type, pos in raw_terms:
            if canonical in self._boilerplate:
                continue
            if len(canonical.split()) < 2 and term_type != "named_entity":
                continue
            if canonical in merged:
                existing = merged[canonical]
                merged[canonical] = (existing[0], existing[1], existing[2], existing[3] + 1)
            else:
                merged[canonical] = (raw, term_type, pos, 1)

        return [
            Observation(
                term=term,
                term_raw=info[0],
                term_type=info[1],
                pos_pattern=info[2],
                frequency=info[3],
                section_index=section_index,
                section_heading=heading,
                heading_context=parent_headings,
            )
            for term, info in merged.items()
        ]

    def _extract_pos_ngrams(self, doc) -> list[tuple[str, str, str, str]]:
        results = []
        tokens = [t for t in doc if not t.is_space]
        for pattern in _POS_PATTERNS:
            n = len(pattern)
            for i in range(len(tokens) - n + 1):
                window = tokens[i:i + n]
                if tuple(t.pos_ for t in window) == pattern:
                    if any(t.is_stop or t.is_punct for t in window):
                        continue
                    canonical, raw = canonicalize_term(window)
                    if canonical and len(canonical.split()) >= 2:
                        pos_str = " ".join(pattern)
                        results.append((canonical, raw, "ngram", pos_str))
        return results


def extract_document(
    nlp,
    sections: list[dict],
    min_section_chars: int = 50,
    boilerplate: frozenset[str] | None = None,
) -> tuple[list[Observation], list[Observation], list[str]]:
    """Extract terms from all sections of a document.

    Returns (section_observations, cvalue_observations, warnings).
    """
    extractor = SectionExtractor(nlp, boilerplate)
    all_obs: list[Observation] = []
    all_text_parts: list[str] = []
    warnings: list[str] = []
    total_chars = 0

    for sec in sections:
        body = sec.get("body_text", "")
        if len(body) < min_section_chars:
            continue

        total_chars += len(body)
        try:
            obs = extractor.extract(
                body_text=body,
                section_index=sec.get("section_index", 0),
                heading=sec.get("heading"),
                parent_headings=sec.get("parent_headings"),
            )
            all_obs.extend(obs)
        except Exception as e:
            sec_idx = sec.get("section_index", "?")
            warnings.append(f"SectionError: section {sec_idx} {type(e).__name__}")
            continue

    if total_chars > _MAX_DOC_CHARS:
        warnings.append(
            f"Large document ({total_chars} chars) — C-value computed section-by-section"
        )
        cvalue_obs = _cvalue_per_section(all_obs)
    else:
        concat = " ".join(
            s.get("body_text", "")
            for s in sections
            if len(s.get("body_text", "")) >= min_section_chars
        )
        if concat:
            cvalue_obs = compute_cvalue(all_obs, nlp, concat)
        else:
            cvalue_obs = []

    return all_obs, cvalue_obs, warnings


def _cvalue_per_section(all_obs: list[Observation]) -> list[Observation]:
    """Fallback C-value for very large documents: aggregate from section-level obs."""
    term_freq: Counter[str] = Counter()
    term_info: dict[str, Observation] = {}
    for obs in all_obs:
        term_freq[obs.term] += obs.frequency
        if obs.term not in term_info:
            term_info[obs.term] = obs

    return _score_cvalue_from_freqs(term_freq, term_info)


def compute_cvalue(
    section_obs: list[Observation],
    nlp,
    concat_text: str,
    min_score: float = 1.0,
) -> list[Observation]:
    """C-value for multi-word candidates (2-5 words).

    Operates on the concatenated text of all sections.
    """
    doc = nlp(concat_text[:_MAX_DOC_CHARS])

    candidates: Counter[str] = Counter()
    candidate_info: dict[str, tuple[str, str | None, int]] = {}

    for chunk in doc.noun_chunks:
        tokens = [t for t in chunk if not t.is_stop and not t.is_punct]
        word_count = len(tokens)
        if word_count < 2 or word_count > 5:
            continue
        canonical, raw = canonicalize_term(tokens)
        if not canonical or canonical in BOILERPLATE_TERMS:
            continue
        candidates[canonical] += 1
        if canonical not in candidate_info:
            pos = " ".join(t.pos_ for t in tokens)
            candidate_info[canonical] = (raw, pos, word_count)

    for pattern in _POS_PATTERNS:
        n = len(pattern)
        all_tokens = [t for t in doc if not t.is_space]
        for i in range(len(all_tokens) - n + 1):
            window = all_tokens[i:i + n]
            if tuple(t.pos_ for t in window) != pattern:
                continue
            if any(t.is_stop or t.is_punct for t in window):
                continue
            canonical, raw = canonicalize_term(window)
            if not canonical or canonical in BOILERPLATE_TERMS:
                continue
            word_count = len(canonical.split())
            if word_count < 2 or word_count > 5:
                continue
            candidates[canonical] += 1
            if canonical not in candidate_info:
                pos_str = " ".join(pattern)
                candidate_info[canonical] = (raw, pos_str, word_count)

    return _score_cvalue_from_freqs(
        candidates,
        {
            term: Observation(
                term=term,
                term_raw=info[0],
                term_type="cvalue",
                pos_pattern=info[1],
                frequency=freq,
                section_index=None,
                section_heading=None,
                heading_context=None,
            )
            for term, (info, freq) in (
                (t, (candidate_info[t], candidates[t]))
                for t in candidates
                if t in candidate_info
            )
        },
        min_score=min_score,
    )


def _score_cvalue_from_freqs(
    term_freq: Counter[str],
    term_info: dict[str, Observation],
    min_score: float = 1.0,
) -> list[Observation]:
    """Core C-value algorithm.

    For each term a:
    - If not nested in any longer term: cvalue = log2(|a|) * f(a)
    - If nested: cvalue = log2(|a|) * (f(a) - (1/P(T_a)) * sum_freq(containing terms))
    where P(T_a) = number of longer terms containing a.
    """
    sorted_terms = sorted(term_freq.keys(), key=lambda t: -len(t.split()))

    nested_in: defaultdict[str, list[str]] = defaultdict(list)
    for i, longer in enumerate(sorted_terms):
        for shorter in sorted_terms[i + 1:]:
            if shorter in longer:
                nested_in[shorter].append(longer)

    results: list[Observation] = []
    for term in sorted_terms:
        word_count = len(term.split())
        if word_count < 2:
            continue

        freq = term_freq[term]
        containers = nested_in.get(term, [])

        if not containers:
            cvalue = math.log2(word_count) * freq
        else:
            container_freq_sum = sum(term_freq[c] for c in containers)
            p_ta = len(containers)
            cvalue = math.log2(word_count) * (freq - container_freq_sum / p_ta)

        if cvalue < min_score:
            continue

        if term not in term_info:
            continue

        template = term_info[term]
        results.append(Observation(
            term=term,
            term_raw=template.term_raw,
            term_type="cvalue",
            pos_pattern=template.pos_pattern,
            frequency=freq,
            section_index=None,
            section_heading=None,
            heading_context=None,
        ))

    return results

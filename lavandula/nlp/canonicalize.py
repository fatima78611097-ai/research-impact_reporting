"""Term canonicalization per Spec 0049 policy.

Rules:
1. Lowercase all tokens
2. Lemmatize each token via spaCy (skip for named entities)
3. Join with single space
4. Strip leading/trailing whitespace
5. Collapse internal whitespace
6. Preserve hyphens ("trauma-informed" stays)
7. Strip possessives ("children's" → "child")
8. Plurals lemmatized ("food pantries" → "food pantry")
"""
from __future__ import annotations

import re

_POSSESSIVE_RE = re.compile(r"'s$|'s$")
_MULTI_SPACE_RE = re.compile(r"\s+")


def canonicalize_term(
    tokens: list,
    is_entity: bool = False,
) -> tuple[str, str]:
    """Canonicalize a list of spaCy tokens.

    Parameters
    ----------
    tokens : list of spaCy Token objects
    is_entity : if True, skip lemmatization (store as-is in lowercase)

    Returns
    -------
    (canonical, raw) where canonical is the normalized form and raw is
    the original surface text.
    """
    raw_parts = []
    for t in tokens:
        if t.text == "-" and raw_parts:
            raw_parts[-1] += "-"
        elif raw_parts and raw_parts[-1].endswith("-"):
            raw_parts[-1] += t.text
        else:
            raw_parts.append(t.text)
    raw = " ".join(raw_parts)
    raw = _MULTI_SPACE_RE.sub(" ", raw).strip()

    if is_entity:
        canonical = raw.lower()
    else:
        parts = []
        prev_hyphen = False
        for t in tokens:
            if t.text == "-":
                if parts:
                    parts[-1] += "-"
                prev_hyphen = True
                continue

            lemma = t.lemma_.lower()
            # spaCy may not lemmatize PROPN; force lowercase as canonical
            if lemma == t.text and t.pos_ == "PROPN":
                lemma = t.text.lower()
            lemma = _POSSESSIVE_RE.sub("", lemma)

            if prev_hyphen and parts:
                parts[-1] += lemma
                prev_hyphen = False
            else:
                parts.append(lemma)
        canonical = " ".join(parts)

    canonical = _MULTI_SPACE_RE.sub(" ", canonical).strip()
    return canonical, raw


def canonicalize_text(
    text: str,
    nlp,
    is_entity: bool = False,
) -> tuple[str, str]:
    """Canonicalize a raw text string by running it through spaCy."""
    doc = nlp(text)
    return canonicalize_term(list(doc), is_entity=is_entity)

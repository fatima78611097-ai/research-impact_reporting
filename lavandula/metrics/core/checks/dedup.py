"""Dedup — drop near-duplicate metrics within one document.

Cross-metric check (runs over a document's metric list, not one metric): same `value`
AND >=2 overlapping content words in the statement -> the later one is a duplicate.
Ported from nlp/slot_render.dedup. We QUARANTINE duplicates (reason "duplicate") rather
than silently drop them, so they stay visible for review.
"""
from __future__ import annotations

import re

from ..types import Metric


def _toks(s) -> set[str]:
    return set(w for w in re.sub(r"[^a-z0-9 ]", " ", str(s).lower()).split() if len(w) > 2)


def duplicate_indices(metrics: list[Metric]) -> set[int]:
    """-> the set of metric.idx values that are near-duplicates of an earlier metric."""
    seen: list[tuple[str, set]] = []
    dups: set[int] = set()
    for m in metrics:
        v = str(m.value)
        w = _toks(m.statement)
        if any(v == sv and len(w & sw) >= 2 for sv, sw in seen):
            dups.add(m.idx)
        else:
            seen.append((v, w))
    return dups

"""Deterministic grounding verifier for LLM-extracted facts.

Pure module — no DB, no network. Implements the gate's core decision
procedure: normalize, test R1 (contiguous span), test R2 (same-table-row),
assign verdict. Same input always produces the same verdict.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from lavandula.faithfulness.source_provider import TableRow

MAX_SNIPPET_CHARS = 2000
MAX_MULTI_SPAN = 8
MAX_CONTEXT_WINDOW_CHARS = 500

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_WS_RE = re.compile(r"\s+")
_QUOTE_MAP = str.maketrans({
    "‘": "'", "’": "'",
    "“": '"', "”": '"',
    "–": "-", "—": "-",
    "‒": "-", "―": "-",
})


@dataclass(frozen=True)
class Verdict:
    grounded: bool
    rule: str  # "R1" | "R2" | "none"
    offsets: list[tuple[int, int]] = field(default_factory=list)
    value_found: bool = False
    denominator_found: bool = False
    # --- diagnostics (Spec 0057 amendment) -------------------------------
    # Computed for EVERY fact (pass and fail) so a quarantine records WHY,
    # not just "no". These let the quarantine bucket self-categorize via a
    # plain GROUP BY — fragmentation vs reorder vs garble/fabrication vs
    # table-artifact vs missing-source — with no re-fetching or sampling.
    word_coverage: float = 0.0       # frac of snippet words present anywhere in source sections
    longest_run: float = 0.0         # longest CONTIGUOUS run of snippet words found, as a frac
    table_coverage: float = 0.0      # frac of snippet words present across table cells
    source_chars: int = 0            # length of normalized source text (0 = missing source)


def normalize(text: str) -> str:
    """Normalize text for grounding comparison.

    lowercase -> collapse whitespace -> Unicode NFC -> fold curly/straight
    quotes & dashes. Rejects non-printable control characters.
    """
    if _CTRL_RE.search(text):
        text = _CTRL_RE.sub(" ", text)
    text = text.lower()
    text = _WS_RE.sub(" ", text).strip()
    text = unicodedata.normalize("NFC", text)
    text = text.translate(_QUOTE_MAP)
    return text


def _find_substring(needle: str, haystack: str) -> tuple[int, int] | None:
    """Find needle in haystack, return (start, end) or None."""
    idx = haystack.find(needle)
    if idx == -1:
        return None
    return (idx, idx + len(needle))


def _check_r1(snippet_norm: str, source_norm: str) -> list[tuple[int, int]]:
    """R1: contiguous span — snippet is a substring of source."""
    result = _find_substring(snippet_norm, source_norm)
    if result is not None:
        return [result]
    return []


_R2_PUNCT_STRIP = re.compile(r"^[^\w$%]+|[^\w$%]+$")


def _tokenize_for_r2(text: str) -> list[str]:
    """Split text into tokens for R2 matching. Strip edge punctuation
    except currency/percent signs which carry meaning."""
    tokens = []
    for t in text.split():
        stripped = _R2_PUNCT_STRIP.sub("", t)
        if stripped:
            tokens.append(stripped)
    return tokens


def _check_r2(
    snippet_norm: str, tables: list[list[TableRow]]
) -> list[tuple[int, int]]:
    """R2: same-table-row — label+value tokens all in one normalized row."""
    snippet_tokens = set(_tokenize_for_r2(snippet_norm))
    if not snippet_tokens:
        return []

    for table in tables:
        for row in table:
            row_text = normalize(" ".join(row.cells))
            row_tokens = set(_tokenize_for_r2(row_text))
            if snippet_tokens.issubset(row_tokens):
                return [(0, len(row_text))]
    return []


def _diagnostics(snippet_norm: str, source_norm: str, tables: list[list[TableRow]]) -> dict:
    """Compute WHY-it-(didn't-)match signals for every fact (pass + fail).

    Pure, cheap, recorded on the Verdict so a quarantine self-categorizes:
      word_coverage  — frac of snippet words present ANYWHERE in source sections
      longest_run    — longest CONTIGUOUS run of snippet words found in source,
                       as a frac of snippet length (the fragmentation signal:
                       high coverage + low run = source has the words but Docling
                       split them; low coverage = words genuinely absent = garble/
                       fabrication; run==1.0 would have matched R1)
      table_coverage — frac of snippet words present across ALL table cell text
      source_chars   — length of normalized source (0 = missing source)
    """
    words = snippet_norm.split()
    n = len(words)
    if n == 0:
        return {"word_coverage": 0.0, "longest_run": 0.0, "table_coverage": 0.0,
                "source_chars": len(source_norm)}

    src_words = set(source_norm.split())
    word_coverage = sum(1 for w in words if w in src_words) / n

    # longest contiguous run of snippet words appearing as a phrase in source
    best = 0
    i = 0
    while i < n:
        j = i
        while j < n and (" ".join(words[i:j + 1]) in source_norm):
            j += 1
        best = max(best, j - i)
        i = i + 1 if j == i else j
    longest_run = best / n

    table_text = ""
    for table in tables:
        for row in table:
            table_text += " " + normalize(" ".join(row.cells))
    tbl_words = set(table_text.split())
    table_coverage = sum(1 for w in words if w in tbl_words) / n if tbl_words else 0.0

    return {"word_coverage": round(word_coverage, 3), "longest_run": round(longest_run, 3),
            "table_coverage": round(table_coverage, 3), "source_chars": len(source_norm)}


def value_present(
    value: float | int | str | None,
    denominator: float | int | str | None,
    snippet: str,
) -> tuple[bool, bool]:
    """Check whether value and denominator appear in the snippet.

    Returns (value_found, denominator_found). Denominator is optional;
    returns True if denominator is None.
    """
    if value is None:
        return (True, True)

    snippet_norm = normalize(str(snippet))
    val_str = _format_number_variants(value)
    v_found = any(v in snippet_norm for v in val_str)

    d_found = True
    if denominator is not None:
        denom_str = _format_number_variants(denominator)
        d_found = any(d in snippet_norm for d in denom_str)

    return (v_found, d_found)


def _format_number_variants(n) -> list[str]:
    """Generate plausible string representations of a number."""
    variants: list[str] = []
    s = str(n)
    variants.append(s.lower())

    try:
        f = float(n)
    except (ValueError, TypeError):
        return variants

    if f == int(f):
        int_val = int(f)
        variants.append(str(int_val))
        formatted = f"{int_val:,}"
        if formatted != str(int_val):
            variants.append(formatted)
    else:
        variants.append(f"{f:.2f}")
        variants.append(f"{f:.1f}")

    return list(set(v.lower() for v in variants))


def check(
    snippet: str,
    source_text: str,
    tables: list[list[TableRow]],
    value: float | int | str | None = None,
    denominator: float | int | str | None = None,
) -> Verdict:
    """Core grounding decision procedure.

    Tests R1 (contiguous span) then R2 (same-table-row). First match
    wins. Deterministic by construction: R1 before R2, first occurrence.
    """
    if not snippet or not snippet.strip():
        return Verdict(grounded=False, rule="none")

    if len(snippet) > MAX_SNIPPET_CHARS:
        return Verdict(grounded=False, rule="none")

    snippet_norm = normalize(snippet)
    if not snippet_norm:
        return Verdict(grounded=False, rule="none")

    source_norm = normalize(source_text) if source_text else ""
    diag = _diagnostics(snippet_norm, source_norm, tables)

    offsets = _check_r1(snippet_norm, source_norm)
    if offsets:
        v_found, d_found = value_present(value, denominator, snippet)
        return Verdict(
            grounded=True, rule="R1", offsets=offsets,
            value_found=v_found, denominator_found=d_found, **diag,
        )

    offsets = _check_r2(snippet_norm, tables)
    if offsets:
        v_found, d_found = value_present(value, denominator, snippet)
        return Verdict(
            grounded=True, rule="R2", offsets=offsets,
            value_found=v_found, denominator_found=d_found, **diag,
        )

    return Verdict(grounded=False, rule="none", **diag)


def check_story(
    source_snippet: str | None,
    story_summary: str | None,
    source_text: str,
    tables: list[list[TableRow]],
) -> Verdict:
    """Story grounding rule (spec §4.5).

    The story_summary may be abstractive (composed) — we do NOT apply
    R1/R2 to the summary prose. But:
    (a) source_snippet must be grounded R1/R2;
    (b) direct quotes in the summary must be verbatim-grounded;
    (c) no grounded source_snippet -> Tier C.
    """
    if not source_snippet or not source_snippet.strip():
        return Verdict(grounded=False, rule="none")

    snippet_verdict = check(source_snippet, source_text, tables)
    if not snippet_verdict.grounded:
        return snippet_verdict

    if story_summary:
        quotes = _extract_direct_quotes(story_summary)
        for quote in quotes:
            quote_verdict = check(quote, source_text, tables)
            if not quote_verdict.grounded:
                return Verdict(grounded=False, rule="none")

    return snippet_verdict


_QUOTE_RE = re.compile(
    r'["“”]([^"“”]{3,})["“”]'
    r"|"
    r"['‘’]([^'‘’]{3,})['‘’]"
)


def _extract_direct_quotes(text: str) -> list[str]:
    """Extract text within quotation marks (direct quotes)."""
    results: list[str] = []
    for m in _QUOTE_RE.finditer(text):
        quote = m.group(1) or m.group(2)
        if quote:
            results.append(quote)
    return results

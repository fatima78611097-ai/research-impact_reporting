"""Metric extraction with context snippets (Spec 0050).

Extracts (term, numeric_value, snippet) tuples from parsed document sections
using regex-based number detection and substring term matching. No spaCy model
load — sentence splitting is regex-only.
"""
from __future__ import annotations

import logging
import re
import signal
from typing import NamedTuple

from sqlalchemy import text as sql_text

log = logging.getLogger(__name__)

_SECTION_TIMEOUT_SEC = 5


class NumberMatch(NamedTuple):
    raw: str
    parsed: float | None
    unit_hint: str | None
    start: int
    end: int


class SentenceFragment(NamedTuple):
    text: str
    start: int
    end: int
    is_bullet: bool


# --- Heading classification ---

_HEADING_BLOCK = [
    "auditor", "financial statement", "balance sheet", "form 990",
    "board of directors", "staff list", "acknowledgment",
    "table of contents", "notes to financial", "independent auditor",
    "statement of activities", "statement of position",
]

_HEADING_PRIORITY = [
    "program", "impact", "outcome", "achievement", "result",
    "service", "community", "client", "participant", "success",
]


def classify_heading(heading: str | None) -> str:
    if not heading:
        return "neutral"
    lower = heading.lower()
    for term in _HEADING_BLOCK:
        if term in lower:
            return "skip"
    for term in _HEADING_PRIORITY:
        if term in lower:
            return "priority"
    return "neutral"


# --- Number detection ---

_RE_PERCENT = re.compile(r'(\d+(?:\.\d+)?)%')
_RE_CURRENCY = re.compile(r'\$\s?(\d[\d,]*(?:\.\d{1,2})?)(?:\s?([KMBkmb]))?')
_RE_COMMA_INT = re.compile(r'\b(\d{1,3}(?:,\d{3})+)\b')
_RE_PLAIN_INT = re.compile(r'\b(\d{3,})\b')

_RE_PHONE = re.compile(r'\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}')
_RE_DATE = re.compile(r'\d{1,2}/\d{1,2}/\d{2,4}')
_RE_PAGE_REF = re.compile(r'(?:page|section|fig(?:ure)?)\s+\d+', re.IGNORECASE)
_RE_ZIP = re.compile(r'[A-Z]{2}\s+\d{5}')

_SUFFIX_MULT = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def _parse_numeric(raw: str) -> tuple[float | None, str | None]:
    """Parse a raw number string into (float_value, unit_hint)."""
    if '%' in raw:
        m = re.search(r'(\d+(?:\.\d+)?)', raw)
        if m:
            return float(m.group(1)), "percent"
        return None, None

    if raw.startswith('$'):
        cleaned = raw.lstrip('$').strip()
        suffix = None
        if cleaned and cleaned[-1].lower() in _SUFFIX_MULT:
            suffix = cleaned[-1].lower()
            cleaned = cleaned[:-1].strip()
        cleaned = cleaned.replace(',', '')
        try:
            val = float(cleaned)
            if suffix:
                val *= _SUFFIX_MULT[suffix]
            return val, "currency"
        except ValueError:
            return None, None

    cleaned = raw.replace(',', '')
    try:
        return float(cleaned), "count"
    except ValueError:
        return None, None


def _is_year(raw: str, text: str, start: int, end: int) -> bool:
    """Check if a 4-digit number is a year (1900-2099) that's not a metric."""
    cleaned = raw.replace(',', '')
    if not cleaned.isdigit() or len(cleaned) != 4:
        return False
    val = int(cleaned)
    if val < 1900 or val > 2099:
        return False
    if start > 0 and text[start - 1] == '$':
        return False
    if end < len(text) and text[end] == '%':
        return False
    return True


def detect_numbers(text: str) -> list[NumberMatch]:
    """Detect metric-worthy numbers in text, excluding years/phones/dates/etc."""
    exclusion_spans: list[tuple[int, int]] = []
    for pattern in (_RE_PHONE, _RE_DATE, _RE_PAGE_REF, _RE_ZIP):
        for m in pattern.finditer(text):
            exclusion_spans.append((m.start(), m.end()))

    def _overlaps_exclusion(start: int, end: int) -> bool:
        for es, ee in exclusion_spans:
            if start < ee and end > es:
                return True
        return False

    results: list[NumberMatch] = []
    claimed: list[tuple[int, int]] = []

    def _overlaps_claimed(start: int, end: int) -> bool:
        for cs, ce in claimed:
            if start < ce and end > cs:
                return True
        return False

    patterns = [
        (_RE_PERCENT, True),
        (_RE_CURRENCY, True),
        (_RE_COMMA_INT, False),
        (_RE_PLAIN_INT, False),
    ]

    for regex, is_special in patterns:
        for m in regex.finditer(text):
            start, end = m.start(), m.end()
            if _overlaps_exclusion(start, end):
                continue
            if _overlaps_claimed(start, end):
                continue

            raw = m.group(0)

            if not is_special and _is_year(raw, text, start, end):
                continue

            parsed, unit_hint = _parse_numeric(raw)
            results.append(NumberMatch(
                raw=raw, parsed=parsed, unit_hint=unit_hint,
                start=start, end=end,
            ))
            claimed.append((start, end))

    results.sort(key=lambda n: n.start)
    return results


# --- Sentence splitting ---

_RE_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-Z])')
_RE_BULLET = re.compile(r'\n\s*[-•]\s+|\n\s*\d+[.)]\s+')


def split_sentences(text: str) -> list[SentenceFragment]:
    """Split text into sentence fragments using regex (no spaCy)."""
    if not text or not text.strip():
        return []

    fragments: list[SentenceFragment] = []

    bullet_spans = list(_RE_BULLET.finditer(text))
    if bullet_spans:
        parts: list[tuple[str, int, bool]] = []
        prev_end = 0
        for bm in bullet_spans:
            if prev_end < bm.start():
                pre = text[prev_end:bm.start()]
                if pre.strip():
                    parts.append((pre, prev_end, False))
            parts.append(("", bm.start(), True))
            prev_end = bm.end()
        if prev_end < len(text):
            remaining = text[prev_end:]
            if remaining.strip():
                parts.append((remaining, prev_end, True))

        for part_text, part_start, is_bullet in parts:
            if is_bullet and not part_text:
                continue
            if is_bullet:
                fragments.append(SentenceFragment(
                    text=part_text.strip(),
                    start=part_start,
                    end=part_start + len(part_text),
                    is_bullet=True,
                ))
            else:
                sub_frags = _split_plain_sentences(part_text, part_start)
                fragments.extend(sub_frags)
    else:
        fragments = _split_plain_sentences(text, 0)

    return [f for f in fragments if f.text.strip()]


def _split_plain_sentences(text: str, offset: int) -> list[SentenceFragment]:
    """Split plain text into sentences using period/exclamation/question boundaries."""
    parts = _RE_SENTENCE_SPLIT.split(text)
    fragments: list[SentenceFragment] = []
    pos = 0
    for part in parts:
        start = text.find(part, pos)
        if start == -1:
            start = pos
        end = start + len(part)
        fragments.append(SentenceFragment(
            text=part.strip(),
            start=offset + start,
            end=offset + end,
            is_bullet=False,
        ))
        pos = end
    return fragments


# --- Snippet construction ---

def build_snippet(sentence: str, heading: str | None, next_sentence: str | None) -> str:
    """Build a context snippet from a sentence, heading, and optional next sentence."""
    if len(sentence) >= 80:
        if len(sentence) > 500:
            return sentence[:499] + "…"
        return sentence

    parts = []
    if heading:
        parts.append(f"[{heading}] — ")
    parts.append(sentence)
    if next_sentence:
        parts.append(" " + next_sentence[:150])

    snippet = "".join(parts)
    if len(snippet) > 500:
        return snippet[:499] + "…"
    return snippet


def build_table_snippet(heading: str | None, row_label: str, cell_value: str) -> str:
    """Build a snippet for a table cell metric."""
    if heading:
        snippet = f"[{heading}] — {row_label}: {cell_value}"
    else:
        snippet = f"{row_label}: {cell_value}"
    if len(snippet) > 500:
        return snippet[:499] + "…"
    return snippet


# --- Term matching ---

def match_terms(text_lower: str, term_set: set[str]) -> list[str]:
    """Find all terms from term_set that appear as substrings in text_lower."""
    return [term for term in term_set if term in text_lower]


# --- Table extraction ---

def extract_table_metrics(
    data_json: list | None,
    heading: str | None,
    term_set: set[str],
) -> list[dict]:
    """Extract metrics from a table's data_json (list of row arrays)."""
    if not isinstance(data_json, list):
        log.debug("Table data_json is not a list, skipping")
        return []

    if len(data_json) < 2:
        log.debug("Table has fewer than 2 rows, skipping")
        return []

    for row in data_json:
        if not isinstance(row, list):
            log.debug("Table row is not a list, skipping table")
            return []

    headers = data_json[0]
    data_rows = data_json[1:]

    total_cells = 0
    numeric_cells = 0
    for row in data_rows:
        for cell in row[1:] if len(row) > 1 else row:
            total_cells += 1
            cell_str = str(cell).strip()
            if cell_str and detect_numbers(cell_str):
                numeric_cells += 1

    if total_cells > 0 and numeric_cells / total_cells > 0.5:
        log.debug("Table is >50%% numeric (financial), skipping")
        return []

    if heading and classify_heading(heading) == "skip":
        return []

    observations: list[dict] = []
    for row in data_rows:
        if not row:
            continue
        row_label = str(row[0]).strip() if row else ""
        for col_idx in range(1, len(row)):
            cell_value = str(row[col_idx]).strip()
            if not cell_value:
                continue
            numbers = detect_numbers(cell_value)
            if not numbers:
                continue

            term_source = row_label if row_label else (
                str(headers[col_idx]).strip() if col_idx < len(headers) else ""
            )
            if not term_source:
                continue

            matched = match_terms(term_source.lower(), term_set)
            if not matched:
                continue

            snippet = build_table_snippet(heading, term_source, cell_value)
            for num in numbers:
                for term in matched:
                    observations.append({
                        "term": term,
                        "numeric_value": num.raw,
                        "numeric_parsed": num.parsed,
                        "unit_hint": num.unit_hint,
                        "snippet": snippet,
                        "snippet_heading": heading,
                        "source_type": "table",
                        "confidence": "high",
                    })

    return observations


# --- Document processor ---

def process_document(
    engine,
    run_id: int,
    sha: str,
    ein: str,
    term_set: set[str],
    archetype_id: int | None,
) -> list[dict]:
    """Extract metric observations from one document's sections and tables."""
    with engine.connect() as conn:
        section_rows = conn.execute(sql_text("""
            SELECT id, section_index, heading, body_text
            FROM lava_parse.sections
            WHERE content_sha256 = :sha
            ORDER BY section_index
        """), {"sha": sha}).fetchall()

        table_rows = conn.execute(sql_text("""
            SELECT t.section_id, t.data_json
            FROM lava_parse.tables t
            JOIN lava_parse.sections s ON t.section_id = s.id
            WHERE s.content_sha256 = :sha
        """), {"sha": sha}).fetchall()

    tables_by_section: dict[int, list] = {}
    for trow in table_rows:
        section_id = trow[0]
        tables_by_section.setdefault(section_id, []).append(trow[1])

    observations: list[dict] = []

    for sec_row in section_rows:
        section_id = sec_row[0]
        section_index = sec_row[1]
        heading = sec_row[2]
        body_text = sec_row[3]

        classification = classify_heading(heading)
        if classification == "skip":
            continue

        if body_text:
            narrative_obs = _process_narrative(
                body_text, heading, section_index, term_set,
            )
            for obs in narrative_obs:
                obs["archetype_id"] = archetype_id
            observations.extend(narrative_obs)

        section_tables = tables_by_section.get(section_id, [])
        for data_json in section_tables:
            try:
                table_obs = extract_table_metrics(data_json, heading, term_set)
                for obs in table_obs:
                    obs["section_index"] = section_index
                    obs["archetype_id"] = archetype_id
                observations.extend(table_obs)
            except Exception as e:
                log.warning("Table extraction error in section %d: %s", section_index, e)

    return observations


def _process_narrative(
    body_text: str,
    heading: str | None,
    section_index: int,
    term_set: set[str],
) -> list[dict]:
    """Process narrative text from a section, extracting metric observations."""
    observations: list[dict] = []

    fragments = split_sentences(body_text)

    heading_terms: list[str] | None = None
    if heading:
        heading_terms = match_terms(heading.lower(), term_set) or None

    for i, fragment in enumerate(fragments):
        numbers = detect_numbers(fragment.text)
        if not numbers:
            continue

        matched_terms = match_terms(fragment.text.lower(), term_set)
        source_type = "bullet" if fragment.is_bullet else "narrative"

        next_sentence = fragments[i + 1].text if i + 1 < len(fragments) else None

        if matched_terms:
            confidence = "high"
            terms_to_use = matched_terms
        elif heading_terms:
            confidence = "medium"
            terms_to_use = heading_terms
        else:
            continue

        snippet = build_snippet(fragment.text, heading, next_sentence)

        for term in terms_to_use:
            for num in numbers:
                observations.append({
                    "term": term,
                    "numeric_value": num.raw,
                    "numeric_parsed": num.parsed,
                    "unit_hint": num.unit_hint,
                    "snippet": snippet,
                    "snippet_heading": heading,
                    "section_index": section_index,
                    "source_type": source_type,
                    "confidence": confidence,
                })

    return observations

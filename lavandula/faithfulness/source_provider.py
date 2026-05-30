"""Source text provider for faithfulness verification.

Abstracts the source of parsed text so the gate can ground against
current Docling/pdftotext text now and swap in 0060-repaired text later.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.engine import Engine


@dataclass(frozen=True)
class TableRow:
    """One row from a parsed table, used for R2 matching."""
    cells: list[str]


@dataclass(frozen=True)
class SourceText:
    """Normalized-ready source text for a document."""
    section_text: str
    tables: list[list[TableRow]]
    source: str  # "docling" | "pdftotext-repaired"
    tier_hint: str | None = None  # provider-set tier override for gate_runner


class SourceTextProvider(Protocol):
    def get(self, content_sha256: str) -> SourceText | None: ...


class DoclingSourceProvider:
    """V1 provider: reads from lava_parse.sections + lava_parse.tables."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, content_sha256: str) -> SourceText | None:
        with self._engine.connect() as conn:
            section_rows = conn.execute(text("""
                SELECT heading, body_text
                FROM lava_parse.sections
                WHERE content_sha256 = :sha
                ORDER BY section_index
            """), {"sha": content_sha256}).fetchall()

            table_rows = conn.execute(text("""
                SELECT data_json, markdown
                FROM lava_parse.tables
                WHERE content_sha256 = :sha
                ORDER BY table_index
            """), {"sha": content_sha256}).fetchall()

        if not section_rows and not table_rows:
            return None

        parts: list[str] = []
        for heading, body in section_rows:
            if heading:
                parts.append(heading)
            parts.append(body)
        section_text = "\n\n".join(parts)

        tables: list[list[TableRow]] = []
        for data_json, markdown in table_rows:
            rows = _parse_table(data_json, markdown)
            if rows:
                tables.append(rows)

        return SourceText(
            section_text=section_text,
            tables=tables,
            source="docling",
        )


def _parse_table(data_json, markdown: str | None) -> list[TableRow]:
    """Extract rows from data_json (preferred) or markdown fallback."""
    if data_json:
        parsed = data_json if isinstance(data_json, list) else json.loads(data_json)
        if isinstance(parsed, list):
            return [
                TableRow(cells=[str(cell) for cell in row.values()] if isinstance(row, dict) else [str(c) for c in row])
                for row in parsed
            ]

    if markdown:
        rows: list[TableRow] = []
        for line in markdown.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("|-") or line.startswith("|--") or all(c in "-| " for c in line):
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if cells:
                rows.append(TableRow(cells=cells))
        return rows

    return []


# ============================================================
# 0060: PdftextSourceProvider — pdftotext-repaired text + certification
# ============================================================

CERTIFICATION_COVERAGE_FLOOR = 0.50


class PdftextSourceProvider:
    """V2 provider: returns certified pdftotext text when available,
    falls back to Docling text otherwise. Tables always from Docling."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._docling = DoclingSourceProvider(engine)

    def get(self, content_sha256: str) -> SourceText | None:
        doc_row, pdftotext_row, tables = self._load(content_sha256)

        if pdftotext_row is None:
            return self._fallback_docling(content_sha256, "unverified_pending")

        text_source = doc_row["text_source"] if doc_row else None
        fwd = doc_row["pdftotext_coverage"] if doc_row else None
        rev = doc_row["pdftotext_reverse"] if doc_row else None

        if text_source == "scanned":
            return self._fallback_docling(content_sha256, "unverified_ocr")

        if text_source == "pdftotext_failed":
            return self._fallback_docling(content_sha256, "unverified_pending")

        if not self._is_certified(text_source, fwd, rev):
            return self._fallback_docling(content_sha256, "unverified_pending")

        return SourceText(
            section_text=pdftotext_row["full_text"],
            tables=tables,
            source="pdftotext-repaired",
        )

    def _load(self, sha: str):
        with self._engine.connect() as conn:
            doc_row = conn.execute(text("""
                SELECT text_source, pdftotext_coverage, pdftotext_reverse
                FROM lava_parse.documents
                WHERE content_sha256 = :sha
            """), {"sha": sha}).mappings().fetchone()

            pdftotext_row = conn.execute(text("""
                SELECT full_text, char_count
                FROM lava_parse.pdftotext
                WHERE content_sha256 = :sha
            """), {"sha": sha}).mappings().fetchone()

            table_rows = conn.execute(text("""
                SELECT data_json, markdown
                FROM lava_parse.tables
                WHERE content_sha256 = :sha
                ORDER BY table_index
            """), {"sha": sha}).fetchall()

        tables: list[list[TableRow]] = []
        for data_json, markdown in table_rows:
            rows = _parse_table(data_json, markdown)
            if rows:
                tables.append(rows)

        return doc_row, pdftotext_row, tables

    def _fallback_docling(self, sha: str, tier_hint: str) -> SourceText | None:
        source = self._docling.get(sha)
        if source is None:
            return None
        return SourceText(
            section_text=source.section_text,
            tables=source.tables,
            source=source.source,
            tier_hint=tier_hint,
        )

    @staticmethod
    def _is_certified(
        text_source: str | None,
        forward_coverage: float | None,
        reverse_coverage: float | None,
    ) -> bool:
        if text_source != "text_native":
            return False
        if forward_coverage is None or reverse_coverage is None:
            return False
        return (float(forward_coverage) >= CERTIFICATION_COVERAGE_FLOOR
                and float(reverse_coverage) >= CERTIFICATION_COVERAGE_FLOOR)

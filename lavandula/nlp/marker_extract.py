"""Marker-citing production extraction runner (Spec 0068, Phase 4).

Per eligible document:
    eligibility gate  ->  tagged render (Phase 1)  ->  one-pass loc1 extract
    ->  strict ref-grammar parse  ->  marker resolution (Phase 2)
    ->  atomic delete-then-insert of metric + markers + resolved coords.

Security / integrity (spec §5.8/§5.9/§8, plan Phase 4):
  - **Eligibility gate** (§5.4): a doc is extracted only if re-parsed to the
    coordinate-bearing schema with a populated-coordinate threshold met; an
    ineligible doc is SKIPPED (never emits marker-less metrics) and *counted as a
    failed extraction* with a reason — it does not enter production output.
  - **parse_version provenance binding** (§8): a doc whose parse_version is
    unknown/unverifiable is skipped (``parse_unverified``); coords are accepted
    only from a trusted parse.
  - **Strict ref grammar** (§5.8/S13): an emitted ref is validated against
    ``^⟨[tc]\\d+⟩$`` (and a hard length bound) the instant it is parsed; a ref
    that doesn't match (5 MB payload, injected prose) is dropped to null and
    counted as forged BEFORE it reaches any log / stats / resolver.
  - **Shared sanitizer** (§8/S14): all source-derived text written to logs /
    stats passes through one utility stripping ``\\r``/``\\n``/ANSI (terminal
    log-forging guard).
  - **Atomic idempotency** (§5.6/S9): a rerun does delete-then-insert per
    ``(run_id, content_sha256)`` in a single transaction (crash-safe).
  - **Surge signal** (§5.9): per-doc quarantine when most refs are null/forged
    with an absolute floor; per-run skip-rate / forged-rate alerts.

The orchestrator takes an injectable ``chat_fn`` (the LLM) and a SQLAlchemy
``engine`` so the bulk of the logic is testable without live API / RDS. The pure
helpers (grammar, sanitizer, eligibility, provenance, surge) have no I/O.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from lavandula.nlp.marker_render import (
    RENDER_VERSION,
    SkipDocument,
    render_tagged,
)
from lavandula.nlp.marker_resolve import resolve_metric

log = logging.getLogger(__name__)

# --- thresholds (plan §4/§5 working defaults; Phase-0 confirms with evidence) ---
ELIGIBILITY_THRESHOLD = 0.80     # >= T% of text elements must carry a bbox
SURGE_DOC_NULL_FRAC = 0.50       # per-doc: > this fraction null/forged value_ref ...
SURGE_DOC_MIN_INVALID = 3        # ... AND >= this many invalid refs -> quarantine
SURGE_RUN_SKIP_RATE = 0.20       # per-run alert if skip-rate exceeds
SURGE_RUN_FORGED_RATE = 0.05     # per-run alert if forged-ref-rate exceeds

_REF_MAX_LEN = 16                # a real marker is short; longer => drop (S13)
_REF_RE = re.compile(r"^⟨[tc]\d{1,9}⟩$")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")

EXTRACTOR_VERSION = "marker-extract-0068.v1"
_DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
_MODEL = "deepseek-chat"

# loc1 prompt — ported verbatim from research
# comp-metric-regression/comp-metric-prompt.v-loc1.txt (versioned with the code so
# the production prompt cannot silently drift from a file under operations/).
LOC1_PROMPT = (
    "You are reading a nonprofit organization's annual or impact report. The text is "
    "TAGGED: each text line and each table cell ends with a source marker in angle "
    "brackets — like ⟨t42⟩ for a text line or ⟨c17⟩ for a table "
    "cell. These markers identify exactly where each piece appears in the document.\n\n"
    "STEP 1 — Understand, then SELECT RUTHLESSLY the points a reader should remember — "
    "the ones on a ONE-PAGE summary of the organization's year: headline outcomes "
    "(people/households served, success rates), reach and scale (communities served, "
    "total dollars, total economic impact), signature program results and "
    "mission-defining context (e.g., the poverty/ALICE rate the org exists to address).\n"
    "EXCLUDE operational / line-item detail even with numbers: single-program or "
    "single-location enrollment counts, per-channel or per-county breakdowns of a larger "
    "total, internal unit costs / cost-per-X / monthly package values / application "
    "counts, sub-rows of a program-breakdown table.\n"
    "A typical report yields roughly 5-15 such points. If longer, cut back.\n\n"
    "STEP 2 — For each: \"statement\" (one complete self-contained sentence), "
    "\"metric_value\" (plain number, no commas/$/%), \"label\" (2-6 words naming what it "
    "measures), \"unit\" (what is being counted), \"value_ref\" (the ⟨⟩ marker "
    "on the line or cell where this VALUE appears), \"subject_ref\" (the ⟨⟩ "
    "marker where this metric's SUBJECT/label appears).\n"
    "For value_ref and subject_ref: use ONLY markers that actually appear in the text "
    "above. If you cannot locate one, use null. NEVER invent or guess a marker.\n"
    "Return a JSON array, most important first. ONLY the JSON array.\n"
)


# ============================================================
# Pure helpers
# ============================================================

def sanitize_for_sink(s: Any, max_len: int = 200) -> str:
    """Strip ANSI + control chars (\\r/\\n/etc.) from source-derived text before it
    reaches any log / stats / UI sink (spec §8/S14 — terminal log-forging guard)."""
    if s is None:
        return ""
    s = str(s)
    s = _ANSI_RE.sub("", s)
    s = _CTRL_RE.sub(" ", s)
    if len(s) > max_len:
        s = s[:max_len] + "…"
    return s


def validate_ref(raw: Any) -> str | None:
    """Strict parse-time ref validation (S13). Returns the bare id (``t42``) for a
    well-formed ``⟨t42⟩``/``⟨c17⟩``, else ``None``. Oversized / malformed / injected
    refs are rejected here, before any sink or the resolver."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s or len(s) > _REF_MAX_LEN:
        return None
    if not _REF_RE.match(s):
        return None
    return s.strip("⟨⟩")


def compute_eligibility(
    sections: list[tuple], tables: list[tuple], *, threshold: float = ELIGIBILITY_THRESHOLD,
) -> tuple[bool, str, dict]:
    """Doc-level coordinate eligibility (§5.4).

    Eligible iff >= ``threshold`` of text elements carry a bbox AND every table
    cell carries row+col+bbox. Returns ``(eligible, reason, stats)``.
    """
    text_total = text_bbox = cells_total = cells_full = 0
    for _body, sl in sections:
        for it in (sl or []):
            if not (it.get("text") or "").strip():
                continue
            text_total += 1
            loc = (it.get("locations") or [{}])[0]
            if loc.get("bbox"):
                text_bbox += 1
    for _page, cl in tables:
        for cell in (cl or []):
            cells_total += 1
            if cell.get("row") is not None and cell.get("col") is not None and cell.get("bbox"):
                cells_full += 1
    stats = {"text_total": text_total, "text_bbox": text_bbox,
             "cells_total": cells_total, "cells_full": cells_full}
    if text_total == 0 and cells_total == 0:
        return False, "no_coordinates", stats
    if cells_total and cells_full < cells_total:
        return False, "cells_missing_rowcolbbox", stats
    if text_total and (text_bbox / text_total) < threshold:
        return False, "text_bbox_below_threshold", stats
    return True, "eligible", stats


def parse_version_trusted(parse_version: Any, trusted: set[str]) -> bool:
    """A coordinate snapshot is trusted only from a known, provenance-tracked parse
    version (§8). A null / sentinel / unknown parse_version is NOT trusted."""
    if not isinstance(parse_version, str) or not parse_version:
        return False
    if parse_version == "legacy-unknown":
        return False
    return parse_version in trusted


def surge_quarantine(n_selected: int, n_invalid: int, n_value_null: int) -> bool:
    """Per-doc adversarial signal (§5.9): quarantine when most selected metrics have
    a null/forged value_ref AND at least the absolute floor of invalid refs (the
    floor avoids false-quarantining a 1-2 metric doc — Gemini MEDIUM)."""
    if n_selected <= 0:
        return False
    if n_invalid < SURGE_DOC_MIN_INVALID:
        return False
    return (n_value_null / n_selected) > SURGE_DOC_NULL_FRAC


# ============================================================
# loc1 output normalization
# ============================================================

def normalize_loc1(raw: Any) -> tuple[list[dict], int]:
    """Normalize the loc1 LLM output to a clean metric list, applying strict ref
    grammar at parse-time. Returns ``(metrics, grammar_forged_count)``.

    Each returned metric has ``value_ref``/``subject_ref`` either a bare valid id
    (``t42``) or ``None``; a ref that failed the grammar is nulled here and counted
    as forged BEFORE it can reach a sink or the resolver (S13)."""
    if isinstance(raw, dict):
        raw = raw.get("metrics") or []
    if not isinstance(raw, list):
        return [], 0
    out: list[dict] = []
    grammar_forged = 0
    for m in raw:
        if not isinstance(m, dict):
            continue
        val = m.get("metric_value")
        if val is not None:
            try:
                val = float(val)
            except (ValueError, TypeError):
                val = None
        clean = {
            "statement": m.get("statement"),
            "metric_value": val,
            "label": m.get("label"),
            "unit": m.get("unit"),
        }
        for ref_key in ("value_ref", "subject_ref"):
            cited = m.get(ref_key)
            valid = validate_ref(cited)
            if valid is None and cited not in (None, ""):
                grammar_forged += 1   # a non-empty cited ref that failed grammar
            clean[ref_key] = valid
        out.append(clean)
    return out, grammar_forged


# ============================================================
# Storage (atomic delete-then-insert per (run_id, content_sha256))
# ============================================================

_INSERT_SQL = text("""
    INSERT INTO lava_vocab.llm_metrics
        (run_id, content_sha256, source_org_ein,
         metric_text, metric_type, metric_value, unit, geo_impact, source_snippet,
         value_ref, subject_ref,
         value_page, value_bbox, value_row, value_col, value_table,
         subject_page, subject_bbox, subject_row, subject_col, subject_table,
         marker_resolved, parse_version, render_version)
    VALUES
        (:run_id, :sha, :ein,
         :metric_text, :metric_type, :metric_value, :unit, :geo_impact, :source_snippet,
         :value_ref, :subject_ref,
         :value_page, CAST(:value_bbox AS JSONB), :value_row, :value_col, :value_table,
         :subject_page, CAST(:subject_bbox AS JSONB), :subject_row, :subject_col, :subject_table,
         :marker_resolved, :parse_version, :render_version)
""")


def _row_params(run_id, sha, ein, row, parse_version):
    p = {
        "run_id": run_id, "sha": sha, "ein": ein,
        "metric_text": row.get("statement"),
        "metric_type": row.get("label"),
        "metric_value": row.get("metric_value"),
        "unit": row.get("unit"),
        "geo_impact": None,
        "source_snippet": row.get("statement"),
        "marker_resolved": row["marker_resolved"],
        "parse_version": parse_version,
        "render_version": RENDER_VERSION,
    }
    for pref in ("value", "subject"):
        p[f"{pref}_ref"] = row.get(f"{pref}_ref")
        bbox = row.get(f"{pref}_bbox")
        p[f"{pref}_bbox"] = json.dumps(bbox) if bbox is not None else None
        for f in ("page", "row", "col", "table"):
            p[f"{pref}_{f}"] = row.get(f"{pref}_{f}")
    return p


def write_doc_atomic(conn, run_id: int, sha: str, ein: str, rows: list[dict], parse_version: str):
    """Delete this run_tag's rows for the doc then insert, in the caller's
    transaction (atomic per (run_id, content_sha256) — S9). Parameterized (Gemini LOW)."""
    conn.execute(text(
        "DELETE FROM lava_vocab.llm_metrics WHERE run_id = :run_id AND content_sha256 = :sha"
    ), {"run_id": run_id, "sha": sha})
    for row in rows:
        conn.execute(_INSERT_SQL, _row_params(run_id, sha, ein, row, parse_version))


# ============================================================
# Orchestration
# ============================================================

@dataclass
class DocResult:
    sha: str
    status: str                         # 'extracted' | 'skipped' | 'quarantined' | 'error'
    reason: str = ""
    metrics: int = 0
    truncated: bool = False
    value_null: int = 0
    forged: int = 0
    flags: dict = field(default_factory=dict)


def extract_document_markers(
    engine: Engine,
    sha: str,
    ein: str,
    run_id: int,
    *,
    parse_version: str,
    trusted_versions: set[str],
    chat_fn: Callable[[str, str], Any],
    write: bool = True,
) -> DocResult:
    """Full marker extraction for one document. Returns a ``DocResult``; on skip /
    quarantine no production rows are written (counted as failed extraction)."""
    # 1. provenance binding
    if not parse_version_trusted(parse_version, trusted_versions):
        return DocResult(sha, "skipped", "parse_unverified")

    # 2. render (eligibility computed from the same parse rows) + item ceiling
    try:
        with engine.connect() as conn:
            secs = conn.execute(text(
                "SELECT body_text, source_locations FROM lava_parse.sections "
                "WHERE content_sha256=:s ORDER BY section_index"), {"s": sha}).fetchall()
            tabs = conn.execute(text(
                "SELECT page_number, cell_locations FROM lava_parse.tables "
                "WHERE content_sha256=:s ORDER BY table_index"), {"s": sha}).fetchall()
            sections = [(b, sl) for b, sl in secs]
            tables = [(p, cl) for p, cl in tabs]
            eligible, reason, _stats = compute_eligibility(sections, tables)
            if not eligible:
                return DocResult(sha, "skipped", reason)
            render = render_tagged(conn, sha)
    except SkipDocument as sd:
        return DocResult(sha, "skipped", sd.reason)

    if not render.tagged_text.strip():
        return DocResult(sha, "skipped", "empty_render")

    # 3. one-pass loc1 extraction
    try:
        raw = chat_fn(LOC1_PROMPT, render.tagged_text)
    except Exception as exc:  # noqa: BLE001 — the runner records, never crashes the batch
        log.error("loc1 call failed for %s: %s", sha[:16], sanitize_for_sink(repr(exc)))
        return DocResult(sha, "error", "llm_error")

    # 4. strict ref grammar at parse-time (before any sink / resolver)
    metrics, grammar_forged = normalize_loc1(raw)
    if not metrics:
        return DocResult(sha, "skipped", "no_metrics", truncated=render.truncated)

    # 5. resolve markers
    rows = []
    resolve_forged = value_null = 0
    for m in metrics:
        r = resolve_metric(m, render.idmap)
        row = {**m, **r}
        rows.append(row)
        if r["flags"]["value_forged"]:
            resolve_forged += 1
        if r["value_ref"] is None:
            value_null += 1

    n_invalid = grammar_forged + resolve_forged
    # 6. per-doc surge quarantine (no rows written; counted as failed)
    if surge_quarantine(len(metrics), n_invalid, value_null):
        return DocResult(sha, "quarantined", "forged_ref_surge",
                         metrics=len(metrics), truncated=render.truncated,
                         value_null=value_null, forged=n_invalid)

    # 7. atomic write
    if write:
        with engine.begin() as conn:
            write_doc_atomic(conn, run_id, sha, ein, rows, parse_version)

    return DocResult(sha, "extracted", "ok", metrics=len(rows), truncated=render.truncated,
                     value_null=value_null, forged=n_invalid)


def deepseek_chat(api_key: str):
    """Build a real loc1 ``chat_fn`` (DeepSeek, temperature 0). Returns a callable
    ``(system, user) -> parsed JSON``. Network — used only by the live runner."""
    import httpx

    client = httpx.Client(timeout=180)

    def _chat(system: str, user: str):
        resp = client.post(
            _DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": _MODEL, "temperature": 0.0, "max_tokens": 3000,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("```", 2)[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip().rstrip("`").strip()
        return json.loads(content)

    return _chat


def trusted_parse_versions(engine: Engine) -> set[str]:
    """The set of parse_version strings produced by real parses (provenance trust
    set, §8). Every row in lava_parse.documents came from a recorded parse run."""
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT DISTINCT parse_version FROM lava_parse.documents WHERE parse_version IS NOT NULL"
        )).fetchall()
    return {r[0] for r in rows if r[0]}


def _ensure_run(engine: Engine, run_tag: str) -> int:
    """Get-or-create the extraction_runs row; return its id."""
    with engine.begin() as conn:
        rid = conn.execute(text(
            "SELECT id FROM lava_vocab.extraction_runs WHERE run_tag=:t"), {"t": run_tag}).scalar()
        if rid is None:
            rid = conn.execute(text(
                "INSERT INTO lava_vocab.extraction_runs (run_tag, extractor_version, started_at) "
                "VALUES (:t, :v, now()) RETURNING id"),
                {"t": run_tag, "v": EXTRACTOR_VERSION}).scalar()
    return rid


def run_extraction(
    engine: Engine,
    run_tag: str,
    shas: list[str],
    *,
    chat_fn: Callable[[str, str], Any],
    write: bool = True,
) -> dict:
    """Operator entrypoint: marker-extract a list of docs under ``run_tag``.

    Idempotent per ``(run_id, content_sha256)``; persists the per-run report into
    ``extraction_runs.stats_json``. Skipped/quarantined docs are counted as failed
    and do not enter production output. Caller supplies ``chat_fn`` (live DeepSeek
    via :func:`deepseek_chat`, or a stub for dry runs).

    ``write=False`` is a **fully read-only dry run**: no metric rows, AND no
    ``extraction_runs`` row or stats update — nothing is persisted at all.
    """
    # Only touch extraction_runs when actually persisting (write=True). A dry run
    # uses a sentinel run_id that the read-only per-doc path never references.
    run_id = _ensure_run(engine, run_tag) if write else -1
    trusted = trusted_parse_versions(engine)
    results: list[DocResult] = []
    for sha in shas:
        with engine.connect() as conn:
            meta = conn.execute(text(
                "SELECT source_org_ein, parse_version FROM lava_parse.documents "
                "WHERE content_sha256=:s"), {"s": sha}).fetchone()
        ein = (meta[0] if meta else None) or "00-0000000"
        parse_version = meta[1] if meta else None
        try:
            r = extract_document_markers(
                engine, sha, ein, run_id, parse_version=parse_version,
                trusted_versions=trusted, chat_fn=chat_fn, write=write)
        except Exception as exc:  # noqa: BLE001 — one doc never sinks the batch
            log.error("doc %s failed: %s", sha[:16], sanitize_for_sink(repr(exc)))
            r = DocResult(sha, "error", "unhandled")
        results.append(r)
        log.info("%s -> %s/%s", sha[:12], r.status, sanitize_for_sink(r.reason, 40))
    stats = aggregate_run_stats(results)
    if write:
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE lava_vocab.extraction_runs SET finished_at=now(), "
                "stats_json=CAST(:s AS JSONB) WHERE id=:id"),
                {"s": json.dumps(stats), "id": run_id})
    return stats


def aggregate_run_stats(results: list[DocResult]) -> dict:
    """Per-run report (§5.9 — abnormal states first-class). Skipped/quarantined docs
    count as failed extraction; surge rates surfaced as alerts."""
    n = len(results)
    extracted = [r for r in results if r.status == "extracted"]
    skipped = [r for r in results if r.status in ("skipped", "quarantined", "error")]
    metrics_total = sum(r.metrics for r in extracted)
    forged_total = sum(r.forged for r in extracted)
    skip_reasons: dict[str, int] = {}
    for r in skipped:
        key = sanitize_for_sink(r.reason, 64)
        skip_reasons[key] = skip_reasons.get(key, 0) + 1
    skip_rate = (len(skipped) / n) if n else 0.0
    forged_rate = (forged_total / metrics_total) if metrics_total else 0.0
    return {
        "docs_total": n,
        "docs_extracted": len(extracted),
        "docs_failed": len(skipped),             # skipped/quarantined/error = failed
        "skip_reasons": skip_reasons,
        "metrics_total": metrics_total,
        "metrics_value_null": sum(r.value_null for r in extracted),
        "metrics_forged_refs": forged_total,
        "docs_truncated": sum(1 for r in extracted if r.truncated),
        "skip_rate": round(skip_rate, 4),
        "forged_rate": round(forged_rate, 4),
        "alerts": {
            "skip_rate_high": skip_rate > SURGE_RUN_SKIP_RATE,
            "forged_rate_high": forged_rate > SURGE_RUN_FORGED_RATE,
        },
    }

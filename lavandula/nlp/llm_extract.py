"""LLM-based impact metric and story extraction (Spec 0051).

Extracts structured metrics and impact stories from nonprofit annual/impact
reports via the DeepSeek API. Independent of Django — can be called from a
plain Python script or the management command.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

_DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
_MODEL = "deepseek-chat"
_MAX_TEXT_CHARS = 60_000
_MIN_TEXT_CHARS = 100
_MAX_TOKENS = 8192
_TIMEOUT = 90
_RETRY_STATUSES = (429, 500, 503)
_RETRY_DELAYS = (1, 2, 4)

# DeepSeek V3 pricing (per million tokens)
_INPUT_COST_PER_M = 0.14
_OUTPUT_COST_PER_M = 0.28

_SYSTEM_PROMPT = """You extract structured data from nonprofit annual reports and impact reports.
Extract TWO types of content:

## 1. IMPACT METRICS
Concrete numeric measurements of the organization's impact, reach, or scale.

Extract:
- Program outcomes (people served, patients supported, hours delivered, meals provided, youth reached)
- Geographic reach (states, countries, communities, facilities, partner locations)
- Organizational scale (volunteers, staff, languages spoken, cancer types, referring organizations)
- Financial summary (total revenue, total expenses — only top-line)

For each metric, return:
- "metric_text": Short natural language description (e.g., "1,514 cancer patients and caregivers served")
- "metric_type": Short category label for what is being measured, without the number (e.g., "patients and caregivers supported", "volunteer hours", "meals and snacks", "youth served", "program participants"). Use lowercase.
- "metric_value": The numeric value as a number
- "unit": What is being counted (e.g., "people", "hours", "states", "languages")
- "geo_impact": Geographic scope of this metric. One of: "LOCAL" (single city/county), "STATE" (single state), "NATIONAL" (multi-state or nationwide), or "GLOBAL" (international). Infer from context clues in the text.
- "source_snippet": The exact phrase from the text containing the metric

Extract every distinct metric even if the numbers are related (e.g., "11,500 total served" and "1,514 served through matching" are separate metrics). For "20+" or "over 500", use the stated number (20, 500).

Skip: detailed financial breakdowns (individual line items, percentages of budget), page numbers, years as dates, addresses, phone numbers, ZIP codes, individual donor names and gift amounts, biographical details (ages, years of experience).

## 2. IMPACT STORIES
Personal narratives, testimonials, and case studies about people helped.

For each story, return:
- "story_title": Short descriptive title
- "story_summary": 2-3 sentence summary
- "people_mentioned": Names of people featured (first names only, or "Anonymous" if unnamed)
- "program": Which program or service, if identifiable
- "themes": Array of 1-3 theme tags
- "source_snippet": Key 1-2 sentences anchoring the story

Skip: organizational founding history, board/staff listings, event recaps with only dates/numbers.

## OUTPUT FORMAT
Return a single JSON object with two keys:
{
  "metrics": [ ... array of metric objects ... ],
  "stories": [ ... array of story objects ... ]
}

IMPORTANT: Only extract content explicitly present in the text. Never invent or fabricate. If none found for a category, use an empty array.

Return ONLY the JSON object, no other text."""

_S3_BUCKET = "lavandula-nonprofit-collaterals"
_S3_PREFIX = "pdfs"


def _estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    return (
        prompt_tokens * _INPUT_COST_PER_M / 1_000_000
        + completion_tokens * _OUTPUT_COST_PER_M / 1_000_000
    )


def get_document_text(engine: Engine, sha: str, s3_client, tmp_dir: Path) -> str | None:
    """Text source precedence: Docling sections -> pdftotext -> None."""
    # 1. Try Docling sections
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT heading, body_text
            FROM lava_parse.sections
            WHERE content_sha256 = :sha
            ORDER BY section_index
        """), {"sha": sha}).fetchall()

    if rows:
        total_len = sum(len(r[1]) for r in rows)
        if total_len >= _MIN_TEXT_CHARS:
            parts = []
            for heading, body in rows:
                if heading:
                    parts.append(f"[{heading}]")
                parts.append(body)
            return "\n\n".join(parts)[:_MAX_TEXT_CHARS]

    # 2. Try pdftotext
    pdf_key = f"{_S3_PREFIX}/{sha}.pdf"
    pdf_dir = None
    try:
        pdf_dir = Path(tempfile.mkdtemp(dir=tmp_dir, prefix="pdf_"))
        pdf_path = pdf_dir / f"{sha}.pdf"

        obj = s3_client.get_object(Bucket=_S3_BUCKET, Key=pdf_key)
        pdf_path.write_bytes(obj["Body"].read())

        result = subprocess.run(
            ["pdftotext", str(pdf_path), "-"],
            capture_output=True, timeout=30, text=True,
        )
        pdf_text = result.stdout.strip()
        if len(pdf_text) >= _MIN_TEXT_CHARS:
            return pdf_text[:_MAX_TEXT_CHARS]
    except subprocess.TimeoutExpired:
        log.warning("pdftotext timed out for %s", sha[:16])
    except Exception as exc:
        log.warning("Failed to get PDF text for %s: %s", sha[:16], type(exc).__name__)
    finally:
        if pdf_dir and pdf_dir.exists():
            shutil.rmtree(pdf_dir, ignore_errors=True)

    return None


def call_deepseek(api_key: str, document_text: str, http_client: httpx.Client) -> tuple[dict, int, int]:
    """Call DeepSeek API. Returns (parsed_json, prompt_tokens, completion_tokens).

    Retries on 429/500/503 with exponential backoff.
    Raises on persistent failure.
    """
    payload = {
        "model": _MODEL,
        "temperature": 0.0,
        "max_tokens": _MAX_TOKENS,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": document_text},
        ],
    }

    last_exc = None
    for attempt, delay in enumerate((*_RETRY_DELAYS, None)):
        try:
            resp = http_client.post(
                _DEEPSEEK_URL,
                json=payload,
                timeout=_TIMEOUT,
            )
            if resp.status_code in _RETRY_STATUSES and delay is not None:
                log.warning(
                    "DeepSeek returned %d (attempt %d), retrying in %ds",
                    resp.status_code, attempt + 1, delay,
                )
                time.sleep(delay)
                continue

            resp.raise_for_status()
            data = resp.json()

            prompt_tokens = data.get("usage", {}).get("prompt_tokens", 0)
            completion_tokens = data.get("usage", {}).get("completion_tokens", 0)

            content = data["choices"][0]["message"]["content"].strip()
            # Strip markdown fences if present
            if content.startswith("```"):
                lines = content.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                content = "\n".join(lines)

            parsed = json.loads(content)
            return parsed, prompt_tokens, completion_tokens

        except httpx.HTTPStatusError as exc:
            last_exc = exc
            if delay is not None and exc.response.status_code in _RETRY_STATUSES:
                log.warning(
                    "DeepSeek HTTP %d (attempt %d), retrying in %ds",
                    exc.response.status_code, attempt + 1, delay,
                )
                time.sleep(delay)
                continue
            raise
        except json.JSONDecodeError as exc:
            log.error(
                "JSON parse failure for DeepSeek response (%.200s): %s",
                content[:200] if 'content' in dir() else "?", type(exc).__name__,
            )
            raise
        except Exception as exc:
            last_exc = exc
            if delay is not None:
                log.warning(
                    "DeepSeek call failed (attempt %d): %s, retrying in %ds",
                    attempt + 1, type(exc).__name__, delay,
                )
                time.sleep(delay)
                continue
            raise

    raise last_exc or RuntimeError("DeepSeek call failed after retries")


def validate_response(result: dict, text_length: int) -> dict | None:
    """Validate JSON structure and apply hallucination guard.

    Returns cleaned result dict or None if invalid.
    """
    # Hallucination guard: discard if input text was too short
    if text_length < _MIN_TEXT_CHARS:
        if result.get("metrics") or result.get("stories"):
            log.warning("Hallucination guard: discarding response for short input (%d chars)", text_length)
        return None

    if not isinstance(result, dict):
        log.warning("Invalid response: not a dict")
        return None

    metrics = result.get("metrics")
    stories = result.get("stories")

    if not isinstance(metrics, list) or not isinstance(stories, list):
        log.warning("Invalid response: missing metrics or stories list")
        return None

    valid_metrics = []
    for m in metrics:
        if not isinstance(m, dict):
            continue
        if not isinstance(m.get("metric_text"), str) or not m["metric_text"]:
            continue
        # metric_value should be a number if present
        val = m.get("metric_value")
        if val is not None:
            try:
                m["metric_value"] = float(val)
            except (ValueError, TypeError):
                m["metric_value"] = None
        valid_metrics.append(m)

    valid_stories = []
    for s in stories:
        if not isinstance(s, dict):
            continue
        if not isinstance(s.get("story_title"), str) or not s["story_title"]:
            continue
        # Ensure people_mentioned and themes are lists
        if not isinstance(s.get("people_mentioned"), list):
            s["people_mentioned"] = []
        if not isinstance(s.get("themes"), list):
            s["themes"] = []
        valid_stories.append(s)

    return {"metrics": valid_metrics, "stories": valid_stories}


def _write_metrics(conn, run_id: int, sha: str, ein: str, metrics: list[dict]):
    """Delete existing + insert metrics for one document within a transaction."""
    conn.execute(text(
        "DELETE FROM lava_vocab.llm_metrics WHERE run_id = :run_id AND content_sha256 = :sha"
    ), {"run_id": run_id, "sha": sha})

    for m in metrics:
        conn.execute(text("""
            INSERT INTO lava_vocab.llm_metrics
                (run_id, content_sha256, source_org_ein,
                 metric_text, metric_type, metric_value, unit, geo_impact, source_snippet)
            VALUES (:run_id, :sha, :ein,
                    :metric_text, :metric_type, :metric_value, :unit, :geo_impact, :source_snippet)
        """), {
            "run_id": run_id, "sha": sha, "ein": ein,
            "metric_text": m.get("metric_text"),
            "metric_type": m.get("metric_type"),
            "metric_value": m.get("metric_value"),
            "unit": m.get("unit"),
            "geo_impact": m.get("geo_impact"),
            "source_snippet": m.get("source_snippet"),
        })


def _write_stories(conn, run_id: int, sha: str, ein: str, stories: list[dict]):
    """Delete existing + insert stories for one document within a transaction."""
    conn.execute(text(
        "DELETE FROM lava_vocab.llm_stories WHERE run_id = :run_id AND content_sha256 = :sha"
    ), {"run_id": run_id, "sha": sha})

    for s in stories:
        people = s.get("people_mentioned", [])
        if not isinstance(people, list):
            people = []
        people = [str(p) for p in people]

        themes = s.get("themes", [])
        if not isinstance(themes, list):
            themes = []
        themes = [str(t) for t in themes]

        conn.execute(text("""
            INSERT INTO lava_vocab.llm_stories
                (run_id, content_sha256, source_org_ein,
                 story_title, story_summary, people_mentioned, program, themes, source_snippet)
            VALUES (:run_id, :sha, :ein,
                    :story_title, :story_summary,
                    CAST(:people AS TEXT[]), :program,
                    CAST(:themes AS TEXT[]), :source_snippet)
        """), {
            "run_id": run_id, "sha": sha, "ein": ein,
            "story_title": s.get("story_title"),
            "story_summary": s.get("story_summary"),
            "people": people,
            "program": s.get("program"),
            "themes": themes,
            "source_snippet": s.get("source_snippet"),
        })


def extract_document(
    engine: Engine,
    sha: str,
    ein: str,
    run_id: int,
    api_key: str,
    s3_client,
    http_client: httpx.Client,
    tmp_dir: Path,
) -> dict:
    """Full extraction pipeline for one document.

    Returns stats dict: {metrics, stories, skipped, cost_usd, error, text_source}.
    """
    result = {
        "metrics": 0,
        "stories": 0,
        "skipped": False,
        "cost_usd": 0.0,
        "error": None,
        "text_source": None,
    }

    try:
        doc_text = get_document_text(engine, sha, s3_client, tmp_dir)
        if doc_text is None:
            result["skipped"] = True
            result["error"] = "insufficient_text"
            return result

        text_len = len(doc_text)
        result["text_source"] = "docling" if text_len > 0 else "pdftotext"

        parsed, prompt_tokens, completion_tokens = call_deepseek(api_key, doc_text, http_client)
        result["cost_usd"] = _estimate_cost(prompt_tokens, completion_tokens)

        validated = validate_response(parsed, text_len)
        if validated is None:
            result["skipped"] = True
            result["error"] = "invalid_response"
            return result

        metrics = validated["metrics"]
        stories = validated["stories"]

        if not metrics and not stories:
            result["skipped"] = True
            return result

        with engine.begin() as conn:
            _write_metrics(conn, run_id, sha, ein, metrics)
            _write_stories(conn, run_id, sha, ein, stories)

        result["metrics"] = len(metrics)
        result["stories"] = len(stories)

    except json.JSONDecodeError:
        result["error"] = "json_parse_error"
        log.error("JSON parse error extracting %s", sha[:16])
    except httpx.HTTPStatusError as exc:
        result["error"] = f"http_{exc.response.status_code}"
        log.error("HTTP %d extracting %s", exc.response.status_code, sha[:16])
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        log.error("Error extracting %s: %s", sha[:16], result["error"])

    return result

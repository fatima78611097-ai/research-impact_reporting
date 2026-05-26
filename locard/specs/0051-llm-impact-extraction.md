# Spec 0051: LLM Impact Extraction (Metrics + Stories)

**Status:** Draft
**Author:** Architect
**Created:** 2026-05-26
**Dependencies:** None (uses pdftotext on S3-archived PDFs; does not depend on Docling parsing)

## Problem Statement

Spec 0050 built a regex + term-dictionary pipeline for metric extraction. It produced 2.67M observations from 3,306 P20 documents — overwhelmingly noise. The median document generated 189 observations; the P99 hit 3,382. One document produced 93,650. The root cause is architectural: the pipeline matches every number in every sentence against every term in the archetype vocabulary, creating a combinatorial explosion of (term × number) pairs. Financial line items, address numbers, ZIP codes, and substring fragments ("ents", "port", "otal") all generate observations.

A competitor extracts 4-16 clean, structured metrics per report from the same documents. They also extract narrative impact stories. Their output includes fields like `metric_text`, `metric_value`, `unit`, `geo_impact`, and `source_snippet` — structured data that reads like a human wrote it.

The difference is approach: the competitor reads each document with an LLM that understands what each number means in context. A regex can find the number "12,000" but cannot determine whether it refers to meals served, dollars raised, or a ZIP code. The document itself contains the label — "We provided 12,000 meals to families facing food insecurity" — and an LLM reads it.

Experiments on 2026-05-26 validated this approach using DeepSeek. A combined prompt (metrics + stories in a single call) matched 100% of competitor metrics across three test reports while also extracting impact stories not available from the competitor. Cost per document: $0.001-0.005. Full P20 corpus (3,335 docs): ~$12-15.

## Goals

1. **Extract structured impact metrics** from each document via a single LLM call — metric text, numeric value, unit, and source snippet
2. **Extract impact stories** from the same call — title, summary, people mentioned, program, themes, and source snippet
3. **Store results in new tables** in `lava_vocab` schema alongside existing extraction infrastructure
4. **Provide dashboard controls** — start/stop extraction runs, progress tracking, cost monitoring, NTEE/state filtering
5. **Match or exceed competitor quality** — validated baseline: 100% match rate on 3 test reports
6. **Run on the full P20 corpus first**, then expand to all NTEE verticals (~56K documents)

## Non-Goals

- Replacing Spec 0049 (statistical NLP extraction) — that pipeline serves vocabulary discovery and archetype clustering, a different job
- Deprecating or dropping `lava_vocab.metric_observations` — the existing table stays; new tables are additive
- Real-time extraction (user uploads a PDF and gets instant results) — this is batch pipeline
- Normalizing metrics across orgs or across years — downstream concern
- OCR or image-based text extraction — pdftotext handles text-layer PDFs; image-heavy pages are a known gap addressed separately

## Design Principles

1. **The document is the source of truth.** The LLM reads what the document says each number means. No dictionary, no term matching, no vocabulary lookup. The document contains both the number and its label.

2. **Full document, single call.** Per-section extraction (tested and rejected) missed metrics because sections lack context about the organization. The LLM needs the whole document to distinguish "14 participated in the Portland Youth Dance Program" from "14" as a page number.

3. **Combined extraction.** Metrics and stories come from the same call. One prompt, one response, one cost. The model already has the full document loaded — extracting both types costs the same tokens as extracting one.

4. **pdftotext first, Docling later.** pdftotext is fast, free, and already available on all hosts. It misses image-heavy pages, but the majority of impact reports have a text layer. When Docling parsing (Spec 0046) is complete, the extraction can switch to Docling sections for better coverage with no prompt changes.

5. **Cost guardrails by default.** Every run has a maximum document count and estimated cost shown before confirmation. No accidental $200 runs.

## Technical Implementation

### Text Source

```
PDF in S3 → pdftotext → full document text → DeepSeek API call
```

The text source is `pdftotext` applied to the S3-archived PDF (`s3://lavandula-nonprofit-collaterals/pdfs/{sha256}.pdf`). This produces the full text layer without any parsing infrastructure. The text is truncated to 60,000 characters (well within DeepSeek's 64K context window) before sending to the API.

When Docling-parsed sections exist (`lava_parse.sections`), the pipeline should prefer them — concatenating all sections in order produces higher-quality text with headings preserved. The prompt and output schema are identical regardless of text source.

### LLM Configuration

- **Provider:** DeepSeek (OpenAI-compatible endpoint)
- **Base URL:** `https://api.deepseek.com/v1/chat/completions`
- **Model:** `deepseek-chat` (production name for DeepSeek V3)
- **API key:** SSM parameter `lavandula/deepseek/api_key` via `get_secret()`
- **Temperature:** 0.0 (deterministic extraction)
- **Max tokens:** 6,000 (sufficient for 30+ metrics and 10+ stories)
- **Timeout:** 90 seconds per call
- **HTTP client:** `httpx` (already installed, no new dependencies)

### System Prompt

```
You extract structured data from nonprofit annual reports and impact reports.
Extract TWO types of content:

## 1. IMPACT METRICS
Concrete numeric measurements of the organization's impact, reach, or scale.

For each metric, return:
- "metric_text": Short natural language description (e.g., "1,514 cancer patients
  and caregivers served")
- "metric_type": Short category label for what is being measured, without the number
  (e.g., "patients and caregivers supported", "volunteer hours", "meals and snacks",
  "youth served", "program participants"). Use lowercase.
- "metric_value": The numeric value as a number
- "unit": What is being counted (e.g., "people", "hours", "states", "languages")
- "geo_impact": Geographic scope of this metric. One of: "LOCAL" (single city/county),
  "STATE" (single state), "NATIONAL" (multi-state or nationwide), or "GLOBAL"
  (international). Infer from context clues in the text.
- "source_snippet": The exact phrase from the text containing the metric

Skip: financial line items, page numbers, years as dates, addresses, phone numbers,
ZIP codes, donor names, staff lists, board member counts, photo credits.

## 2. IMPACT STORIES
Personal narratives, testimonials, and case studies about people helped.

For each story, return:
- "story_title": Short descriptive title
- "story_summary": 2-3 sentence summary
- "people_mentioned": Names of people featured (first names only, or "Anonymous"
  if unnamed)
- "program": Which program or service, if identifiable
- "themes": Array of 1-3 theme tags
- "source_snippet": Key 1-2 sentences anchoring the story

Skip: organizational founding history, board/staff listings, event recaps with
only dates/numbers, financial summaries.

## OUTPUT FORMAT
Return a single JSON object with two keys:
{
  "metrics": [ ... array of metric objects ... ],
  "stories": [ ... array of story objects ... ]
}

IMPORTANT: Only extract content explicitly present in the text. Never invent
or fabricate. If none found for a category, use an empty array.

Return ONLY the JSON object, no other text.
```

### Schema Additions (`lava_vocab`)

```sql
-- Extraction run tracking (reuses existing extraction_runs table)
-- run_tag format: "llm-extract-{ntee}-{date}" e.g. "llm-extract-p20-20260526"

CREATE TABLE IF NOT EXISTS lava_vocab.llm_metrics (
    id BIGSERIAL PRIMARY KEY,
    run_id INT NOT NULL REFERENCES lava_vocab.extraction_runs(id),
    content_sha256 TEXT NOT NULL,
    source_org_ein TEXT NOT NULL,
    metric_text TEXT NOT NULL,
    metric_type TEXT,
    metric_value REAL,
    unit TEXT,
    geo_impact TEXT,
    source_snippet TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_llm_metrics_ein ON lava_vocab.llm_metrics(source_org_ein);
CREATE INDEX idx_llm_metrics_sha ON lava_vocab.llm_metrics(content_sha256);
CREATE INDEX idx_llm_metrics_run ON lava_vocab.llm_metrics(run_id);

GRANT SELECT, INSERT, DELETE ON lava_vocab.llm_metrics TO research_app;
GRANT USAGE, SELECT ON lava_vocab.llm_metrics_id_seq TO research_app;


CREATE TABLE IF NOT EXISTS lava_vocab.llm_stories (
    id BIGSERIAL PRIMARY KEY,
    run_id INT NOT NULL REFERENCES lava_vocab.extraction_runs(id),
    content_sha256 TEXT NOT NULL,
    source_org_ein TEXT NOT NULL,
    story_title TEXT NOT NULL,
    story_summary TEXT,
    people_mentioned TEXT[],
    program TEXT,
    themes TEXT[],
    source_snippet TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_llm_stories_ein ON lava_vocab.llm_stories(source_org_ein);
CREATE INDEX idx_llm_stories_sha ON lava_vocab.llm_stories(content_sha256);
CREATE INDEX idx_llm_stories_run ON lava_vocab.llm_stories(run_id);

GRANT SELECT, INSERT, DELETE ON lava_vocab.llm_stories TO research_app;
GRANT USAGE, SELECT ON lava_vocab.llm_stories_id_seq TO research_app;
```

Key design decisions:
- **Separate tables from Spec 0050's `metric_observations`**: Different provenance, different schema, different trust level. LLM-extracted metrics have `metric_text` (human-readable) instead of `term` (vocabulary key). No need for `archetype_id`, `confidence`, or `unit_hint` columns — the LLM provides the full description.
- **No UNIQUE constraint on metrics**: The same number may appear in different phrasings. Dedup is per-document: `DELETE FROM llm_metrics WHERE run_id = :run_id AND content_sha256 = :sha` before inserting that document's metrics. Same for stories. The DELETE is always scoped to the current run_id + document SHA — never a bulk table wipe.
- **Array columns for stories**: `people_mentioned` and `themes` use Postgres TEXT[] arrays. Simple, queryable, no join tables needed for a single-operator system.
- **`metric_value` as REAL**: The LLM returns parsed numbers (1492.0 not "1,492"). Sufficient precision for impact metrics.
- **`metric_type` as freeform TEXT**: The LLM generates ad-hoc category labels (e.g., "volunteer hours", "youth served"). These are NOT from a controlled vocabulary — they will vary across documents. After the full corpus extraction, the operator will review the distribution, normalize the long tail, and build a consistent taxonomy. The raw LLM labels are the input to that process, not the final product.
- **`geo_impact` as TEXT**: One of LOCAL/STATE/NATIONAL/GLOBAL, inferred by the LLM from context. Same approach — review after corpus extraction, refine if needed.

### Management Command

```
python3 manage.py llm_extract <run_tag> [--ntee P2%] [--state TX] [--ein 760305357]
    [--batch-size 20] [--parallel 5] [--resume] [--dry-run] [--max-docs 100]
    [--cost-limit 25.00]
```

Arguments with validation (all validated before any DB or API calls):
- `run_tag`: Unique identifier for this run. Must match `^[a-zA-Z0-9_-]{1,64}$`.
- `--ntee`: NTEE prefix filter. Must match `^[A-Z][0-9]*%?$` (default: `P2%`).
- `--state`: Two-letter state code. Must match `^[A-Z]{2}$`.
- `--ein`: Nine-digit EIN. Must match `^\d{9}$`.
- `--batch-size`: Positive integer, 1-100 (default: 20).
- `--parallel`: Positive integer, 1-20 (default: 5). Capped at 20 to stay within API rate limits.
- `--resume`: Skip documents already extracted in this run.
- `--dry-run`: Show eligible document count and estimated cost, then exit.
- `--max-docs`: Positive integer (safety cap on documents to process).
- `--cost-limit`: Positive float in USD (default: $25.00). Run aborts when cumulative estimated cost exceeds this.

### Run Lifecycle

Extraction runs use the existing `extraction_runs` table with these states in `stats_json`:

- **running**: Actively processing documents. `stats_json` updated every batch with: `docs_processed`, `docs_skipped`, `docs_failed`, `metrics_found`, `stories_found`, `cost_usd`, `elapsed_s`.
- **completed**: All eligible documents processed (including skips and failures). Final stats written.
- **aborted**: Run stopped early due to `--cost-limit` breach, SIGINT, or unrecoverable error. Partial results are retained in the tables — the run is resumable.
- **failed**: Unexpected crash. Same as aborted — partial results retained, resumable.

### Resume Semantics

`--resume` identifies completed documents by checking for any row in `llm_metrics` OR `llm_stories` with matching `(run_id, content_sha256)`. A document with zero metrics AND zero stories (legitimately empty report) is recorded as a skip in `stats_json.skipped_shas` (a JSON array of SHA256 strings) so `--resume` does not re-process it.

### Text Source Precedence

Strictly ordered, no ambiguity:

1. If `lava_parse.sections` has rows for this SHA with total `LENGTH(body_text) >= 100`: concatenate all sections in order, with `[heading]` prefixes. Use this text.
2. Otherwise: download PDF from S3, run `pdftotext`. If result >= 100 chars: use this text.
3. Otherwise: skip the document (`insufficient_text`).

Partial Docling data (some sections parsed, others missing) is still used — it's better than pdftotext when available.

### Extraction Pipeline

```
1. Create or resume extraction_run record
2. Query eligible documents:
   - JOIN lava_corpus.corpus ON material_type IN ('annual_report', 'impact_report')
   - LEFT JOIN to skip already-extracted (if --resume): check llm_metrics/llm_stories
     and stats_json.skipped_shas
   - Apply --ntee, --state, --ein filters
   - LIMIT --max-docs
3. Show dry-run summary: doc count, estimated cost, estimated time
   - Cost estimate: doc_count × $0.004 (average from experiments)
   - Time estimate: doc_count / parallel × 10s average per call
4. For each batch of documents (ThreadPoolExecutor with --parallel workers):
   a. Get text via precedence rules above (Docling → pdftotext → skip)
   b. Truncate to 60,000 chars
   c. POST to DeepSeek API with combined prompt
   d. Parse JSON response:
      - Must be a dict with "metrics" (list) and "stories" (list) keys
      - Each metric must have "metric_text" (str) and "metric_value" (number)
      - "metric_type" (str, optional), "geo_impact" (str, optional) stored as-is
      - Each story must have "story_title" (str)
      - Missing optional fields default to NULL
      - If top-level structure invalid: log and skip
   e. Write metrics to lava_vocab.llm_metrics
   f. Write stories to lava_vocab.llm_stories
   g. Update running stats in extraction_run
   h. Check cumulative cost against --cost-limit; abort if exceeded
5. Update extraction_run with final stats and status (completed/aborted)
6. Log summary
```

### Error Handling

- **API errors (429, 500, 503):** Retry with exponential backoff (1s, 2s, 4s), max 3 retries. Log and skip document on persistent failure.
- **JSON parse failure:** Log the raw response, skip document. Do not attempt to repair malformed JSON.
- **Empty text (< 100 chars):** Skip with `skipped_reason='insufficient_text'` in the run stats. Not an error — these are image-only PDFs.
- **Hallucination guard:** If the document text is empty or very short (< 100 chars) but the LLM returns metrics/stories, discard the entire response. This was validated in experiments — DeepSeek fabricates plausible-looking stories when given no input.
- **Cost tracking:** Accumulate prompt + completion tokens per call. Estimate cost using DeepSeek pricing ($0.14/M input, $0.28/M output for V3). Abort run if `--cost-limit` exceeded.

### Cost Model

Based on experiment results (2026-05-26):

| Document Type | Avg Input Tokens | Avg Output Tokens | Cost/Doc |
|---------------|:---:|:---:|:---:|
| Text-heavy (CanCare) | 3,700 | 2,700 | $0.005 |
| Text-light (BGCSM) | 760 | 1,270 | $0.002 |
| Average estimate | 2,000 | 2,000 | $0.003-0.004 |

- **P20 corpus (3,335 docs):** ~$12-15, ~45 min at parallel=10
- **Full corpus (56K docs):** ~$170-225, ~12 hours at parallel=10

### Dashboard Integration

Add to the existing pipeline dashboard:

1. **LLM Extraction page** (`/pipeline/llm-extract/`):
   - Run history table: run_tag, status, doc count, metrics found, stories found, cost, duration
   - Start new run form: NTEE filter, state filter, max docs, parallel workers, cost limit
   - Progress bar for running extraction (poll via AJAX)

2. **Org detail page** additions:
   - "LLM Metrics" tab showing extracted metrics for this org (metric_text, value, unit, snippet)
   - "Impact Stories" tab showing extracted stories (title, summary, people, program, themes)
   - Source document link for each metric/story

### Relationship to Spec 0050

Spec 0050's regex pipeline and this LLM pipeline serve different purposes:
- **0050 (regex):** Corpus-wide statistical analysis. Term frequency, archetype vocabulary linkage, phrasing patterns across 56K documents. Noisy but comprehensive.
- **0051 (LLM):** Per-org structured extraction. Clean, human-readable metrics and stories suitable for the AI interviewer and external APIs.

Both tables coexist. No migration or deprecation of 0050 data.

## Acceptance Criteria

1. Management command `llm_extract` runs end-to-end on a single EIN
2. `--dry-run` shows document count and estimated cost without making API calls
3. `--resume` skips already-extracted documents
4. `--cost-limit` aborts the run when estimated cost exceeds threshold
5. `--parallel` controls concurrent API calls (default 5)
6. Metrics written to `lava_vocab.llm_metrics` with correct foreign keys
7. Stories written to `lava_vocab.llm_stories` with correct foreign keys
8. Documents with < 100 chars of text are skipped (not sent to API)
9. Empty/hallucinated responses are discarded when input text is insufficient
10. Docling sections used when available, pdftotext fallback otherwise
11. Dashboard page shows run history with stats
12. Dashboard start form creates and launches extraction run
13. Org detail page shows LLM-extracted metrics and stories
14. Extraction of CanCare (760305357) produces >= 20 metrics including "1,514 cancer patients" and "9,611 visits" (frozen fixture from 2026-05-26 experiment — not dependent on competitor API)
15. Extraction of Boys & Girls Club (010211543) produces >= 10 metrics including "1,492 youth served" and "66,108 meals" (frozen fixture)
16. Full P20 extraction completes within $20 and 90 minutes (conservative bounds over estimates)

## Security and Data Handling

- **API key management:** DeepSeek key retrieved from SSM at runtime via `get_secret()`. Never logged, never stored in code or config files.
- **Document text sent to external API:** Report text is sent to DeepSeek's API for processing. These are publicly-filed nonprofit reports — no proprietary or sensitive data. DeepSeek's API terms state inputs are not used for training (verify at deployment time).
- **PII in stories:** Extracted stories contain names of individuals mentioned in public reports. Same PII posture as Spec 0050: no redaction at extraction time, PII review required before any external-facing system surfaces this data.
- **Input sanitization:** All database writes use parameterized queries via SQLAlchemy. No user input enters the API prompt.
- **Cost protection:** `--cost-limit` prevents runaway spending. Default $25 requires explicit override for larger runs.
- **Rate limiting:** DeepSeek API has built-in rate limits. The `--parallel` cap (max 20) keeps concurrency well within documented limits. Retry with backoff on 429 responses.
- **pdftotext resource limits:** `pdftotext` is run via `subprocess` with a 30-second timeout. PDFs that cause hangs or crashes are logged and skipped — they do not block the pipeline.
- **DELETE scope:** All DELETE operations are scoped to `(run_id, content_sha256)` — never bulk table deletes. This limits blast radius even if the application is compromised.
- **Data at rest:** RDS uses AES-256 encryption at rest (AWS managed keys). S3 bucket uses SSE-S3 encryption. Both were configured during initial infrastructure setup.

## Traps to Avoid

1. **Per-section extraction loses context.** Tested and rejected: 214 sections individually missed 2/4 competitor metrics. Always send the full document.
2. **Empty document hallucination.** DeepSeek fabricates stories when given no text. The < 100 char guard must be enforced before the API call, not after.
3. **pdftotext misses image pages.** Some impact reports are heavily visual. pdftotext extracts 0 chars from image-only pages. This is a known gap — the fix is Docling, not a change to this pipeline.
4. **`::jsonb` cast syntax.** SQLAlchemy interprets `::` as bind parameters. Use `CAST(:param AS jsonb)` instead. This has bitten us three times in this codebase.
5. **Combined prompt dilution.** The combined prompt may occasionally miss a metric that a standalone metric-only prompt would catch. Experiment showed 1 miss out of 16 in one test — acceptable tradeoff for halving cost.

## Consultation Summary

### Spec Review - Gemini

**Verdict**: APPROVE
**Confidence**: HIGH
No key issues.

### Spec Review - Codex

**Verdict**: REQUEST_CHANGES
**Confidence**: HIGH
9 findings:
1. JSON schema for metric/story objects underspecified → **Fixed**: Added explicit validation rules in extraction pipeline.
2. Sequence ownership not clarified → **Accepted**: BIGSERIAL auto-creates owned sequences; grants cover usage.
3. Uniqueness/idempotency rules missing → **Fixed**: Added per-document DELETE-before-INSERT scoped to (run_id, sha).
4. Run lifecycle underspecified → **Fixed**: Added running/completed/aborted/failed states with transition rules.
5. Resume semantics incomplete → **Fixed**: Added skipped_shas tracking in stats_json for empty docs.
6. Cost estimation formula missing → **Fixed**: Added doc_count × $0.004 formula and time estimate.
7. Docling/pdftotext precedence ambiguous → **Fixed**: Added strictly ordered text source precedence section.
8. Dashboard requirements too high-level → **Accepted**: Dashboard follows existing pipeline page patterns; details in plan.
9. Testing strategy missing → **Accepted**: Detailed testing strategy deferred to plan phase.

### Red Team - Gemini

**Verdict**: REQUEST_CHANGES
0 CRITICAL, 2 HIGH, 3 MEDIUM, 2 LOW.

1. **HIGH — DELETE permissions too broad**: research_app could wipe entire tables.
   - **Disposition**: Fixed. Scoped all DELETE operations to `(run_id, content_sha256)` — documented in spec and plan.

2. **HIGH — Input validation missing for CLI args**: Extreme values could cause resource exhaustion.
   - **Disposition**: Fixed. Added explicit validation regex and bounds for all arguments (run_tag, ntee, state, ein, batch-size, parallel, cost-limit).

3. **MEDIUM — PII review process undefined**: Stories contain names from public reports.
   - **Disposition**: Accepted. Same posture as Spec 0050 — PII review required before external-facing systems. Single-operator research DB.

4. **MEDIUM — Malformed PDFs could hang pdftotext**: DoS via crafted input.
   - **Disposition**: Fixed. Added 30-second subprocess timeout for pdftotext.

5. **MEDIUM — Data retention policy missing**: No deletion schedule for extracted data.
   - **Disposition**: Accepted. Single-operator system, data retention managed manually. Old runs can be deleted via `DELETE FROM llm_metrics WHERE run_id = X`.

6. **LOW — DeepSeek API terms not verified**: "verify at deployment time" is too late.
   - **Disposition**: Accepted. Public nonprofit reports only; DeepSeek terms reviewed informally. Formal verification at deployment.

7. **LOW — Encryption at rest not documented**: Platform-level concern.
   - **Disposition**: Fixed. Added explicit note confirming RDS AES-256 and S3 SSE-S3 encryption.

### Human Review (2026-05-26)

**APPROVED**. Added `metric_type` and `geo_impact` fields per operator request — freeform LLM-generated labels to be normalized after full corpus extraction.

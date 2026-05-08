# Plan 0035: Multi-Page Classification Context & Hardened Classifier

**Spec**: `locard/specs/0035-multipage-classification.md`
**Status**: Draft
**Phases**: 8 (sequenced by dependency)

## Context for the Builder

The classifier today sees only `first_page_text` — a single page, 4,096 chars max. A third of the corpus has < 200 chars (cover pages), and 1,965 IRS 990s are misclassified because the classifier has no metadata or rule-based pre-filter. This plan adds multi-page extraction, rule-based triage, metadata-augmented prompts, and a full corpus reclassification pipeline — all with persistent storage so iterating on prompts costs ~$24/run with no re-extraction.

**Key codebase context**:
- Existing V3 classifier: `lavandula/reports/classify.py` → `classify_first_page_v3()`
- Definition loader: `lavandula/nonprofits/definition_loader.py` → `ClassifierDefinition`
- Batch classifier (Anthropic CLI): `lavandula/reports/tools/classify_null.py`
- Batch classifier (OpenAI API): `lavandula/nonprofits/pipeline_classify.py`
- Classifier clients: `lavandula/reports/classifier_clients.py` (DeepSeek API + subscription CLIs)
- PDF extractor (sandbox): `lavandula/reports/sandbox/pdf_extractor.py` (first-page only)
- S3 archive: `lavandula/reports/s3_archive.py` (boto3, `lavandula-nonprofit-collaterals` bucket)
- DB access: `lavandula/common/db.py` → `make_app_engine()` (SQLAlchemy, RDS)
- Existing migrations: `lavandula/migrations/rds/` (latest: 013)
- Dashboard management commands: `lavandula/dashboard/pipeline/management/commands/`
- Definition file: `lavandula/nonprofits/definitions/corpus_reports.md`

---

## Phase 1: Database Schema (Migration 014)

**Goal**: Create the `classification_context`, `classification_runs`, and `classification_results` tables, plus `v3_*` columns on corpus.

**File**: `lavandula/migrations/rds/014_classification_context.sql`

```sql
-- classification_context: persistent multi-page extraction cache
CREATE TABLE lava_corpus.classification_context (
    content_sha256    TEXT PRIMARY KEY
        REFERENCES lava_corpus.corpus(content_sha256),
    pages_text        TEXT NOT NULL,
    pages_extracted   SMALLINT NOT NULL,
    total_pages       SMALLINT,
    extraction_method TEXT NOT NULL DEFAULT 'pypdf',
    extracted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    text_length       INT NOT NULL
);

CREATE INDEX idx_cc_text_length
    ON lava_corpus.classification_context(text_length);

-- classification_runs: run-level metadata for A/B comparison
CREATE TABLE lava_corpus.classification_runs (
    id              SERIAL PRIMARY KEY,
    run_tag         TEXT NOT NULL UNIQUE,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    config_json     JSONB,
    rules_snapshot  TEXT,
    stats_json      JSONB,
    notes           TEXT
);

-- classification_results: per-doc results, partitioned by run
-- Stores material_group and event_type alongside material_type so that
-- promotion can populate all canonical columns without re-deriving from
-- the taxonomy YAML (which may have changed between run and promote).
CREATE TABLE lava_corpus.classification_results (
    run_id          INT NOT NULL REFERENCES lava_corpus.classification_runs(id) ON DELETE CASCADE,
    content_sha256  TEXT NOT NULL REFERENCES lava_corpus.corpus(content_sha256),
    material_type   TEXT,
    material_group  TEXT,
    event_type      TEXT,
    confidence      REAL,
    reasoning       TEXT,
    classified_by   TEXT,
    PRIMARY KEY (run_id, content_sha256)
);

-- v3 columns on corpus (denormalized cache of latest promoted run)
ALTER TABLE lava_corpus.corpus ADD COLUMN IF NOT EXISTS v3_material_type TEXT;
ALTER TABLE lava_corpus.corpus ADD COLUMN IF NOT EXISTS v3_confidence REAL;
ALTER TABLE lava_corpus.corpus ADD COLUMN IF NOT EXISTS v3_reasoning TEXT;
ALTER TABLE lava_corpus.corpus ADD COLUMN IF NOT EXISTS v3_classified_at TIMESTAMPTZ;
ALTER TABLE lava_corpus.corpus ADD COLUMN IF NOT EXISTS v3_run_tag TEXT;
ALTER TABLE lava_corpus.corpus ADD COLUMN IF NOT EXISTS v3_classified_by TEXT;
```

**Acceptance criteria**: AC1, AC29

**Testing**: Manual — verify migration applies cleanly via PGAdmin. Builder should include a rollback SQL file (`014_classification_context_rollback.sql`) that drops the tables and columns.

---

## Phase 2: Multi-Page Extraction Engine

**Goal**: Extract pages 1–5 from S3-archived PDFs, store in `classification_context`.

### 2a. Extraction module

**New file**: `lavandula/reports/extraction.py`

Responsibilities:
- `extract_pages(pdf_bytes: bytes, max_pages: int = 5) -> ExtractionResult` — runs pypdf in a `multiprocessing.Process` with hardened resource limits. Returns structured result (pages_text, pages_extracted, total_pages, extraction_method).
- Page concatenation with `\n--- PAGE N ---\n` markers (spec §1a).
- 16,000 char cap on `pages_text` (spec §1a).
- No decryption — encrypted PDFs → `failed:encrypted`.
- Corrupt/timeout/OOM → appropriate `failed:*` method strings.
- Image-only PDFs (empty text) → `pypdf:empty`.

**Implementation notes**:
- Reuse the sandbox pattern from `sandbox/pdf_extractor.py` but with multi-page extraction instead of single-page.
- The `multiprocessing.Process` worker should set these resource limits before any pypdf work:
  1. `resource.RLIMIT_AS` = 1 GB (memory cap)
  2. `resource.RLIMIT_CPU` = 30 seconds (CPU time cap — defense against busy loops)
  3. `resource.RLIMIT_NOFILE` = 64 (prevent file-descriptor exhaustion)
  4. `resource.RLIMIT_NPROC` = 0 (prevent child from forking)
  5. `resource.RLIMIT_FSIZE` = 0 (prevent file writes)
  6. Strip credential-carrying env vars: clear `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `DATABASE_URL` from the child's environment
- Extraction steps:
  1. Create `PdfReader(io.BytesIO(pdf_bytes))`
  2. Extract `.extract_text()` for pages 0–4
  3. Concatenate with page markers
  4. Return result via a `multiprocessing.Queue`
- The parent process enforces the 30s wall-clock timeout via `process.join(30)` + `process.kill()`.
- **Parent-side size enforcement**: After `Queue.get()`, verify that `len(result.pages_text) <= 32_000` (2× the 16K cap, allowing margin). If oversized, treat as `failed:protocol_violation` and discard. This defends against a compromised child returning a memory-exhausting payload.

**Key constraint**: The extraction runs in a subprocess — no shared memory with the main process. Communicate via queue or pipe.

### 2b. Management command

**New file**: `lavandula/dashboard/pipeline/management/commands/extract_classification_context.py`

Django management command that:
1. Acquires advisory lock: `SELECT pg_advisory_lock(hashtext('extract-context'))`
2. Queries `corpus LEFT JOIN classification_context` for rows where context is NULL and `content_type = 'application/pdf'`
3. Creates a Job record (phase: `extract-context`) for dashboard visibility
4. Uses a **bounded producer-consumer design** with two executor tiers per AC7:
   - `ThreadPoolExecutor(max_workers=download_workers)` for S3 downloads (network-bound — benefits from connection reuse). Default `--download-workers 8`.
   - A bounded `queue.Queue(maxsize=extract_workers * 2)` between download and extraction.
   - Extraction consumer threads (count = `--extract-workers`, default 4) each pull from the queue and call `extract_pages()` which spawns a sandboxed `multiprocessing.Process` per PDF.
   - This ensures at most `extract_workers` extraction subprocesses run concurrently, bounding total memory to `extract_workers × 1 GB RLIMIT_AS` — safe on a 4 GB host at default settings.
5. For each PDF:
   a. Check `corpus.file_size` — if > 100 MB, **write a `classification_context` row** with `pages_text = ''`, `extraction_method = 'skipped:oversized'`, `text_length = 0`. This ensures re-runs skip the file (idempotent).
   b. Download bytes from `s3://lavandula-nonprofit-collaterals/pdfs/{sha256}.pdf`, enforcing a **100 MB streaming cap** on the download itself (abort + discard if Content-Length or streamed bytes exceed 100 MB). This defends against TOCTOU if `corpus.file_size` is stale or null.
   c. Verify SHA256 of downloaded bytes matches the key — reject + log on mismatch. **No `classification_context` row written** for mismatches (will be retried on next run).
   d. Run `extract_pages()` (spawns sandboxed subprocess internally)
   e. Bulk INSERT results into `classification_context` (ON CONFLICT DO NOTHING)
6. For missing S3 objects: log warning, skip. No row written — will be retried on next run.
7. Updates Job record with progress/stats
8. Releases advisory lock on exit

**CLI interface**:
```
python3 manage.py extract_classification_context
python3 manage.py extract_classification_context --limit 1000
python3 manage.py extract_classification_context --reextract
python3 manage.py extract_classification_context --workers 8
```

**S3 access**: Use `lavandula/reports/s3_archive.py` patterns — boto3 client from the existing config. The bucket name is `lavandula-nonprofit-collaterals`, prefix `pdfs/`.

**Acceptance criteria**: AC2, AC3, AC4, AC5, AC6, AC7, AC32

**Testing**:
- Unit: `test_extract_pages` with fixture PDFs (normal 10-page, 2-page, encrypted, corrupt, empty/image-only). Verify page markers, char cap, page count, extraction_method strings.
- Unit: SHA256 verification — pass mismatched bytes, verify rejection.
- Integration: Mock S3 + real DB → verify `classification_context` rows created with correct columns.
- Idempotency: Run twice, verify no duplicates (ON CONFLICT DO NOTHING).

---

## Phase 3: Rule-Based Pre-Filter

**Goal**: Deterministic classification for unambiguous documents.

### 3a. Rules YAML file

**New file**: `lavandula/nonprofits/definitions/prefilter_rules.yaml`

Copy the rules from spec §Phase 2 verbatim. Initial rules:
- `irs_990_text` (text_contains_any: "OMB No. 1545-0047", etc.)
- `irs_990_url` (url_matches regex)
- `irs_990_creator_plus_text` (compound: pdf_creator_any + text_contains_any)

### 3b. Rule engine

**New file**: `lavandula/nonprofits/prefilter.py`

`RuleEngine` class:
- `__init__(yaml_path: Path)` — loads rules via `yaml.safe_load`. Missing/unreadable file → raise at startup (fail closed). Validates all regexes at load time. Rejects regexes with nested quantifiers or that fail a 100ms test against a 5KB adversarial string. Records SHA256 of the YAML file.
- `evaluate(text, url, pdf_creator, page_count, file_size) → RuleMatch | None`
  - `RuleMatch` dataclass: `(material_type, confidence, reasoning, rule_name, rules_sha256)`
  - Applies rules in YAML order; first match wins.
  - `text_contains_any`: Aho-Corasick matching (use `ahocorasick` package or `pyahocorasick`). Input text: lowered, whitespace-normalized, apostrophe-normalized.
  - `url_matches`: `regex` package (not stdlib `re`) with 1-second timeout. URL input capped at 2,048 chars. **Note**: The spec § Rule Matching Semantics mentions `re.search` with `re.IGNORECASE`, but the spec § Security section requires the `regex` package with per-match timeout for ReDoS safety. The Security section supersedes — use `regex` exclusively. The primary defense against ReDoS is the `regex` package's timeout; the nested-quantifier check at load time is a secondary belt-and-suspenders measure. Builder should not rely solely on regex pattern validation.
  - `pdf_creator_any`: case-insensitive substring match on sanitized pdf_creator.
  - `text_not_contains`: negative guard — if any of these strings present, rule does NOT match (falls through).
  - Returns `None` when no rule matches.
- `rules_sha256` property — hex digest of the YAML file content.

**Suppression sampling** (spec §Phase 2): The reclassification pipeline (Phase 6) handles the 5% sampling — not the rule engine itself. The engine just returns matches; the caller decides whether to sample.

**Dependencies**: Add `pyahocorasick` and `regex` to requirements.

**Acceptance criteria**: AC8, AC9, AC10, AC11, AC12, AC13, AC33, AC34

**Testing**:
- Unit: Each condition type individually (text_contains_any, url_matches, pdf_creator_any, text_not_contains, compound).
- Unit: Case-insensitive, whitespace-normalized, apostrophe-normalized matching.
- Unit: Priority ordering (first match wins when multiple rules could match).
- Unit: No-match fallback returns None.
- Unit: YAML validation — invalid regex rejected at load time, missing required fields rejected, unknown condition types rejected.
- Unit: Regex timeout — crafted ReDoS pattern rejected at load time.
- Unit: URL cap at 2,048 chars.

---

## Phase 4: Metadata-Augmented Classifier Prompt

**Goal**: Enrich the V3 classifier prompt with multi-page text + metadata.

### 4a. Definition file update

**Modify**: `lavandula/nonprofits/definitions/corpus_reports.md`

Add `context_mode: multipage` to the YAML frontmatter:
```yaml
name: corpus_reports
version: 2
description: Classify nonprofit PDF documents by material type
source_taxonomy: collateral_taxonomy.yaml
context_mode: multipage
output_columns:
  - material_type
  - material_group
  - event_type
```

### 4b. Definition loader update

**Modify**: `lavandula/nonprofits/definition_loader.py`

- Parse `context_mode` from frontmatter (default: `single_page`). Validate: must be `single_page` or `multipage`.
- Add `context_mode` field to `ClassifierDefinition` dataclass.
- No other changes to the loader — the prompt assembly moves to the classifier.

### 4c. Augmented prompt builder

**Modify**: `lavandula/reports/classify.py`

Add a new function:
```python
def build_augmented_user_message(
    *,
    pages_text: str | None,
    first_page_text: str,
    url_path: str | None = None,
    page_count: int | None = None,
    file_size_bytes: int | None = None,
    pdf_creator: str | None = None,
    nonce: str | None = None,
) -> str:
```

This function:
1. Chooses text source: `pages_text` if available, else `first_page_text` (AC17 fallback).
2. Sanitizes document text: strip XML-like tag patterns (`<...>`, `</...>`), control chars, zero-width chars. Same sanitization applied to `pdf_creator` (it's attacker-controlled metadata with the same threat profile as document text).
3. Generates a per-request nonce (8 hex chars) if not provided.
4. Formats page markers with nonce: `--- PAGE 2 [x7f3a] ---`.
5. Builds metadata block — **only system-derived, non-attacker-controlled fields**:
   ```
   <document_metadata>
   Page count: 32
   File size: 4.2 MB
   </document_metadata>
   ```
6. Places **both** `pdf_creator` AND `url_path` inside `<untrusted_document>` because both are attacker-influenced. URL paths come from the crawled website (attacker controls their own URL structure). `pdf_creator` comes from PDF metadata (attacker controls their own PDFs). Only `page_count` and `file_size` are system-derived (from S3 object metadata / pypdf parsing).
7. URL path: extracted via `urllib.parse.urlparse`, scheme allowlist (http, https only), only `.path` component. Sanitized: strip angle brackets, control chars, percent-decode then re-encode to normalize. Capped at 2,048 chars. Non-http URLs → empty string.
8. `pdf_creator` sanitized: strip XML-like tags, angle brackets, control chars, newlines. Cap at 100 chars.
9. Returns the assembled user message string.

### 4d. V3 classifier integration

**Modify**: `lavandula/reports/classify.py` — `classify_first_page_v3()`

Add optional parameters:
```python
def classify_first_page_v3(
    first_page_text: str,
    *,
    client,
    definition,
    model=None,
    raise_on_error=True,
    # New params for augmented mode:
    pages_text: str | None = None,
    url_path: str | None = None,
    page_count: int | None = None,
    file_size_bytes: int | None = None,
    pdf_creator: str | None = None,
) -> ClassificationResult:
```

When `definition.context_mode == "multipage"` and any metadata param is provided, use `build_augmented_user_message()` instead of the current simple prompt. Otherwise, use the existing prompt (backwards compatible).

**Reasoning cap enforcement**: The 500-char `reasoning` cap (spec § Security) is already enforced in `classify.py`'s `_validate_tool_input_v2()` and the V3 classifier path. Verify this remains in place after modifications — do not remove or weaken the cap.

**Note on `pdf_creator` placement**: The spec §Phase 3 example prompt shows `PDF creator: Adobe InDesign 2024` inside `<document_metadata>`, but the spec § Security section explicitly states `pdf_creator` must go inside `<untrusted_document>` because it is attacker-controlled. **Follow the Security section** — place `pdf_creator` in the untrusted section. The example is illustrative, not normative.

**Acceptance criteria**: AC14, AC15, AC16, AC17, AC18

**Testing**:
- Unit: `build_augmented_user_message()` — verify metadata in `<document_metadata>`, text in `<untrusted_document>`, pdf_creator in untrusted section, nonce in page markers.
- Unit: URL path extraction — various URLs including edge cases (no path, query params, fragments, non-http scheme → empty).
- Unit: Fallback — when `pages_text` is None, uses `first_page_text`.
- Unit: Sanitization — XML-like tags stripped from document text.
- Unit: File size formatting (bytes → human-readable).
- Unit: Reasoning cap — verify output reasoning is ≤ 500 chars after augmented classification.
- Integration: `classify_first_page_v3()` with `context_mode: multipage` definition and mock client → verify augmented prompt sent.

---

## Phase 5: Reclassification Pipeline

**Goal**: Re-classify the full corpus using rules + augmented LLM prompt.

**New file**: `lavandula/dashboard/pipeline/management/commands/reclassify_corpus.py`

Django management command that orchestrates the full pipeline:

### Flow:
1. Parse args: `--run-tag` (required), `--sample N`, `--where "..."`, `--dry-run`, `--backend deepseek|haiku`, `--workers 4`, `--resume`
2. Acquire advisory lock: `SELECT pg_advisory_lock(hashtext('reclassify-' || :run_tag))`
3. Create `classification_runs` row with config snapshot (model, definition, rules YAML SHA256, parameters). Store full YAML content in `rules_snapshot`.
4. Load `RuleEngine` from `prefilter_rules.yaml`.
5. Load `ClassifierDefinition` (corpus_reports).
6. Query target docs:
   - Base: `SELECT c.content_sha256, c.first_page_text, c.source_url, c.file_size, c.pdf_creator, cc.pages_text, cc.pages_extracted, cc.total_pages FROM corpus c LEFT JOIN classification_context cc ON cc.content_sha256 = c.content_sha256`
   - `pdf_creator` comes from `corpus.pdf_creator` (populated at crawl time by `sandbox/pdf_extractor.py`). This is the source for both rule evaluation and prompt assembly.
   - Apply `--where` filter, `--sample` via `ORDER BY random() LIMIT N`
   - Apply `--resume` cursor from Job record
   - LEFT JOIN `classification_results` for this run_id to skip already-processed rows
7. **Dry run** path: if `--dry-run`, print counts and exit.

### Processing loop (batches of 500, keyset pagination on content_sha256):

For each document:

**Step A: Rule pre-filter**
- Call `rule_engine.evaluate(text=pages_text or first_page_text, url=source_url, pdf_creator=pdf_creator, page_count=total_pages, file_size=file_size)`
- If rule matches:
  - Write to `classification_results` with `classified_by = 'rule:{rule_name}@{sha256[:8]}'`
  - If `material_type == 'not_relevant'`: deterministic 5% sampling via `hashlib.sha256(f"{run_id}:{content_sha256}".encode()).digest()[0] < 13` (13/256 ≈ 5.1%). Send sampled docs to LLM anyway; log disagreements. Deterministic so re-runs produce the same sample for audit reproducibility.
  - Skip LLM for non-sampled rule matches.

**Step B: Insufficient text check**
- If both `pages_text` and `first_page_text` are empty or < 50 chars:
  - Write `material_type = 'other_collateral'`, `confidence = 0.1`, `classified_by = 'rule:insufficient_text'`
  - Skip LLM.

**Step C: LLM classification**
- Call `classify_first_page_v3()` with augmented params (pages_text, url_path, page_count, file_size_bytes, pdf_creator).
- Write to `classification_results` with `classified_by = 'llm:{model}'`.

**Important**: Phase 5 writes ONLY to `classification_results` and `classification_runs`. It does NOT update `v3_*` columns on corpus. The `v3_*` columns are a denormalized cache of the latest **promoted** run — populated exclusively by Phase 7 (`promote_classification_run`). This preserves the "run → compare → promote" workflow and prevents non-validated runs from corrupting the promotion cache.

**Step D: Checkpoint**
- After each batch of 500: update Job record with last cursor.
- On error: exponential backoff (2s, 4s, 8s, 16s). After 4 consecutive failures for same doc → mark as `llm:error`. After 20 consecutive failures across docs → halt, record cursor.

**Step E: Per-domain suppression audit** (spec § Security)
- Track per-source-domain counts of `not_relevant` rule matches.
- After the run completes, compute suppression rate per domain: `rule_not_relevant_count / total_docs_from_domain`.
- **Warn** (log + include in stats_json) for any domain where suppression rate > 50%.
- This catches adversarial or misconfigured rule patterns that suppress too many docs from a single source.

### Post-run:
- Update `classification_runs.finished_at` and `stats_json` (total, rule_matched, llm_classified, by_type counts, suppression_sample_disagreements, per_domain_suppression_warnings).
- Print summary including any domain suppression warnings.

**Acceptance criteria**: AC19, AC20, AC21, AC22, AC23, AC24, AC25, AC30, AC31, AC35, AC36

**Testing**:
- Unit: Rule-then-LLM ordering — mock rule engine + mock client, verify rules run first.
- Unit: `--dry-run` prints counts, writes nothing.
- Unit: `--sample N` limits to N rows.
- Unit: `--resume` reads cursor from prior Job, continues from there.
- Unit: Suppression sampling — mock random, verify 5% of rule-suppressed docs go to LLM.
- Unit: Insufficient text handling — docs with < 50 chars classified without LLM.
- Unit: Exponential backoff — mock client that fails N times, verify retry delays.
- Unit: 20 consecutive failures → halt with cursor recorded.
- Unit: Per-domain suppression warning — fixture with 10 docs from same domain, 6 rule-suppressed → warn (60% > 50%).
- Integration: Small fixture corpus (10 docs: 3 990s, 2 thin text, 5 normal) → verify correct routing through rules vs LLM.
- Integration: Full pipeline smoke test — 10-doc corpus through rule→LLM routing, suppression sampling, checkpoint, resume.
- **Validation fixtures**: Create 20 hand-labeled test PDFs spanning: 990, audited financials, annual report, impact report, cover-page-only, newsletter, not_relevant. Store in `lavandula/reports/tests/fixtures/classification/`. Used for regression testing on prompt changes per spec § Testing Strategy.

---

## Phase 6: Comparison & Validation Command

**Goal**: Compare old vs new classifications.

**New file**: `lavandula/dashboard/pipeline/management/commands/compare_classifications.py`

Django management command:
```
python3 manage.py compare_classifications --run-tag v3.1
python3 manage.py compare_classifications --run-tag v3.1 --show-reasoning
```

Output:
1. **Summary**: total docs, rule-classified count, LLM-classified count.
2. **Migration matrix**: `old_material_type → new_material_type` with counts, sorted by count descending. Only show transitions where count > 0.
3. **Confidence comparison**: avg confidence old vs new, count of docs with confidence < 0.8 old vs new.
4. **Breakdown by classified_by**: counts per `rule:*` and `llm:*` category.
5. **Suppression audit**: disagreement count and rate from suppression sampling.

Data source: JOIN `corpus` (canonical columns) with `classification_results` (for the given run_tag, via `classification_runs.id`).

**Acceptance criteria**: AC26, AC27, AC28

**Testing**:
- Unit: Two fixture runs with known differences → verify migration matrix output matches expected.
- Unit: `--show-reasoning` includes reasoning column.

---

## Phase 7: Promotion Command

**Goal**: Promote a validated run's results to canonical corpus columns.

**New file**: `lavandula/dashboard/pipeline/management/commands/promote_classification_run.py`

```
python3 manage.py promote_classification_run --run-tag v3.1 --confirm
```

1. Requires `--confirm` flag (safety gate).
2. Reads `classification_results` for the run (which now includes `material_type`, `material_group`, `event_type`, `confidence`, `reasoning`, `classified_by`).
3. Updates `v3_*` columns on corpus with results.
4. Updates canonical columns from `classification_results`:
   - `material_type` ← `cr.material_type`
   - `material_group` ← `cr.material_group` (stored at classification time, not re-derived)
   - `event_type` ← `cr.event_type`
   - `classification` ← `material_type_to_legacy(cr.material_type)` (derived at promotion time for backwards compat)
   - `classification_confidence` ← `cr.confidence`
   - `reasoning` ← `cr.reasoning`
   - `classifier_model` ← extracted from `cr.classified_by` (e.g., `llm:deepseek-v4-flash` → `deepseek-v4-flash`)
5. Logs to `classification_runs.notes`: "Promoted by {operator}@{hostname} (uid={uid}) at {timestamp}". Operator identity obtained via `getpass.getuser()`, hostname via `socket.gethostname()`, uid via `os.getuid()`. All three are logged for audit. **Trust assumption**: this is an audit log, not an authentication boundary — anyone with shell access can promote. Per project context (single-operator DB, ronp only), this is acceptable. The `--confirm` flag provides intent verification, not identity verification.

**Testing**: Integration test with fixture run → verify canonical columns updated.

---

## Phase 8: Dashboard Integration

**Goal**: Wire extraction and reclassification into the dashboard orchestrator.

### 8a. Dashboard forms

**Modify**: `lavandula/dashboard/pipeline/forms.py`

Add new forms:
- `ExtractContextForm`: limit (int), reextract (bool), workers (int, default 4)
- `ReclassifyCorpusForm`: run_tag (str, required), sample (int), backend (choice: deepseek/haiku), workers (int), dry_run (bool)

### 8b. Orchestrator stage registration

**Modify**: `lavandula/dashboard/pipeline/stages.py` and `orchestrator.py`

Register two new stages:
- `extract_context` → runs `extract_classification_context` management command
- `reclassify_corpus` → runs `reclassify_corpus` management command

### 8c. Template

**Modify**: Add a "Classifier v3" section to the classifier page (or a new page) with:
- Extract Context form
- Reclassify Corpus form
- Link to comparison results

**Testing**: Manual — start dev server, verify forms render, submit test runs.

---

## Dependency Graph

```
Phase 1 (schema)
    ↓
Phase 2 (extraction) ──→ Phase 5 (reclassification) ──→ Phase 6 (comparison)
    ↓                          ↓                              ↓
Phase 3 (rules) ──────→ Phase 5                         Phase 7 (promotion)
    ↓
Phase 4 (augmented prompt) → Phase 5
                                                         Phase 8 (dashboard)
```

Phases 2, 3, and 4 are independent of each other and could be parallelized. Phase 5 depends on all three. Phases 6 and 7 depend on Phase 5. Phase 8 can start after Phase 5.

---

## New Dependencies

| Package | Purpose | Phase | Security |
|---------|---------|-------|----------|
| `pyahocorasick` | Multi-pattern text matching for rule engine | 3 | C extension; pin version, monitor for CVEs |
| `regex` | Timeout-capable regex for URL matching | 3 | Pin to ≥ 2024 release for timeout reliability |
| `pypdf` (existing) | PDF text extraction in sandbox | 2 | **Security-critical**: pin to current known-good version in `requirements.txt` with a comment. Historical CVEs in PyPDF2 family (CVE-2023-36810, CVE-2022-24859). The sandbox RLIMIT mitigates RCE risk, but version pinning prevents regression. Check for open CVEs at pin time. |

All new packages should be pinned to specific versions in `requirements.txt`. Builder should verify no known CVEs at pin time and document the version choice.

---

## File Inventory

### New files (10):
1. `lavandula/migrations/rds/014_classification_context.sql`
2. `lavandula/migrations/rds/014_classification_context_rollback.sql`
3. `lavandula/reports/extraction.py`
4. `lavandula/nonprofits/prefilter.py`
5. `lavandula/nonprofits/definitions/prefilter_rules.yaml`
6. `lavandula/dashboard/pipeline/management/commands/extract_classification_context.py`
7. `lavandula/dashboard/pipeline/management/commands/reclassify_corpus.py`
8. `lavandula/dashboard/pipeline/management/commands/compare_classifications.py`
9. `lavandula/dashboard/pipeline/management/commands/promote_classification_run.py`
10. Test files for each phase

### Modified files (5):
1. `lavandula/nonprofits/definitions/corpus_reports.md` (add `context_mode` frontmatter)
2. `lavandula/nonprofits/definition_loader.py` (parse `context_mode`)
3. `lavandula/reports/classify.py` (add `build_augmented_user_message`, extend `classify_first_page_v3`)
4. `lavandula/dashboard/pipeline/forms.py` (add new forms)
5. `lavandula/dashboard/pipeline/stages.py` + `orchestrator.py` (register new stages)

---

## Security Checklist

Per spec § Security + red team findings, the builder must verify:

**Sandbox hardening (Phase 2)**:
- [ ] pypdf runs in `multiprocessing.Process` with 30s wall-clock timeout
- [ ] Child sets RLIMIT_AS (1 GB), RLIMIT_CPU (30s), RLIMIT_NOFILE (64), RLIMIT_NPROC (0), RLIMIT_FSIZE (0)
- [ ] Child strips credential env vars (AWS_*, DATABASE_URL)
- [ ] Parent enforces 32K char cap on queue messages from child (defense against child memory attacks)
- [ ] No decryption attempted on encrypted PDFs
- [ ] pypdf pinned to known-good version in requirements.txt

**S3 download (Phase 2)**:
- [ ] SHA256 verification after S3 download
- [ ] 100 MB streaming cap on downloads (TOCTOU defense against stale file_size)
- [ ] Extraction concurrency bounded (`--extract-workers` × 1 GB RLIMIT fits in host memory)

**Rule engine (Phase 3)**:
- [ ] `yaml.safe_load` exclusively for rules YAML (AC34)
- [ ] `regex` package (not `re`) for URL matching with 1s timeout (primary ReDoS defense)
- [ ] Nested quantifier rejection as secondary defense
- [ ] URL input capped at 2,048 chars before regex

**Prompt assembly (Phase 4)**:
- [ ] `pdf_creator` AND `url_path` in `<untrusted_document>`, not `<document_metadata>` (both attacker-controlled)
- [ ] `pdf_creator` sanitized: strip XML-like tags, angle brackets, control chars, newlines, cap 100 chars
- [ ] URL path via `urlparse` with scheme allowlist (http, https only)
- [ ] Page boundary nonce to distinguish from attacker-planted markers
- [ ] Reasoning capped at 500 chars (enforce at write time to `classification_results`, not just validator)

**Pipeline (Phase 5)**:
- [ ] Error logs never include document text excerpts (AC35)
- [ ] Advisory locks on both long-running commands
- [ ] 5% deterministic suppression sampling with disagreement logging
- [ ] Per-domain suppression rate > 50% triggers warning
- [ ] `compare_classifications --show-reasoning` strips control chars from reasoning display

**Promotion (Phase 7)**:
- [ ] `promote_classification_run` requires `--confirm` flag
- [ ] Operator identity logged: getpass.getuser() + socket.gethostname() + os.getuid()

---

## Traps to Avoid (from spec)

1. **Don't re-extract from S3 on every classifier run.** `classification_context` is a persistent cache — the reclassification command reads from it, never re-downloads PDFs.
2. **Don't overwrite existing classifications.** Write to `classification_results` only during reclassification. The `v3_*` columns and canonical columns are untouched until promotion (Phase 7). This preserves the "run → compare → promote" workflow.
3. **Don't send metadata inside `<untrusted_document>` tags.** `<document_metadata>` is separate. Only `pdf_creator` goes in the untrusted section.
4. **Don't skip the pre-filter.** Every reclassification run applies rules first, even if the user only wants "just the LLM".
5. **Don't hardcode rules in Python.** Rules live in YAML.

---

## Estimated Effort

| Phase | Estimated effort | Risk |
|-------|-----------------|------|
| 1. Schema | Low | Low — straightforward DDL |
| 2. Extraction | Medium | Medium — subprocess sandboxing, S3 I/O |
| 3. Rules | Medium | Low — well-defined; Aho-Corasick is standard |
| 4. Augmented prompt | Low | Low — mostly string assembly |
| 5. Reclassification | High | Medium — orchestration, backoff, checkpointing |
| 6. Comparison | Low | Low — read-only queries |
| 7. Promotion | Low | Low — simple UPDATE |
| 8. Dashboard | Low | Low — follows existing patterns |

Total: ~1,200–1,500 lines of production code + ~800 lines of tests.

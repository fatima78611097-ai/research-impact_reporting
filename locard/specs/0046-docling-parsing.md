# Spec 0046: Docling Full-Document Parsing

**Status:** Draft
**Author:** Architect
**Created:** 2026-05-16
**Dependencies:** None (builds on existing S3 corpus)

## Problem Statement

The pipeline's Parse stage (between Classify and Extract in the PROJECT_SEED) does not exist. Documents in the corpus are stored as raw PDFs in S3 with only pypdf-extracted text available (`first_page_text` in corpus table, `pages_text` in classification_context — both unstructured, the latter capped at ~5 pages).

Layer 2 vocabulary extraction requires full-document structured text — section boundaries, heading hierarchy, tables preserved as structured data, and reading order resolved. Without this, NLP term extraction methods (noun-phrase extraction, TF-IDF, C-value) cannot operate reliably, and the resulting vocabulary would be incomplete (missing 80%+ of document content).

Docling (MIT-licensed, IBM Research) provides GPU-accelerated structured document parsing that produces hierarchical section trees, table DataFrames, figure identification, and page-level provenance. This spec defines the infrastructure and pipeline to parse the full corpus (~186K PDFs, ~580 GiB) into structured text stored in PostgreSQL.

## Goals

1. **Parse all corpus PDFs** into structured text with section hierarchy, tables, and page provenance
2. **Store results in PostgreSQL** in a dedicated schema (`lava_parse`) for downstream consumption
3. **Run on GPU spot instance** (G6.2xlarge with L4 GPU) for cost-efficient batch processing
4. **Produce section-level chunks** with heading context suitable for NLP term extraction and embedding
5. **Operate idempotently** — re-running on already-parsed documents is a no-op
6. **Prioritize by classification** — parse annual/impact reports first (Layer 2 priority), then remaining corpus

## Non-Goals

- Embedding generation (that's a separate pipeline stage)
- Vocabulary extraction (downstream of this spec)
- OCR for scanned documents (defer — most corpus PDFs are digital; handle OCR subset later)
- Replacing `pages_text` or `first_page_text` in existing tables (additive, not destructive)
- Dashboard UI for browsing parsed content (future spec)
- Real-time parsing (this is batch infrastructure)

## Technical Context

### Infrastructure

- **GPU instance:** G6.2xlarge (1× NVIDIA L4, 24 GiB VRAM, 8 vCPU, 32 GiB RAM) — spot pricing ~$0.60/hr in us-east-1 (checked 2026-05-16, range $0.57–0.68)
- **Source:** S3 `lavandula-nonprofit-collaterals/pdfs/{sha256}.pdf` (186,051 objects, 579.7 GiB)
- **Target:** RDS PostgreSQL (`lava_prod1` on `lava-1.czahqlvmtyh8.us-east-1.rds.amazonaws.com`)
- **Orchestration host:** cloud2 (t3.large) — starts/stops spot instance, monitors progress
- **Network:** Same VPC (`vpc-03903867c28f9e908`), same subnet (us-east-1a) — GPU instance can reach RDS and S3 directly

### Docling Capabilities (v2.93)

- **Throughput:** ~0.49 sec/page on L4 GPU (digital PDFs without OCR)
- **Output:** `DoclingDocument` — hierarchical tree of text items, tables, pictures with bounding boxes and page numbers
- **Chunking:** `HierarchicalChunker` produces section-aware chunks with heading context metadata
- **Tables:** Full row/column/span structure, exportable as DataFrame
- **Export:** JSON (lossless), Markdown, HTML
- **License:** MIT

### Corpus Profile

| Metric | Value |
|--------|-------|
| Total PDFs | 186,051 |
| Total size | 579.7 GiB |
| Avg file size | ~3.2 MiB |
| Estimated avg pages | ~30 (based on file size distribution) |
| Estimated total pages | ~5.6M |
| Digital PDFs (no OCR needed) | ~90%+ (crawled from web, not scanned) |

### Cost Estimate

- **Pages:** ~5.6M pages × 0.49 sec/page = ~2.7M seconds = ~760 GPU-hours
- **Spot rate:** G6.2xlarge at ~$0.60/hr spot = **~$456** for full corpus
- **Priority batch (annual/impact, ~30K docs):** ~900K pages × 0.49s = ~125 GPU-hours = **~$75**
- **Storage:** Structured text averages 2-3× raw text size. 186K docs × ~100 KiB structured output = ~18 GiB in RDS (within auto-scale limits)

### Priority Order

1. **Annual reports + impact reports** (~30K docs classified as `annual` or `impact`) — needed for Layer 2 vocabulary extraction
2. **Program descriptions, newsletters** — useful for multi-register analysis
3. **Remaining corpus** — completeness, but lower priority

## Technical Implementation

### Schema: `lava_parse`

```sql
CREATE SCHEMA IF NOT EXISTS lava_parse;

-- One row per parsed document
CREATE TABLE lava_parse.documents (
    content_sha256 TEXT PRIMARY KEY,       -- FK reference to corpus (not enforced)
    source_org_ein TEXT NOT NULL,          -- denormalized for query convenience
    parse_version TEXT NOT NULL,           -- Docling version string (e.g., "docling-2.93.0")
    parsed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    page_count INT NOT NULL,
    section_count INT NOT NULL,
    table_count INT NOT NULL,
    figure_count INT NOT NULL,
    total_text_chars INT NOT NULL,         -- total character count across all sections
    parse_duration_ms INT,                 -- how long parsing took
    error TEXT,                            -- NULL if successful; sanitized error class + truncated message (max 500 chars, no stack traces or internal paths)
    metadata_json JSONB                    -- filtered document metadata (title, year only — no internal editor names, hidden comments, or private codes)
);

-- One row per section/chunk (HierarchicalChunker output)
CREATE TABLE lava_parse.sections (
    id BIGSERIAL PRIMARY KEY,
    content_sha256 TEXT NOT NULL,          -- parent document
    section_index INT NOT NULL,            -- position in document (0-based)
    heading TEXT,                          -- section heading (NULL for untitled sections)
    heading_level INT,                     -- heading depth (1=H1, 2=H2, etc.)
    body_text TEXT NOT NULL,               -- section text content
    char_count INT NOT NULL,
    page_start INT,                        -- first page this section appears on
    page_end INT,                          -- last page this section appears on
    parent_headings TEXT[],                -- ancestor heading chain for context (e.g., ['Annual Report', 'Programs', 'Youth Services'])
    UNIQUE(content_sha256, section_index)
);

-- One row per table found in document
CREATE TABLE lava_parse.tables (
    id BIGSERIAL PRIMARY KEY,
    content_sha256 TEXT NOT NULL,
    table_index INT NOT NULL,             -- position among tables in this doc
    section_id BIGINT REFERENCES lava_parse.sections(id) ON DELETE CASCADE,  -- which section contains this table (NULL if before first heading)
    page_number INT,
    caption TEXT,                          -- table caption if detected
    row_count INT NOT NULL,
    col_count INT NOT NULL,
    data_json JSONB NOT NULL,             -- structured table data (array of row arrays)
    markdown TEXT,                         -- markdown rendering for readability
    UNIQUE(content_sha256, table_index)
);

-- Parse run tracking (batch management)
CREATE TABLE lava_parse.parse_runs (
    id SERIAL PRIMARY KEY,
    run_tag TEXT NOT NULL UNIQUE,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    config_json JSONB NOT NULL,           -- batch config (priority filter, instance type, workers, etc.)
    stats_json JSONB,                     -- final stats (total, succeeded, failed, duration, cost)
    instance_id TEXT                      -- EC2 instance ID used for this run
);

-- Indexes
CREATE INDEX idx_sections_sha ON lava_parse.sections(content_sha256);
CREATE INDEX idx_sections_heading ON lava_parse.sections(heading) WHERE heading IS NOT NULL;
CREATE INDEX idx_tables_sha ON lava_parse.tables(content_sha256);
CREATE INDEX idx_documents_org ON lava_parse.documents(source_org_ein);
CREATE INDEX idx_documents_parsed_at ON lava_parse.documents(parsed_at);

-- Grants
GRANT USAGE ON SCHEMA lava_parse TO research_app;
GRANT ALL ON ALL TABLES IN SCHEMA lava_parse TO research_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA lava_parse TO research_app;
```

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ cloud2 (t3.large) — Orchestrator                                │
│                                                                 │
│  manage.py parse_documents <run_tag> [options]                  │
│    ├── Launches G6 spot instance (if not running)               │
│    ├── SSHes into GPU instance, starts worker(s)                │
│    ├── Monitors progress via parse_runs.stats_json              │
│    └── Terminates spot instance when batch completes            │
│                                                                 │
└──────────────────────────────┬──────────────────────────────────┘
                               │ SSH / systemd
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ G6.2xlarge (spot) — GPU Worker                                  │
│                                                                 │
│  docling_worker.py                                              │
│    ├── Pulls batch of SHA256s from RDS (unparsed docs)          │
│    ├── Downloads PDFs from S3                                   │
│    ├── Runs Docling DocumentConverter                           │
│    ├── Runs HierarchicalChunker                                 │
│    ├── Writes results to lava_parse.documents/sections/tables   │
│    └── Updates parse_runs.stats_json with progress              │
│                                                                 │
│  Environment: Python 3.11+, CUDA 12.x, docling, psycopg2       │
│  IAM: Same role as cloud2 (S3 read, RDS connect)               │
└─────────────────────────────────────────────────────────────────┘
```

### Worker Design

The GPU worker is a standalone Python script (not a Django management command) that runs directly on the GPU instance. It:

1. **Connects to RDS** via IAM auth (same pattern as cloud2)
2. **Fetches work batch** via atomic claim:
   ```sql
   -- Work queue uses advisory locks for claim coordination.
   -- Single-worker design (one GPU instance at a time), but safe against
   -- overlapping restarts via SELECT ... FOR UPDATE SKIP LOCKED on a
   -- lightweight claim table.
   
   -- Step 1: Find eligible documents
   SELECT c.content_sha256, c.source_org_ein
   FROM lava_corpus.corpus c
   WHERE c.content_sha256 NOT IN (SELECT content_sha256 FROM lava_parse.documents)
     AND c.classification IN ('annual', 'impact')  -- priority filter (configurable)
   ORDER BY c.source_org_ein, c.content_sha256
   LIMIT 500
   ```
   
   **Concurrency safety:** The system runs a single worker at a time (one GPU instance). The orchestrator holds a `pg_advisory_lock(hashtext('docling-parse'))` for the duration of the run to prevent overlapping launches. If a second orchestrator attempt sees the lock held, it aborts with an error message. This is simpler than a claim table and sufficient for single-worker operation.
   
   **Advisory lock lifecycle:** PostgreSQL session-level advisory locks are automatically released when the session terminates (graceful or crash). If cloud2 crashes, the PostgreSQL connection closes and the lock is freed. No manual intervention needed. If a lock appears stuck (shouldn't happen), operator can identify the session via `pg_locks` and terminate it with `pg_terminate_backend(pid)`.
3. **Downloads PDFs from S3** in parallel (prefetch next batch while processing current)
4. **Parses with Docling:**
   ```python
   from docling.document_converter import DocumentConverter
   from docling.chunking import HierarchicalChunker

   converter = DocumentConverter()
   result = converter.convert(pdf_path)
   doc = result.document

   chunker = HierarchicalChunker()
   chunks = list(chunker.chunk(doc))
   ```
5. **Extracts tables:** from `doc.tables` — each table yields structured row/col data. **Section linkage rule:** Each Docling table has a `prov` (provenance) field with page number and bounding box. The table is assigned to the section whose `page_start <= table.page <= page_end` and whose position in the document is closest preceding the table's position. If no section matches (e.g., table appears before any heading), `section_id = NULL`.
6. **Writes to RDS:** batch INSERT into `documents`, `sections`, `tables`
7. **Repeats** until no more unparsed documents match the priority filter

### Orchestrator Command

```
Usage:
  manage.py parse_documents <run_tag> [options]

Options:
  --priority TEXT       Classification filter (default: 'annual,impact')
  --instance-type TEXT  EC2 instance type (default: 'g6.2xlarge')
  --spot               Use spot instance (default: true)
  --max-hours INT      Maximum runtime before auto-terminate (default: 12)
  --batch-size INT     Documents per work-queue fetch (default: 500)
  --dry-run            Show eligible count and cost estimate
  --status             Show progress of current/recent run
  --terminate          Terminate the GPU instance for this run
  --retry-errors       Re-queue previously failed documents (deletes error rows first)
  --reparse            Reparse all docs; use with --min-version to target old parses
  --min-version TEXT   Only reparse docs parsed by versions older than this
```

The orchestrator:
1. Creates `parse_runs` record
2. Launches (or reuses) GPU spot instance via EC2 API
3. Bootstraps the instance (install Docling, copy worker script, configure IAM)
4. Starts the worker process via SSH/systemd
5. Polls `parse_runs.stats_json` for progress
6. Terminates instance when work completes or `--max-hours` exceeded

### Spot Instance Management

- **Launch template:** Pre-configured with AMI (Ubuntu 22.04 + NVIDIA drivers + CUDA), security group (`internal-hosts`), IAM instance profile, and user-data bootstrap script
- **Interruption handling:** Worker commits progress after every batch (500 docs). If spot instance is reclaimed, orchestrator detects termination and can relaunch — already-parsed docs are skipped (idempotent)
- **Cost control:** `--max-hours` terminates after N hours regardless of completion state. Default 12 hours = ~$7.20 spot cost, parses ~88K pages

### Idempotency & Retry Semantics

**Document states** (determined by `lava_parse.documents` row):
- **No row:** Eligible for parsing
- **Row with `error IS NULL`:** Successfully parsed. Skipped unless `--reparse` flag with newer `parse_version`.
- **Row with `error IS NOT NULL`:** Failed. Eligible for retry via `--retry-errors` flag.

**Failure classes:**
- **Transient** (network timeout, S3 throttle, RDS connection loss): NOT recorded as document error. Worker retries 3× with backoff. If still failing, worker exits — orchestrator relaunches.
- **Permanent** (corrupt PDF, Docling crash, empty output): recorded in `documents.error`. Skipped on normal re-runs. Retryable via `--retry-errors` which DELETEs the error row + its sections/tables, then requeues.

**Parse-version upgrades:** When Docling is upgraded, operator can run with `--reparse --min-version <old_version>` to reparse documents parsed by an older version. This DELETEs the existing data (tables by SHA, then sections by SHA, then document row) and requeues for parsing. Only used for major Docling upgrades, not routine runs.

**Retry/reparse deletion order:** Since `sections` has no FK to `documents` (TEXT reference only), cascading deletion is handled in application code:
```sql
DELETE FROM lava_parse.tables WHERE content_sha256 = :sha;
DELETE FROM lava_parse.sections WHERE content_sha256 = :sha;
DELETE FROM lava_parse.documents WHERE content_sha256 = :sha;
```

**S3 immutability assumption:** PDFs in S3 are never modified after upload (content-addressed by SHA256). If a PDF were replaced (impossible given SHA addressing), the parse would be stale — but this cannot happen by design.

**Transaction model:** Each document is written atomically within a single transaction:
```
BEGIN;
  INSERT INTO documents (...) VALUES (...);
  INSERT INTO sections (...) VALUES (...), (...), ...;  -- all sections for this doc
  INSERT INTO tables (...) VALUES (...), ...;           -- all tables for this doc
COMMIT;
```
If the worker is killed mid-document, that document's transaction is rolled back. The document has no row in `documents` and will be picked up on next run. Progress is committed per-document (not per-batch of 500 — the batch size controls how many SHAs are fetched from the work queue at once, not the commit boundary).

### Error Handling

- **PDF download failure:** Transient — retry 3×. If still failing, record in `documents.error`, continue with next doc.
- **Docling parse failure:** Permanent — catch exception, record in `documents.error`, continue with next doc.
- **Empty/near-empty output:** If Docling produces 0 sections and 0 text, treat as permanent failure. Record `documents.error = 'empty_parse'` with `section_count=0, total_text_chars=0`. The document likely has no extractable text (image-only PDF without OCR, or a corrupt file).
- **RDS connection loss:** Transient — retry with exponential backoff (3 attempts). If persistent, worker exits cleanly — orchestrator can relaunch.
- **Spot interruption:** Worker gets 2-minute warning via EC2 metadata. Finish current document transaction, exit. Orchestrator relaunches.
- **Memory pressure:** If a PDF is extremely large (>500 pages), process it solo with reduced batch prefetch.

### Bootstrap / AMI Strategy

**Option A (preferred): Pre-baked AMI**
- Build AMI once with: Ubuntu 22.04, NVIDIA drivers, CUDA 12.x, Python 3.11, Docling + dependencies, psycopg2, boto3
- Store AMI ID in SSM parameter
- Launch template references this AMI
- Pro: Instance ready to parse in <60 seconds after launch
- Con: AMI must be rebuilt when Docling version changes

**Option B: User-data bootstrap**
- Base Ubuntu GPU AMI + user-data script that installs everything
- Pro: Always gets latest versions
- Con: 5-10 minute startup time, version drift risk

Recommend Option A for production. Use Option B only during initial development/testing.

## Acceptance Criteria

### Schema
- AC1: `lava_parse` schema exists with tables: `documents`, `sections`, `tables`, `parse_runs`
- AC2: No foreign keys reference tables outside `lava_parse` (cross-schema refs are TEXT only). Intra-schema FKs (e.g., `tables.section_id → sections.id`) are allowed.
- AC3: Schema can be dropped entirely without affecting Layer 1

### Worker
- AC4: Worker parses a PDF via Docling and produces sections with heading hierarchy
- AC5: Worker extracts tables with row/column structure
- AC6: Worker writes results to `lava_parse.documents`, `sections`, and `tables`
- AC7: Worker skips already-parsed documents (idempotent)
- AC8: Worker handles PDF parse failures gracefully (error recorded, processing continues)
- AC9: Worker commits progress per-batch (500 docs) — recoverable from interruption
- AC10: Worker connects to RDS via IAM auth
- AC11: Worker downloads PDFs from S3 with prefetch parallelism

### Orchestrator
- AC12: `parse_documents --dry-run` shows eligible count and cost estimate
- AC13: `parse_documents --status` shows current progress
- AC14: `parse_documents --terminate` stops the GPU instance
- AC15: Orchestrator creates `parse_runs` record with config and updates stats
- AC16: Orchestrator respects `--max-hours` timeout
- AC17: Orchestrator handles spot interruption (detects termination, can relaunch)

### Output Quality
- AC18: Sections include heading text and heading level
- AC19: Sections include `parent_headings` array (full ancestor chain)
- AC20: Sections include page_start and page_end
- AC21: Tables include structured data (rows × cols) as JSONB
- AC22: Tables are linked to their containing section
- AC23: Document record includes page_count, section_count, table_count, total_text_chars
- AC24: Parse version recorded (Docling version string)

### Infrastructure
- AC25: GPU instance launches in same VPC/subnet as cloud2
- AC26: GPU instance uses IAM role with S3 read + RDS connect permissions
- AC27: Spot instance terminates automatically when work completes or timeout hit
- AC28: AMI or bootstrap installs Docling with GPU acceleration

### Priority Filtering
- AC29: Default priority parses annual/impact reports first
- AC30: Priority filter is configurable via `--priority` flag
- AC31: Once priority batch completes, can re-run with broader filter for remaining corpus

## Security Considerations

- **IAM:** GPU instance gets a dedicated instance profile (`docling_worker`) with least-privilege permissions:
  - `s3:GetObject` on `arn:aws:s3:::lavandula-nonprofit-collaterals/pdfs/*` (read PDFs only)
  - `rds-db:connect` for `docling_writer` user on `db-NAMZ7DUPILQKINJANPKHMXEXDU` (dedicated DB user, not `research_app`)
  - `ssm:GetParameter` on `/cloud2.lavandulagroup.com/rds-*` (connection config only)
  - No S3 write, no EC2 describe, no IAM access
- **RDS privilege separation:** Create a dedicated PostgreSQL user `docling_writer` with permissions ONLY on `lava_parse` schema:
  ```sql
  CREATE USER docling_writer;
  GRANT rds_iam TO docling_writer;
  GRANT USAGE ON SCHEMA lava_parse TO docling_writer;
  GRANT ALL ON ALL TABLES IN SCHEMA lava_parse TO docling_writer;
  GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA lava_parse TO docling_writer;
  -- READ-ONLY on corpus (for work-queue query):
  GRANT USAGE ON SCHEMA lava_corpus TO docling_writer;
  GRANT SELECT ON lava_corpus.corpus TO docling_writer;
  -- No access to any other schema
  ```
  This limits blast radius: a compromised GPU instance can only write to `lava_parse` and read the corpus work queue.
- **Network:** GPU instance in same VPC/subnet (`subnet-0e2008e48d602e945`, us-east-1a) — no public IP needed. RDS security group already allows `internal-hosts`.
- **SSH:** Orchestrator connects to GPU instance via **EC2 Instance Connect** (push ephemeral key, no persistent SSH keys stored). Tailscale is not installed on the GPU instance — it's ephemeral and doesn't need mesh membership.
- **Data in transit:** S3 downloads over HTTPS. RDS connection uses TLS 1.2+ (IAM auth requires it). No sensitive data leaves the VPC.
- **Data at rest:** RDS encrypted (AES-256, aws/rds key). Parsed text is derived from publicly-available PDFs — same sensitivity as existing `pages_text`.
- **Spot termination:** No data loss — all results committed to RDS per-batch. Temporary PDF files on instance disk are ephemeral.
- **SQL injection:** All database writes use parameterized queries (psycopg2 `execute_values`). Content from parsed documents is NEVER interpolated into SQL strings.
- **Input validation:** SHA256 values from the work queue are validated against `^[a-f0-9]{64}$` before use in S3 key construction. No user-supplied input reaches the worker.

## Testing Requirements

- Unit tests for work-queue query logic (priority filter, exclusion of already-parsed docs)
- Unit tests for section extraction (heading hierarchy, parent_headings construction)
- Unit tests for table extraction (structured data format, section linkage)
- Unit tests for error handling (corrupt PDF, empty PDF, oversized PDF)
- Integration test: parse a known PDF → verify documents/sections/tables rows in test DB
- Integration test: re-parse same PDF → verify no duplicates (idempotency)
- Infrastructure test: spot launch → worker starts → parses 10 docs → terminates

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Spot instance reclaimed mid-batch | Per-batch commits (500 docs). Orchestrator relaunches. Zero data loss. |
| Some PDFs are scanned (OCR needed) | Skip OCR initially (set `ocr=False`). Parse digital PDFs first. OCR batch as follow-up. |
| Docling fails on complex layouts | Record error per-doc, continue. Review error rate after first 1,000 docs. |
| RDS storage fills up | Auto-scale enabled (max 1 TiB). Monitor via CloudWatch. 18 GiB projected is well within limits. |
| GPU instance costs exceed budget | `--max-hours` cap. Priority batch first ($38). Full corpus only after validating results. |
| AMI goes stale / Docling updates | Version pinned in AMI. Rebuild only when explicitly upgrading. |

## Rollback

```sql
DROP SCHEMA lava_parse CASCADE;
```

Terminate any running GPU instance. No other system affected.

## Traps to Avoid

1. **Don't proxy PDFs through cloud2** — GPU instance downloads directly from S3. cloud2 has 8 GiB RAM and 25 GiB free disk — it cannot buffer 580 GiB of PDFs.
2. **Don't store raw Docling JSON in RDS** — the full `DoclingDocument` JSON is huge (bounding boxes, coordinates). Store only the extracted text, headings, and table structure. Discard layout geometry.
3. **Don't run Docling on cloud2** — it's a t3.large (no GPU, 8 GiB RAM). CPU parsing at 3.1 sec/page × 5.6M pages = 200+ days. GPU is not optional.
4. **Don't parse everything at once** — prioritize annual/impact reports. Layer 2 needs those first. Parse the rest incrementally.
5. **Don't depend on OCR for the first pass** — most crawled PDFs are digital. Disabling OCR saves ~60% runtime. Handle the scanned subset separately after validating the pipeline works.
6. **Don't create a Django model for lava_parse** — same pattern as other lava_* schemas. Raw SQL via psycopg2. The worker runs outside Django anyway.

## Consultation Log

| Round | Model | Type | Verdict | Key Findings |
|-------|-------|------|---------|--------------|
| 1 | Gemini | spec-review | COMMENT | Minor AC2 clarification (intra-schema FKs allowed) |
| 2 | Codex | spec-review | REQUEST_CHANGES | 6 issues: failure semantics, work-claiming, transaction model, parse-version, table linkage, IAM specificity |
| 3 | Gemini | red-team-spec | REQUEST_CHANGES | 1 HIGH (RDS privilege separation), 4 MEDIUM (advisory lock, metadata filtering, error sanitization, AMI build) |

All findings addressed. Human approved 2026-05-16.

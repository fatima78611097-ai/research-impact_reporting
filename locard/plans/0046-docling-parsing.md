# Plan 0046: Docling Full-Document Parsing

**Spec:** locard/specs/0046-docling-parsing.md
**Protocol:** SPIDER
**Estimated Effort:** ~10-14 hours builder time (split across infrastructure setup + code)

## Overview

Build the Parse stage of the pipeline: a GPU worker (`docling_worker.py`) that runs on a G6.2xlarge spot instance and parses PDFs into structured sections/tables stored in `lava_parse` PostgreSQL schema, plus an orchestrator command (`parse_documents`) on cloud2 that manages the spot instance lifecycle.

This project has two distinct work streams:
1. **Infrastructure** (IAM, AMI, launch template, networking) — operator-executed
2. **Code** (worker, orchestrator, schema migration) — builder-executed

## Dependencies

**Infrastructure (operator provisions before builder starts):**
- G6.2xlarge spot availability in us-east-1a
- IAM role `docling_worker` with instance profile
- Security group membership for GPU instance
- AMI with Docling + CUDA (or user-data bootstrap for development)

**Python packages (on GPU instance AMI):**
- `docling==2.93.0` (pinned — document parsing)
- `psycopg2-binary` (RDS connection)
- `boto3` (S3 + EC2 metadata)

**Python packages (on cloud2 for orchestrator):**
- `boto3` (already installed — EC2 API for spot management)

## File Layout

```
lavandula/
  parse/
    __init__.py
    worker.py                    # GPU worker — standalone script
    chunking.py                  # Section/table extraction from DoclingDocument
    db.py                        # Database operations (insert, delete, work queue)
    config.py                    # Constants, validation, error sanitization
  dashboard/
    pipeline/
      management/
        commands/
          parse_documents.py     # Django orchestrator command (runs on cloud2)
  migrations/
    parse/
      001_create_lava_parse_schema.sql    # DDL for schema + tables + indexes
      002_create_docling_writer_user.sql  # DB user + grants
```

## Operational Semantics

These define the behavioral contracts the builder must implement. Codex review flagged these as underspecified — resolving them here so the builder has unambiguous guidance.

### Run Tag Lifecycle

`parse_runs.run_tag` is UNIQUE. Each invocation creates a new row. Reruns use a fresh tag (e.g., `priority-v1`, `priority-v2`). The `--status` command accepts a run_tag to show that specific run. If the orchestrator is relaunched after spot interruption for the SAME logical batch, it reuses the existing `parse_runs` row (matched by run_tag passed on CLI). The orchestrator checks: if a row exists and `finished_at IS NULL`, it's a resume — don't INSERT, just continue.

### Worker Lifecycle Invariant

**At most one worker process exists at any time.** Enforced by:
1. Orchestrator holds `pg_advisory_lock(hashtext('docling-parse'))` — prevents concurrent orchestrator instances.
2. Before launching a new spot instance, orchestrator terminates any existing instance tagged `Purpose=docling-parse` in the VPC.
3. Worker startup verifies no other worker is active by attempting `pg_try_advisory_lock(hashtext('docling-worker'))`. If held, worker exits immediately.

This three-layer defense ensures no overlap even during crash recovery.

### Commit Granularity

**Per-document transactions.** Each document's insert (document + sections + tables) is one atomic transaction. "Batch size" (500) controls how many SHAs are fetched from the work queue at once — it does NOT define the commit boundary. The stats_json is updated every 50 documents for progress visibility.

### Priority Filter Handling (AC29-31)

The `--priority` argument is a comma-separated list of `corpus.classification` values. Validation: each value must match `^[a-z_]+$`. The worker query uses `AND c.classification = ANY(:priority_list)`. When the priority batch is exhausted (0 eligible docs), the operator runs again with a broader filter (e.g., `--priority annual,impact,newsletter,program_description`) or no filter (all docs).

### Error Row Payload

When a document fails permanently, the INSERT uses these defaults for required fields:
```python
{
    'sha': item['content_sha256'],
    'org_ein': item['source_org_ein'],
    'parse_version': f"docling-{docling.__version__}",
    'page_count': 0,
    'section_count': 0,
    'table_count': 0,
    'figure_count': 0,
    'total_text_chars': 0,
    'parse_duration_ms': elapsed_ms,
    'error': sanitize_error(exc),  # max 500 chars
    'metadata_json': None,
    'sections': [],
    'tables': [],
}
```

### Section-to-Table ID Resolution

Tables reference `sections.id` (a BIGSERIAL). Since sections are inserted in the same transaction as tables, the resolution pattern is:

```python
with conn:
    with conn.cursor() as cur:
        # Insert document row
        cur.execute("INSERT INTO lava_parse.documents (...) VALUES (%s, ...)", (...))
        
        # Insert sections, get back IDs
        section_ids = {}
        for section in doc['sections']:
            cur.execute(
                "INSERT INTO lava_parse.sections (...) VALUES (%s, ...) RETURNING id",
                (sha, section['section_index'], ...)
            )
            section_ids[section['section_index']] = cur.fetchone()[0]
        
        # Insert tables with resolved section_id
        for table in doc['tables']:
            section_id = section_ids.get(table['section_index'])  # None if unlinked
            cur.execute(
                "INSERT INTO lava_parse.tables (...) VALUES (%s, ...)",
                (sha, table['table_index'], section_id, ...)
            )
```

For large documents (100+ sections), use `execute_values` with a CTE approach:
```sql
WITH inserted_sections AS (
    INSERT INTO lava_parse.sections (content_sha256, section_index, ...)
    VALUES %s
    RETURNING id, section_index
)
SELECT id, section_index FROM inserted_sections;
```
Then map `section_index → id` in Python for table inserts.

### Version Comparison for `--min-version`

`parse_version` is stored as `"docling-X.Y.Z"`. Comparison uses semantic versioning:
```python
from packaging.version import Version

def parse_docling_version(v: str) -> Version:
    return Version(v.removeprefix("docling-"))

# --reparse --min-version docling-2.93.0
# Selects: WHERE parse_version < 'docling-2.93.0' (string comparison works for semver with same prefix format)
```
Since all versions follow `docling-MAJOR.MINOR.PATCH` format, lexicographic string comparison is safe (no single-digit vs double-digit ambiguity in practice). If needed, the query uses `packaging.version` in Python to filter, not SQL string comparison.

### Bootstrap Strategy

**AMI-only for production.** The plan removes the temporary-public-IP fallback. The GPU instance has no public IP. The orchestrator connects via:
1. EC2 Instance Connect (pushes ephemeral SSH key to instance metadata — works on private IPs within same VPC)
2. If EC2 Instance Connect is unavailable: use SSM Session Manager (`aws ssm start-session`) as fallback

No `scp` of worker code needed — the worker script is baked into the AMI alongside Docling. Configuration (run_id, priority, batch_size) passed via SSM parameters or environment variables in the launch template user-data.

## Implementation Phases

### Phase 1: Schema & Module Structure (0.5 hours)

**Goal:** Create the `lava_parse` schema DDL and module skeleton.

**Steps:**

1.1. Write `lavandula/migrations/parse/001_create_lava_parse_schema.sql`:
- `CREATE SCHEMA IF NOT EXISTS lava_parse`
- All 4 tables exactly as specified: `documents`, `sections`, `tables`, `parse_runs`
- All 5 indexes: `idx_sections_sha`, `idx_sections_heading`, `idx_tables_sha`, `idx_documents_org`, `idx_documents_parsed_at`
- UNIQUE constraints: `sections(content_sha256, section_index)`, `tables(content_sha256, table_index)`
- FK: `tables.section_id → sections.id ON DELETE CASCADE`
- Grants to `research_app` (schema usage, all tables, all sequences)

1.2. Write `lavandula/migrations/parse/002_create_docling_writer_user.sql`:
```sql
-- Dedicated least-privilege user for GPU worker
CREATE USER docling_writer;
GRANT rds_iam TO docling_writer;
GRANT USAGE ON SCHEMA lava_parse TO docling_writer;
GRANT ALL ON ALL TABLES IN SCHEMA lava_parse TO docling_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA lava_parse TO docling_writer;
GRANT USAGE ON SCHEMA lava_corpus TO docling_writer;
GRANT SELECT ON lava_corpus.corpus TO docling_writer;
```

1.3. Create module skeleton:
- `lavandula/parse/__init__.py` (empty)
- `lavandula/parse/config.py` (constants)

**Acceptance:** DDL files exist, module structure exists.

### Phase 2: Worker Core — Config & Database Layer (1.5 hours)

**Goal:** Build the shared utilities: configuration, validation, database operations.

**Steps:**

2.1. Write `lavandula/parse/config.py`:

```python
import re

PARSE_VERSION_PREFIX = "docling"
BATCH_SIZE = 500
MAX_ERROR_LEN = 500
SHA256_RE = re.compile(r'^[a-f0-9]{64}$')
METADATA_ALLOWED_KEYS = frozenset(['title', 'year'])
S3_BUCKET = "lavandula-nonprofit-collaterals"
S3_PREFIX = "pdfs/"

TRANSIENT_RETRY_COUNT = 3
TRANSIENT_RETRY_BASE_SECONDS = 2.0

def validate_sha256(sha: str) -> bool: ...
def sanitize_error(exc: Exception) -> str:
    """Return error class + truncated message, max 500 chars. No stack traces."""
    ...
def filter_metadata(raw_metadata: dict) -> dict:
    """Keep only allowed keys (title, year). Discard everything else."""
    ...
```

2.2. Write `lavandula/parse/db.py`:

```python
def get_connection(user='docling_writer') -> psycopg2.connection:
    """Connect to RDS via IAM auth token. TLS required."""
    ...

def fetch_work_batch(conn, priority_filter: list[str], batch_size: int) -> list[dict]:
    """Fetch next batch of unparsed documents matching priority filter.
    Returns list of {content_sha256, source_org_ein}.
    """
    ...

def insert_document(conn, doc: dict) -> None:
    """Atomic per-document transaction: INSERT document + sections + tables.
    All in one BEGIN/COMMIT block.
    Uses parameterized queries exclusively (execute_values for sections/tables).
    """
    ...

def delete_document_data(conn, sha: str) -> None:
    """Delete tables, sections, document row for a SHA (for retry/reparse).
    Order: tables → sections → documents.
    """
    ...

def update_run_stats(conn, run_id: int, stats: dict) -> None:
    """UPDATE parse_runs SET stats_json = ... WHERE id = ..."""
    ...

def acquire_advisory_lock(conn) -> bool:
    """pg_try_advisory_lock(hashtext('docling-parse')). Returns True if acquired."""
    ...

def release_advisory_lock(conn) -> None:
    """pg_advisory_unlock(hashtext('docling-parse'))."""
    ...
```

**Key implementation detail for `insert_document`:**
```python
def insert_document(conn, doc: dict) -> None:
    with conn:  # auto-commit on success, rollback on exception
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO lava_parse.documents (...) VALUES (%s, %s, ...)",
                (doc['sha'], doc['org_ein'], ...)
            )
            if doc['sections']:
                execute_values(cur,
                    "INSERT INTO lava_parse.sections (...) VALUES %s",
                    doc['sections']
                )
            if doc['tables']:
                execute_values(cur,
                    "INSERT INTO lava_parse.tables (...) VALUES %s",
                    doc['tables']
                )
```

2.3. Write unit tests:
- `test_config.py`: SHA validation, error sanitization (truncation, no paths), metadata filtering
- `test_db.py`: work-queue query construction, insert parameterization (mock cursor)

**Acceptance:** AC7, AC10 foundations testable.

### Phase 3: Worker Core — Chunking & Table Extraction (2 hours)

**Goal:** Build the logic that converts a Docling `DoclingDocument` into our schema rows.

**Steps:**

3.1. Write `lavandula/parse/chunking.py`:

```python
from docling.document_converter import DocumentConverter
from docling.chunking import HierarchicalChunker

def parse_pdf(pdf_path: Path) -> 'DoclingDocument':
    """Run Docling DocumentConverter on a PDF. Returns DoclingDocument.
    Raises DoclingParseError on failure.
    """
    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    return result.document

def extract_sections(doc: 'DoclingDocument') -> list[dict]:
    """Run HierarchicalChunker, return list of section dicts.
    
    Each dict: {
        section_index: int,
        heading: str | None,
        heading_level: int | None,
        body_text: str,
        char_count: int,
        page_start: int | None,
        page_end: int | None,
        parent_headings: list[str],
    }
    """
    chunker = HierarchicalChunker()
    chunks = list(chunker.chunk(doc))
    sections = []
    for i, chunk in enumerate(chunks):
        sections.append({
            'section_index': i,
            'heading': _extract_heading(chunk),
            'heading_level': _extract_heading_level(chunk),
            'body_text': chunk.text,
            'char_count': len(chunk.text),
            'page_start': _get_page_start(chunk),
            'page_end': _get_page_end(chunk),
            'parent_headings': _get_parent_headings(chunk),
        })
    return sections

def extract_tables(doc: 'DoclingDocument', sections: list[dict]) -> list[dict]:
    """Extract tables and link to containing section.
    
    Linkage rule: table assigned to section whose page range contains
    the table's page AND whose document position most closely precedes
    the table. If no match, section_id = None (resolved to NULL in DB).
    
    Each dict: {
        table_index: int,
        section_index: int | None,  -- links to section by index (resolved to ID at insert time)
        page_number: int | None,
        caption: str | None,
        row_count: int,
        col_count: int,
        data_json: list[list],  -- array of row arrays
        markdown: str,
    }
    """
    ...

def get_document_metadata(doc: 'DoclingDocument') -> dict:
    """Extract page_count, figure_count, and filtered metadata_json."""
    ...
```

3.2. Key implementation notes:

- **`_extract_heading`:** Docling chunks have a `meta.headings` attribute containing the heading text. Use the last heading in the list (most specific).
- **`_get_parent_headings`:** Docling chunks have `meta.headings` as a list of ancestor headings. Return the full list as `parent_headings`.
- **`_get_page_start/_get_page_end`:** Docling chunks have `meta.doc_items` with provenance including page numbers. Min/max across items gives page range.
- **Table linkage:** Iterate tables in document order. For each table, find sections by page overlap, then pick the one with highest `section_index` that precedes the table's position. This is deterministic given deterministic chunking order.
- **Table data:** Use `table.export_to_dataframe()` → convert to list of lists for JSONB storage. Also store `table.export_to_markdown()` for human readability.

3.3. Write unit tests:
- `test_chunking.py`: Mock DoclingDocument with known structure → verify sections extracted correctly
- Test heading hierarchy extraction
- Test table linkage (table on page 5, sections span pages 3-6 and 7-10 → links to first)
- Test empty document (0 sections → returns empty list)
- Test document with no headings (sections still created, heading=NULL)

**Acceptance:** AC4, AC5, AC18, AC19, AC20, AC21, AC22, AC23, AC24.

### Phase 4: Worker Main Loop (2 hours)

**Goal:** Build the complete worker script that runs on the GPU instance.

**Steps:**

4.1. Write `lavandula/parse/worker.py`:

```python
"""Docling GPU worker — runs on G6.2xlarge spot instance.

Usage:
    python -m lavandula.parse.worker --run-id <id> [--priority annual,impact] [--batch-size 500]
"""

def main():
    args = parse_args()
    conn = db.get_connection()
    
    s3 = boto3.client('s3')
    stats = {'total': 0, 'succeeded': 0, 'failed': 0, 'start_time': time.time()}
    
    while True:
        batch = db.fetch_work_batch(conn, args.priority, args.batch_size)
        if not batch:
            break  # No more work
        
        # Prefetch: download PDFs for this batch
        pdf_paths = download_batch(s3, batch, tmp_dir)
        
        for item in batch:
            sha = item['content_sha256']
            pdf_path = pdf_paths.get(sha)
            
            if not pdf_path:
                record_error(conn, item, 'download_failed')
                stats['failed'] += 1
                continue
            
            try:
                result = process_one(pdf_path, item)
                db.insert_document(conn, result)
                stats['succeeded'] += 1
            except TransientError:
                # Don't record — will retry on next run
                stats['failed'] += 1
            except PermanentError as e:
                record_error(conn, item, e)
                stats['failed'] += 1
            finally:
                stats['total'] += 1
                cleanup_pdf(pdf_path)
            
            # Check spot interruption
            if spot_termination_pending():
                break
        
        db.update_run_stats(conn, args.run_id, stats)
    
    # Final stats update
    stats['end_time'] = time.time()
    stats['duration_seconds'] = stats['end_time'] - stats['start_time']
    db.update_run_stats(conn, args.run_id, stats)

def process_one(pdf_path: Path, item: dict) -> dict:
    """Parse one PDF, return structured result for db.insert_document."""
    start = time.time()
    
    doc = chunking.parse_pdf(pdf_path)
    sections = chunking.extract_sections(doc)
    tables = chunking.extract_tables(doc, sections)
    meta = chunking.get_document_metadata(doc)
    
    duration_ms = int((time.time() - start) * 1000)
    
    return {
        'sha': item['content_sha256'],
        'org_ein': item['source_org_ein'],
        'parse_version': f"docling-{docling.__version__}",
        'page_count': meta['page_count'],
        'section_count': len(sections),
        'table_count': len(tables),
        'figure_count': meta['figure_count'],
        'total_text_chars': sum(s['char_count'] for s in sections),
        'parse_duration_ms': duration_ms,
        'error': None,
        'metadata_json': config.filter_metadata(meta.get('metadata', {})),
        'sections': sections,
        'tables': tables,
    }

def download_batch(s3, batch, tmp_dir) -> dict[str, Path]:
    """Download PDFs in parallel (ThreadPoolExecutor, 8 threads).
    Returns {sha: local_path} for successful downloads.
    """
    ...

def spot_termination_pending() -> bool:
    """Check EC2 instance metadata for spot termination notice."""
    try:
        resp = httpx.get(
            'http://169.254.169.254/latest/meta-data/spot/instance-action',
            timeout=1.0
        )
        return resp.status_code == 200
    except:
        return False

def record_error(conn, item, error):
    """Insert document row with error field set."""
    ...
```

4.2. Key implementation details:
- **Download parallelism:** Use ThreadPoolExecutor(max_workers=8) for S3 downloads. Pre-download next batch while processing current for pipeline overlap.
- **Temp directory:** Use `/tmp/docling-work/` on the instance SSD. Clean up per-document after insert.
- **Memory management:** Process one PDF at a time (Docling loads the full document model into memory). For PDFs > 500 pages, skip prefetch to reduce memory pressure.
- **Spot check:** Poll every document (cheap HTTP call to instance metadata). If termination pending, finish current document transaction and exit gracefully.
- **Logging:** Write structured JSON logs to stdout. Orchestrator can capture via SSH or CloudWatch agent.

4.3. Write unit tests:
- `test_worker.py`: Mock Docling + DB, verify main loop processes batch correctly
- Test spot interruption handling (mock metadata endpoint)
- Test download failure (one PDF fails → error recorded, others continue)
- Test empty batch (worker exits cleanly)

**Acceptance:** AC6, AC7, AC8, AC9, AC11.

### Phase 5: Orchestrator Command (2.5 hours)

**Goal:** Django management command on cloud2 that manages the GPU spot instance and worker lifecycle.

**Steps:**

5.1. Write `parse_documents.py` management command:

```python
class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument('run_tag')
        parser.add_argument('--priority', default='annual,impact')
        parser.add_argument('--instance-type', default='g6.2xlarge')
        parser.add_argument('--spot', action='store_true', default=True)
        parser.add_argument('--max-hours', type=int, default=12)
        parser.add_argument('--batch-size', type=int, default=500)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--status', action='store_true')
        parser.add_argument('--terminate', action='store_true')
        parser.add_argument('--retry-errors', action='store_true')
        parser.add_argument('--reparse', action='store_true')
        parser.add_argument('--min-version', type=str)
    
    def handle(self, *args, **options):
        if options['dry_run']:
            return self._dry_run(options)
        if options['status']:
            return self._show_status(options)
        if options['terminate']:
            return self._terminate(options)
        return self._run(options)
```

5.2. Orchestrator responsibilities:

**`_dry_run`:**
- Query eligible doc count (same WHERE clause as worker)
- Estimate: `count × 30 pages avg × 0.49 sec/page ÷ 3600 = GPU-hours × $0.30 = cost`
- Report: `{eligible} docs, ~{pages} pages, ~{hours:.1f} GPU-hours, ~${cost:.0f} spot cost`

**`_run`:**
1. Acquire advisory lock (abort if held)
2. Create `parse_runs` record
3. If `--retry-errors`: delete error rows for this priority filter
4. If `--reparse --min-version X`: delete rows with parse_version < X
5. Launch or reuse GPU spot instance:
   - Check for existing instance tagged `docling-worker` + this run_tag
   - If none: `ec2.run_instances()` with launch template, spot market options, tags
   - Wait for instance to reach `running` state
6. Bootstrap (if not pre-baked AMI): copy worker script via EC2 Instance Connect + scp
7. Start worker: `ssh <instance> python -m lavandula.parse.worker --run-id <id> --priority <filter>`
8. Monitor loop:
   - Poll `parse_runs.stats_json` every 60s
   - Print progress: `{succeeded}/{total} docs ({percent}%), {failed} errors`
   - Check instance state (terminated = spot reclaimed → relaunch)
   - Check elapsed time vs `--max-hours`
9. On completion or timeout: terminate instance, update `parse_runs.finished_at`
10. Release advisory lock

**`_show_status`:**
- Query latest `parse_runs` for this run_tag
- Show: started_at, elapsed, stats_json (total/succeeded/failed), instance state

**`_terminate`:**
- Find instance by tag, terminate it
- Update parse_runs.finished_at

5.3. EC2 spot instance management:

```python
def _launch_spot_instance(self, run_tag, instance_type):
    """Launch G6 spot instance with proper config."""
    ec2 = boto3.client('ec2')
    response = ec2.run_instances(
        ImageId=self._get_ami_id(),  # from SSM parameter
        InstanceType=instance_type,
        MinCount=1, MaxCount=1,
        InstanceMarketOptions={'MarketType': 'spot'},
        IamInstanceProfile={'Name': 'docling_worker'},
        SubnetId='subnet-0e2008e48d602e945',
        SecurityGroupIds=['sg-0d9a6217a104cfe35'],
        TagSpecifications=[{
            'ResourceType': 'instance',
            'Tags': [
                {'Key': 'Name', 'Value': f'docling-worker-{run_tag}'},
                {'Key': 'Project', 'Value': 'lavandula'},
                {'Key': 'Purpose', 'Value': 'docling-parse'},
            ]
        }],
    )
    return response['Instances'][0]['InstanceId']
```

5.4. Write integration test:
- Mock EC2 + RDS, verify orchestrator creates run record, calls launch, polls progress
- Test `--dry-run` output format
- Test advisory lock conflict (second invocation aborts)

**Acceptance:** AC12, AC13, AC14, AC15, AC16, AC17, AC25, AC26, AC27.

### Phase 6: Infrastructure Setup (Operator Steps)

**Goal:** Provision the AWS infrastructure required by the code. These are operator-executed, not builder-coded.

**Steps (executed by operator after code is merged):**

6.1. Create IAM role `docling_worker`:
- Trust policy: EC2 assume role
- Permissions:
  - `s3:GetObject` on `arn:aws:s3:::lavandula-nonprofit-collaterals/pdfs/*`
  - `rds-db:connect` for `docling_writer` on `db-NAMZ7DUPILQKINJANPKHMXEXDU`
  - `ssm:GetParameter` on `/cloud2.lavandulagroup.com/rds-*`
- Create instance profile `docling_worker`, attach role

6.2. Create PostgreSQL user:
```sql
-- Run as postgres (requires master password)
CREATE USER docling_writer;
GRANT rds_iam TO docling_writer;
```

6.3. Apply schema migration:
```bash
psql -f lavandula/migrations/parse/001_create_lava_parse_schema.sql
psql -f lavandula/migrations/parse/002_create_docling_writer_user.sql
```

6.4. Build AMI (Option B for first run, Option A for production):
- Start from AWS Deep Learning AMI (Ubuntu 22.04, CUDA pre-installed)
- Install: `pip install docling==2.93.0 psycopg2-binary boto3 httpx`
- Test: `python -c "from docling.document_converter import DocumentConverter; print('OK')"`
- Create AMI, store ID in SSM: `/cloud2.lavandulagroup.com/docling-ami-id`

6.5. Add `ec2:RunInstances`, `ec2:TerminateInstances`, `ec2:DescribeInstances` permissions to cloud2's role (needed for orchestrator to manage spot instances).

**Acceptance:** AC25, AC26, AC28 (infrastructure prerequisites met).

### Phase 7: End-to-End Validation (1.5 hours)

**Goal:** Run the pipeline on a small subset to validate everything works.

**Steps:**

7.1. Dry-run to verify eligible count:
```bash
python manage.py parse_documents smoke-test --dry-run --priority annual,impact
```

7.2. Run on 10 documents (manual, on a dev GPU or CPU-fallback):
```bash
# Can test worker directly without full spot orchestration
python -m lavandula.parse.worker --run-id 1 --priority annual,impact --batch-size 10
```

7.3. Verify results in database:
```sql
SELECT content_sha256, page_count, section_count, table_count, total_text_chars, parse_duration_ms
FROM lava_parse.documents
WHERE error IS NULL
LIMIT 10;

-- Check section quality
SELECT heading, heading_level, char_count, page_start, parent_headings
FROM lava_parse.sections
WHERE content_sha256 = '<sample_sha>'
ORDER BY section_index;

-- Check table extraction
SELECT caption, row_count, col_count, page_number
FROM lava_parse.tables
WHERE content_sha256 = '<sample_sha>';
```

7.4. Verify idempotency:
```bash
# Re-run same batch — should process 0 documents
python -m lavandula.parse.worker --run-id 1 --priority annual,impact --batch-size 10
# Verify stats show 0 new docs processed
```

7.5. If smoke test passes, ready for priority batch:
```bash
python manage.py parse_documents priority-batch-v1 --priority annual,impact --max-hours 12
```

**Acceptance:** Full pipeline executes without error on subset. Sections have meaningful headings. Tables have structured data.

## Test Strategy

| Layer | Scope | Tools |
|-------|-------|-------|
| Unit | config, db ops, chunking logic | pytest, no DB, mocked Docling |
| Integration | worker → DB → verify rows | pytest, test DB (local PostgreSQL) |
| Infrastructure | spot launch → worker → terminate | Manual validation (real AWS) |
| Smoke | 10 real PDFs parsed end-to-end | Real Docling, real RDS |

**Required test cases (from Codex review):**
- Idempotent rerun: parse doc, run again → 0 new inserts (real DB integration test)
- `--retry-errors`: parse fails → error row exists → retry-errors deletes + requeues → success
- Transient retry: mock S3 timeout 2×, succeed on 3rd → no error row recorded
- Run-tag reuse: orchestrator resumes existing run (finished_at IS NULL) without INSERT conflict
- Worker interruption: kill worker mid-batch → partial docs NOT in DB → relaunch picks them up
- Section-to-table ID mapping: verify tables.section_id matches correct section after insert
- Priority filter: `--priority annual` only processes annual; `--priority annual,impact` processes both
- Version comparison: `--reparse --min-version docling-2.92.0` selects docs parsed by 2.92.0, skips 2.93.0

**Test count estimate:** ~30-35 tests across unit + integration.

**Note:** Infrastructure tests (Phase 6-7) are manual/operational, not automated CI. The spot instance lifecycle depends on real AWS APIs and GPU availability.

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Docling API differs from research (v2.93 specifics) | Phase 3 tests against real Docling output. Builder validates chunk structure early. |
| EC2 Instance Connect not available in private subnet | Fallback: SSM Session Manager (requires SSM agent on AMI — included in AWS Deep Learning AMI). No public IP ever assigned. |
| G6.2xlarge spot capacity unavailable | Fall back to g5.2xlarge (A10G, similar performance). Or use on-demand for small batches. |
| AMI build fails (CUDA version mismatch) | Use AWS Deep Learning AMI as base (CUDA pre-installed, tested). |
| Worker OOM on large PDFs | Monitor memory. Process 500+ page docs one-at-a-time with no prefetch. |
| RDS connection limits exceeded | Worker uses 1 connection. Orchestrator uses 1. Well within 181 max. |

## Rollback

```sql
DROP SCHEMA lava_parse CASCADE;
```

Terminate any running GPU instance. Delete IAM role/profile if needed. No other system affected.

## Operator Steps Summary

| Step | When | Who |
|------|------|-----|
| Create IAM role `docling_worker` | Before first run | Operator |
| Create DB user `docling_writer` | Before first run | Operator (needs master password) |
| Apply schema migration | Before first run | Operator |
| Build/configure AMI | Before first run | Operator |
| Add EC2 permissions to cloud2 role | Before first run | Operator |
| Store AMI ID in SSM | Before first run | Operator |
| Run priority batch | After code merge + infra ready | Operator |

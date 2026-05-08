# Spec 0035: Multi-Page Classification Context & Hardened Classifier

**Status**: Approved
**Author**: Architect
**Date**: 2026-05-08
**Dependencies**: 0023 (classifier expansion), 0025 (definition-driven classifier), 0007 (S3 PDF archive)

## Problem Statement

The current classifier sees only `first_page_text` — a single page extracted at crawl time, capped at 4,096 chars. This creates three categories of failure:

1. **Cover-page blindness** (48K docs, 33% of corpus): 14,149 docs have < 50 chars of text (just a title or date). Another 34,404 have 50–200 chars. The classifier is guessing on a third of the corpus.

2. **No metadata context**: The classifier never sees the source URL, page count, file size, or PDF creator — all strong classification signals. A 2-page PDF from a URL containing "/990/" is obviously different from a 48-page PDF at "/annual-report/", but the model can't distinguish them.

3. **No rule-based pre-filtering**: 1,965 IRS 990s are classified as `financial_report` despite having unambiguous markers ("OMB No. 1545-0047", "Form 990") that don't need an LLM. Similar deterministic signals exist for other obvious categories.

Re-running the same classifier won't fix these problems. The model needs more text, more context, and some categories shouldn't go through the LLM at all.

## Goals

1. **Persistent multi-page extraction**: Extract pages 1–5 from every S3-archived PDF into a `classification_context` table. This is the expensive operation (S3 I/O) — store it once, re-use for every classifier iteration.

2. **Metadata-augmented prompt**: Feed the classifier URL path, page count, file size, and pdf_creator alongside the document text. These are free signals we already have.

3. **Rule-based pre-filter**: Deterministic classification for unambiguous documents (990s, financial statements with specific PDF creators, documents whose URLs contain definitive keywords). No LLM cost, near-perfect accuracy.

4. **Full corpus re-classification**: Re-classify all 145K docs using the improved pipeline. Store results alongside (not overwriting) existing classifications so we can compare.

5. **Iterability**: The architecture must support "tweak prompt → re-run → compare" cycles at $30/run on DeepSeek, without re-extracting from S3.

## Non-Goals

- Full OCR for scanned PDFs (future work, separate spec)
- Changing the taxonomy itself (that's a product decision, not this spec)
- Replacing the definition-driven classifier framework (Spec 0025) — we build on it
- Real-time classification of newly crawled docs (this is a batch improvement; the crawl-time path continues using `first_page_text` until this is proven)

## Technical Design

### Phase 1: Schema & Extraction Pipeline

#### 1a. `classification_context` Table

```sql
CREATE TABLE lava_corpus.classification_context (
    content_sha256    TEXT PRIMARY KEY
        REFERENCES lava_corpus.corpus(content_sha256),
    pages_text        TEXT NOT NULL,       -- pages 1-5, concatenated with markers
    pages_extracted   SMALLINT NOT NULL,   -- how many pages were actually extracted
    total_pages       SMALLINT,            -- total page count in PDF
    extraction_method TEXT NOT NULL,       -- 'pypdf' or 'ocr' (future)
    extracted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    text_length       INT NOT NULL         -- length(pages_text), for quick stats
);

CREATE INDEX idx_cc_text_length ON lava_corpus.classification_context(text_length);
```

**Design decisions**:
- Primary key is `content_sha256` (same PDF = same extraction, regardless of which org it was found on)
- `pages_text` concatenates pages with `\n--- PAGE N ---\n` markers so the LLM knows page boundaries
- Cap `pages_text` at 16,000 chars (≈4,000 tokens) — covers pages 1–5 for virtually all PDFs. Median first-page text is ~1,000 chars; 5 pages at ~3,000 chars each is typical, with cap headroom for dense pages
- `pages_extracted` records how many pages we actually got (some PDFs have < 5 pages)

#### 1b. Batch Extraction Command

New Django management command: `extract_classification_context`

```
python3 manage.py extract_classification_context
python3 manage.py extract_classification_context --limit 1000    # dev/testing
python3 manage.py extract_classification_context --reextract     # force re-extraction
```

**Flow**:
1. Query `corpus LEFT JOIN classification_context` for rows where context is NULL
2. For each batch of 100 SHA256 hashes:
   a. Download PDF from S3: `s3://lavandula-nonprofit-collaterals/pdfs/{sha256}.pdf`
   b. Verify SHA256 of downloaded bytes matches the key (reject + alert on mismatch)
   c. Run pypdf extraction in a `multiprocessing.Process` with:
      - 30-second wall-clock timeout (killed on timeout → `failed:timeout`)
      - 1 GB RLIMIT_AS memory cap (killed on OOM → `failed:oom`)
      - Do NOT attempt decryption of encrypted PDFs (→ `failed:encrypted`)
   d. Extract pages 0–4 text, concatenate with page markers, cap at 16,000 chars
   e. Bulk INSERT into `classification_context` (ON CONFLICT DO NOTHING for safety)
3. Use ProcessPoolExecutor (4 workers) for extraction — each worker is isolated and killable
4. Track progress via Job record (phase: `extract-context`)
5. Skip PDFs > 100 MB without downloading (→ `skipped:oversized`)

**Cost**: ~167K S3 GET requests × $0.0004/1000 = $0.07. Time is the real cost — estimate ~2 hours at 25 PDFs/sec.

### Phase 2: Rule-Based Pre-Filter

A deterministic triage layer that runs before the LLM. Rules are applied in priority order; first match wins.

#### Rule definitions (stored in `lavandula/nonprofits/definitions/prefilter_rules.yaml`):

```yaml
rules:
  # 990s — strongest signal
  - name: irs_990_text
    material_type: not_relevant
    confidence: 0.99
    reasoning: "Rule: IRS Form 990 detected from page text markers"
    conditions:
      text_contains_any:
        - "OMB No. 1545-0047"
        - "Return of Organization Exempt From Income Tax"
        - "Form 990-EZ"
        - "Form 990-PF"
        - "Form 990-T"

  - name: irs_990_url
    material_type: not_relevant
    confidence: 0.95
    reasoning: "Rule: URL contains 990 filing indicator"
    conditions:
      url_matches: "(?i)/990[^a-z]|form.?990|irs.?990"

  # Compound rule: 990 creator + text co-signal (creator alone is attacker-controlled)
  - name: irs_990_creator_plus_text
    material_type: not_relevant
    confidence: 0.95
    reasoning: "Rule: Known 990 filing software + 990 text markers"
    conditions:
      pdf_creator_any:
        - "ProSystem fx"
        - "Intuit Inc. FPS Engine"
      text_contains_any:
        - "Form 990"
        - "Return of Organization"
```

**Implementation**: `RuleEngine` class in `lavandula/nonprofits/prefilter.py`:
- Loads rules from YAML (`yaml.safe_load`) at startup; missing/unreadable file aborts startup (fail closed)
- `evaluate(text, url, pdf_creator, page_count, file_size) → (material_type, confidence, reasoning) | None`
- Returns `None` if no rule matches (falls through to LLM)
- Rules are data, not code — PM can add/edit rules without touching Python
- Rules checked into git and require code review for changes (no runtime fetch)
- Records SHA256 of rules YAML file in `classified_by` (e.g., `'rule:irs_990_text@a1b2c3d4'`)
- `text_contains_any` uses Aho-Corasick multi-pattern match (constant time per input regardless of rule count)
- `url_matches` regexes validated at load time; rejected if they contain nested quantifiers or fail a 100ms timeout test against a 5KB adversarial string
- All regex matches run with a 1-second timeout via the `regex` package (not stdlib `re`)
- URL inputs capped at 2,048 chars before regex evaluation
- **Suppression sampling**: 5% of docs classified as `not_relevant` by rules are randomly sampled through the LLM as a control. Disagreements are logged for audit. Per-domain suppression rates > 50% trigger a warning.

### Phase 3: Metadata-Augmented Classifier Prompt

Modify the V3 classifier's user message to include metadata context when available:

**Current prompt** (user message):
```
Classify the nonprofit PDF below by calling the record_classification tool.
<untrusted_document>
{first_page_text}
</untrusted_document>
```

**New prompt** (user message):
```
Classify the nonprofit PDF below by calling the record_classification tool.

<document_metadata>
URL path: /wp-content/uploads/2024/annual-report-2024.pdf
Page count: 32
File size: 4.2 MB
PDF creator: Adobe InDesign 2024
</document_metadata>

<untrusted_document>
--- PAGE 1 ---
[page 1 text]
--- PAGE 2 ---
[page 2 text]
--- PAGE 3 ---
[page 3 text]
</untrusted_document>
```

**Key decisions**:
- Metadata goes in `<document_metadata>` tags (separate from untrusted document text)
- URL path extracted via `urllib.parse.urlparse` (scheme allowlist: http, https). Only `.path` component used — no domain, subdomain, query params, or fragments. Path sanitized (strip angle brackets, control chars) before inclusion.
- File size is human-readable (e.g., "4.2 MB")
- `pages_text` from `classification_context` replaces `first_page_text`
- Falls back to `first_page_text` if context not yet extracted
- **Prompt injection defense**: Document text sanitized before prompt assembly — strip `</untrusted_document>`, `<document_metadata>`, and other XML-like tags. Page boundary markers (`--- PAGE N ---`) are unique per-request with a random nonce (e.g., `--- PAGE 2 [x7f3a] ---`) so attacker-planted markers are distinguishable.
- `pdf_creator` is attacker-controlled metadata — sanitized (strip angle brackets, control chars, cap 100 chars) and included in the untrusted section, not the metadata section.

**Definition file change**: Add a `context_mode: multipage` field to the definition frontmatter. The classifier engine checks this flag and assembles the augmented prompt when set. Default for definitions without this field is `single_page` (backwards compatible).

### Phase 4: Re-Classification Pipeline

New management command: `reclassify_corpus`

```
python3 manage.py reclassify_corpus                          # full corpus
python3 manage.py reclassify_corpus --sample 1000            # random sample for testing
python3 manage.py reclassify_corpus --where "material_type = 'financial_report'"  # targeted
python3 manage.py reclassify_corpus --dry-run                # show what would be classified
python3 manage.py reclassify_corpus --run-tag v3.1           # tag for comparison
```

**Flow**:
1. Rule-based pre-filter runs first on ALL rows
   - Rows matched by rules: write classification directly (skip LLM)
   - Rows not matched: queue for LLM classification
2. LLM classification uses `classification_context.pages_text` (falls back to `corpus.first_page_text`)
3. Results written to new columns (not overwriting existing):

```sql
ALTER TABLE lava_corpus.corpus ADD COLUMN v3_material_type TEXT;
ALTER TABLE lava_corpus.corpus ADD COLUMN v3_confidence REAL;
ALTER TABLE lava_corpus.corpus ADD COLUMN v3_reasoning TEXT;
ALTER TABLE lava_corpus.corpus ADD COLUMN v3_classified_at TIMESTAMPTZ;
ALTER TABLE lava_corpus.corpus ADD COLUMN v3_run_tag TEXT;
ALTER TABLE lava_corpus.corpus ADD COLUMN v3_classified_by TEXT;  -- 'rule:{rule_name}' or 'llm:{model}'
```

This lets us compare old vs new classifications without losing data. Once validated, a follow-up migration promotes `v3_*` to the canonical columns.

**Cost estimate** (full corpus, 145K docs):
- Rule-based: ~15K docs at $0 (990s, obvious financials)
- LLM (remaining ~130K): DeepSeek V3 at ~1,200 input tokens + 80 output tokens = ~$24
- Total: **~$24** per full-corpus run

### Phase 5: Comparison & Validation

Management command: `compare_classifications`

```
python3 manage.py compare_classifications --run-tag v3.1
```

Output:
```
Total: 145,702
Rule-classified: 14,892 (10.2%)
LLM-classified: 130,810 (89.8%)

Changes from current classification:
  financial_report → not_relevant:     1,965 (990s caught by rules)
  annual_report → impact_report:         412
  impact_report → annual_report:         287
  other_collateral → program_brochure:   198
  ...

Confidence improvement:
  Avg confidence (old): 0.894
  Avg confidence (new): 0.937
  Docs with confidence < 0.8 (old): 22,962
  Docs with confidence < 0.8 (new):  4,210
```

## Migration Plan

```sql
-- Phase 1: classification_context table
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

-- Phase 4: v3 classification columns
ALTER TABLE lava_corpus.corpus ADD COLUMN v3_material_type TEXT;
ALTER TABLE lava_corpus.corpus ADD COLUMN v3_confidence REAL;
ALTER TABLE lava_corpus.corpus ADD COLUMN v3_reasoning TEXT;
ALTER TABLE lava_corpus.corpus ADD COLUMN v3_classified_at TIMESTAMPTZ;
ALTER TABLE lava_corpus.corpus ADD COLUMN v3_run_tag TEXT;
ALTER TABLE lava_corpus.corpus ADD COLUMN v3_classified_by TEXT;
```

## Acceptance Criteria

### Phase 1 — Extraction
- AC1: `classification_context` table created with FK to corpus
- AC2: `extract_classification_context` command extracts pages 1–5 from S3 PDFs
- AC3: Extraction is idempotent (re-runs skip already-extracted rows)
- AC4: `pages_text` capped at 16,000 chars with page boundary markers
- AC5: Job record created for dashboard visibility
- AC6: Handles missing PDFs gracefully (logs warning, continues)
- AC7: ThreadPoolExecutor for S3 downloads, configurable worker count

### Phase 2 — Pre-Filter
- AC8: Rules loaded from YAML file at startup
- AC9: 990s detected by text markers classified as `not_relevant` without LLM
- AC10: 990s detected by URL pattern classified as `not_relevant`
- AC11: Rules are evaluated in priority order; first match wins
- AC12: Rule matches include confidence and reasoning
- AC13: `None` returned when no rule matches (falls through to LLM)

### Phase 3 — Augmented Prompt
- AC14: Classifier prompt includes metadata when `context_mode: multipage`
- AC15: URL path is domain-stripped before inclusion
- AC16: `pages_text` from `classification_context` used when available
- AC17: Falls back to `first_page_text` when context not extracted
- AC18: Metadata in `<document_metadata>` tags separate from `<untrusted_document>`

### Phase 4 — Re-Classification
- AC19: `reclassify_corpus` command processes full corpus
- AC20: Rule-based pre-filter runs before LLM on every row
- AC21: Results written to `v3_*` columns (existing classifications preserved)
- AC22: `--run-tag` labels each run for A/B comparison
- AC23: `--sample N` processes a random sample for quick testing
- AC24: `--dry-run` shows counts without writing
- AC25: `v3_classified_by` distinguishes rule vs LLM classifications

### Phase 5 — Comparison
- AC26: `compare_classifications` shows category migration matrix
- AC27: Confidence distribution comparison (old vs new)
- AC28: Breakdowns by `v3_classified_by` (rule vs LLM)

### Cross-Cutting
- AC29: `classification_runs` and `classification_results` tables store full run history
- AC30: Reclassification is resumable via cursor on Job record
- AC31: Advisory locks prevent concurrent extraction or reclassification
- AC32: Encrypted/corrupt/image-only PDFs produce rows with `extraction_method` indicating failure
- AC33: Rule matching is case-insensitive, whitespace-normalized, apostrophe-normalized
- AC34: YAML rules parsed with `yaml.safe_load`; invalid regex fails at startup
- AC35: Error logs never include document text excerpts
- AC36: URL path stripped of scheme, domain, subdomain, and query params before prompt inclusion

## Cost Analysis

| Operation | Cost | Time (est.) |
|---|---|---|
| S3 extraction (167K GETs) | $0.07 | ~2 hours |
| Full LLM re-classify (130K × DeepSeek) | $24 | ~3 hours |
| Subsequent re-runs (prompt tweak) | $24 | ~3 hours |
| Storage (classification_context, ~2KB/row avg) | ~330 MB | — |

Total first run: **~$24**. Each subsequent iteration: **~$24** (no re-extraction).

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Multi-page text still insufficient for scanned PDFs | `extraction_method` column tracks this; OCR can backfill later |
| Rule-based filter has false positives | Rules require high-confidence thresholds; `v3_classified_by` tracks provenance for audit |
| DeepSeek model quality insufficient | `v3_*` columns preserve both old and new; can swap to Haiku ($278) if needed |
| S3 extraction takes too long | Parallelized with ThreadPoolExecutor; can run overnight; only runs once |

## Run History & Iterability

The `v3_*` columns on corpus store only the latest run's results. For multi-iteration tuning (run v3.1, tweak prompt, run v3.2, compare all three), a separate run history table preserves every classification decision:

```sql
CREATE TABLE lava_corpus.classification_runs (
    id              SERIAL PRIMARY KEY,
    run_tag         TEXT NOT NULL UNIQUE,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    config_json     JSONB,          -- model, definition, rules_yaml_sha256, parameters
    rules_snapshot  TEXT,           -- full YAML content at run time for reproducibility
    stats_json      JSONB,          -- total, rule_matched, llm_classified, by_type counts
    notes           TEXT
);

CREATE TABLE lava_corpus.classification_results (
    run_id          INT NOT NULL REFERENCES classification_runs(id),
    content_sha256  TEXT NOT NULL,
    material_type   TEXT,
    confidence      REAL,
    reasoning       TEXT,
    classified_by   TEXT,           -- 'rule:{name}' or 'llm:{model}'
    PRIMARY KEY (run_id, content_sha256)
);
```

The `v3_*` columns on corpus are a denormalized cache of the latest promoted run — updated by `promote_classification_run --run-tag v3.2` after validation. The `classification_results` table holds every run's output permanently.

**Workflow**: run → compare against baseline and prior runs → promote the best one.

## Resumability & Checkpointing

Both long-running commands must survive interrupts:

**`extract_classification_context`**: Already idempotent (AC3). On restart, the `LEFT JOIN … WHERE cc.content_sha256 IS NULL` query skips completed rows. No checkpoint file needed.

**`reclassify_corpus`**: Uses keyset pagination (cursor-based, ordered by `content_sha256`). After each batch of 500, the last processed SHA256 is recorded on the Job record's `config_json.cursor` field. On restart with `--resume`, the command reads the cursor from the most recent incomplete Job with the same `--run-tag` and continues from there. Completed rows (already in `classification_results` for this run_id) are skipped via `LEFT JOIN … IS NULL`.

## Validation & Promotion Criteria

The new classifier is considered "validated" when:

1. **Manual spot-check**: Operator reviews 100 random changed classifications (stratified by material_type), approves ≥ 90% of changes as correct.
2. **Confidence lift**: Average confidence increases (measured by `compare_classifications`).
3. **No regression in high-confidence docs**: Docs previously classified with confidence ≥ 0.95 should not change type unless the change is clearly correct (e.g., 990 → not_relevant).
4. **Rule accuracy**: 100% of rule-matched classifications are spot-checked on first run (expected ~15K, small enough for statistical sampling).

Once validated, run `promote_classification_run --run-tag <tag>` to update the canonical columns.

## PDF Edge Cases

| Case | Behavior |
|---|---|
| Encrypted PDF (pypdf raises) | Log warning, write `classification_context` row with `pages_text = ''`, `extraction_method = 'failed:encrypted'` |
| Corrupt PDF (pypdf raises) | Same as encrypted: empty text, `extraction_method = 'failed:corrupt'` |
| Image-only PDF (pypdf returns empty text) | Write row with `pages_text = ''`, `extraction_method = 'pypdf:empty'`. Future OCR pass can backfill. |
| Oversized PDF (> 100 MB) | Skip download, log warning, write `extraction_method = 'skipped:oversized'` |
| Missing from S3 | Log warning, skip. No `classification_context` row written — will be retried on next run. |
| Non-PDF content_type | Filter query: only process rows where `content_type = 'application/pdf'` |

During reclassification, rows with failed/empty extraction fall back to `corpus.first_page_text`. If both `pages_text` and `first_page_text` are empty or < 50 chars, the document is classified as `other_collateral` with `confidence = 0.1` and `classified_by = 'rule:insufficient_text'` — no LLM call is made (sending blank text produces hallucinated classifications).

## Rule Matching Semantics

All `text_contains_any` matching is:
- **Case-insensitive** (lower-cased before comparison)
- **Whitespace-normalized** (collapse runs of whitespace to single space)
- **Apostrophe-normalized** (straight `'` and curly `'` `'` treated as equivalent)
- **Substring match** (not word-boundary — "Form 990" matches "IRS Form 990-EZ")

`url_matches` uses Python `re.search` with `re.IGNORECASE`. Patterns are validated at YAML load time — invalid regex raises startup error.

`text_not_contains` is a safety guard, not a primary classifier. The `audited_financials_text` rule's `text_not_contains: ["Annual Report"]` prevents misclassifying an annual report that happens to include the words "Independent Auditor" — but the rule is conservative by design. If a document has both signals, it falls through to the LLM for a nuanced decision.

## DeepSeek Availability

The reclassification command uses exponential backoff (2s, 4s, 8s, 16s) on transient errors (HTTP 429, 5xx, connection timeout). After 4 consecutive failures for the same document, it marks the row as `classified_by = 'llm:error'` with `confidence = 0` and continues. After 20 consecutive failures across documents, the command halts and records the cursor for resume.

Model can be overridden at runtime: `--backend haiku` switches to Claude Haiku for the LLM portion. Cost increases ~10x but avoids DeepSeek dependency.

## Concurrency Safety

Both commands are designed for **single-process operation**. The extraction command uses `SELECT pg_advisory_lock(hashtext('extract-context'))` to prevent concurrent runs. The reclassification command uses a run-tag-specific advisory lock. Running multiple workers within a single process (ThreadPoolExecutor) is fine; multiple processes is not supported.

## Testing Strategy

### Unit Tests
- `RuleEngine`: test each condition type (text_contains_any, url_matches, pdf_creator_any, text_not_contains), case/whitespace normalization, priority ordering, no-match fallback
- `RuleEngine` YAML validation: reject invalid regex, missing required fields, unknown condition types
- Prompt assembly: verify metadata in `<document_metadata>`, text in `<untrusted_document>`, fallback to first_page_text
- Page text concatenation: verify markers, cap enforcement, edge cases (1 page, 0 pages, > 5 pages)

### Integration Tests
- Extraction: fixture PDFs (normal, encrypted, image-only, corrupt, oversized) → verify `classification_context` rows
- Rule pre-filter end-to-end: sample 990 PDF → rule match → correct classification
- LLM classification: mock DeepSeek response → verify result written to `classification_results`
- Comparison: two runs with known differences → verify migration matrix output

### Validation Fixtures
- 20 hand-labeled PDFs spanning: 990, audited financials, annual report, impact report, cover-page-only, newsletter, not_relevant
- Used for regression testing on prompt changes

## Security

### Threat Model
The system ingests adversary-controlled PDFs from the open internet. Threat actors:
- **External**: Any nonprofit website that publishes a PDF (the entire input domain). Attacker goals: suppress their documents from classification, inject prompt instructions, DoS the pipeline.
- **Insider**: PM with write access to `prefilter_rules.yaml` (checked into git, requires PR review).
- **Operator**: Anyone who can run `promote_classification_run` (requires explicit `--confirm` flag, logged to audit).
- **Supply chain**: Compromised S3 object, compromised LLM endpoint, compromised pypdf.

### Mitigations

- **YAML parsing**: Use `yaml.safe_load` exclusively. No custom constructors. Missing/unreadable rules file aborts startup (fail closed).
- **Regex safety**: Use the `regex` package (not stdlib `re`) with per-match timeout of 1 second. Reject rules at load time if regex contains nested quantifiers `(.*)+` or fails a 100ms test against a 5KB adversarial string. URL inputs capped at 2,048 chars before regex evaluation.
- **Rule suppression auditing**: 5% of docs classified as `not_relevant` by rules are randomly sampled through the LLM. Disagreements logged. Per-source-domain suppression rates > 50% trigger a warning. All rule matches record the rules YAML SHA256 hash in provenance.
- **pdf_creator is untrusted**: Never used as a sole classification signal. `irs_990_creator_plus_text` rule requires BOTH a known creator string AND 990-specific text markers. In the prompt, `pdf_creator` is placed inside `<untrusted_document>` tags (not metadata), sanitized (strip angle brackets, control chars, cap 100 chars).
- **pypdf sandboxing**: Each PDF extraction runs in a `multiprocessing.Process` with 30s timeout and 1 GB RLIMIT_AS. No decryption attempted (even empty passwords). Pin pypdf to a known-good version; update on CVE alerts.
- **SHA256 verification**: After S3 download, recompute SHA256 from bytes. Reject and alert on mismatch (TOCTOU defense).
- **Prompt injection defense**: Document text sanitized before prompt assembly — strip XML-like tag patterns (`<...>`, `</...>`). Page boundary markers include a per-request random nonce to distinguish from attacker-planted markers. Metadata fields with attacker influence (`pdf_creator`) placed in untrusted section.
- **URL path extraction**: `urllib.parse.urlparse` with scheme allowlist (http, https only). Only `.path` component used. Path sanitized (strip angle brackets, control chars). Anything that doesn't parse → empty string.
- **Log scrubbing**: Extraction error logs include only SHA256, page count, and error type — never document text excerpts.
- **Reasoning PII**: LLM reasoning capped at 500 chars. Not included in `compare_classifications` stdout unless `--show-reasoning` flag is passed.
- **Access control**: `prefilter_rules.yaml` is committed to git (requires PR review for changes). `promote_classification_run` requires `--confirm` flag and logs to `classification_runs.notes`. Run tags are namespaced to include operator identity.
- **Data residency**: DeepSeek API is used for classification. All corpus documents are publicly published PDFs from nonprofit websites. This data-flow is acceptable for public documents. If non-public corpus is ever added, this assumption must be revisited. DeepSeek's data retention policy should be reviewed before first production run.
- **`classification_context.pages_text` is internal-only**: Not exposed via any dashboard endpoint or API. If ever surfaced, must be HTML-escaped on render.

## Open Questions

1. After validation, should we promote `v3_*` columns in-place or add a `classification_version` column? In-place is simpler; version column enables permanent A/B.

## Traps to Avoid

1. **Don't re-extract from S3 on every classifier run.** The whole point of `classification_context` is to extract once and iterate on the prompt cheaply.
2. **Don't overwrite existing classifications.** Write to `v3_*` columns until the new classifier is validated. The comparison command is useless if we destroy the baseline.
3. **Don't send metadata inside `<untrusted_document>` tags.** Metadata is trusted system data; document text is untrusted. Keep them in separate XML elements.
4. **Don't skip the pre-filter for "just the LLM run."** The pre-filter exists because some categories are deterministic — sending a 990 to the LLM wastes money and introduces uncertainty where none exists.
5. **Don't hardcode rules in Python.** Rules in YAML means the PM can tune classification without a code deploy.

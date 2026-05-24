# Plan 0049: Statistical NLP Extraction & Sub-Archetype Discovery

**Spec**: `locard/specs/0049-nlp-extraction-archetypes.md`
**Protocol**: SPIDER
**Estimated phases**: 5 (Schema, Extraction, Scoring, Discovery, Integration)

---

## Overview

This plan implements a 3-step statistical NLP pipeline for extracting domain vocabulary from Docling-parsed nonprofit documents and discovering organizational sub-archetypes within NTEE verticals. The pipeline uses spaCy, TF-IDF, C-value, and FP-Growth — zero LLM, zero API cost.

All code lives in Django management commands under `lavandula/dashboard/pipeline/management/commands/` and a new library module `lavandula/nlp/` for the extraction logic. The pipeline reads from `lava_parse` (Spec 0046 output) and writes to a new `lava_vocab` schema.

**Key patterns to follow:**
- Management commands: follow `reclassify_corpus.py` style (BaseCommand, add_arguments, SQLAlchemy via `lavandula.common.db`)
- DB access: parameterized SQL via SQLAlchemy `text()` — no ORM models for `lava_vocab`
- Schema DDL: raw SQL migration file (no Django migrations — same pattern as `lava_parse`)
- Resume/checkpoint: cursor-based (see `parse_documents.py` pattern)

---

## Dependencies (pip)

```
spacy>=3.7,<4.0
scikit-learn>=1.3
mlxtend>=0.23
scipy>=1.11
```

Model: `python -m spacy download en_core_web_lg`

These are added to `requirements.txt` (or equivalent). The builder must verify these don't conflict with existing deps.

---

## Phase A: Schema & Infrastructure

### A1. Schema DDL file

**File**: `lavandula/migrations/lava_vocab/001_create_schema.sql`

Create the full `lava_vocab` schema exactly as specified in the spec (Section: Technical Implementation > Schema). Copy the DDL verbatim — the spec IS the source of truth for schema definition.

Include all 8 tables, all indexes, and the GRANT statements. The file must be idempotent (`CREATE SCHEMA IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS`).

### A2. NLP library module

**Directory**: `lavandula/nlp/`

```
lavandula/nlp/__init__.py
lavandula/nlp/extractor.py      # spaCy + C-value extraction logic
lavandula/nlp/keyness.py        # TF-IDF scoring logic
lavandula/nlp/archetypes.py     # FP-Growth + clustering
lavandula/nlp/stopwords.py      # Boilerplate term lists
lavandula/nlp/canonicalize.py   # Term canonicalization per spec policy
```

This module is pure Python — no Django dependency. It can be tested independently.

### A3. Stopword/boilerplate list

**File**: `lavandula/nlp/stopwords.py`

Define `BOILERPLATE_TERMS` frozenset containing the spec's lists:
- Generic nonprofit: "community", "mission", "impact", "stakeholders", "board of directors", "fiscal year", "annual report", "strategic plan"
- Financial/IRS: "form 990", "tax-exempt", "gross receipts", "net assets"

Also define `ENTITY_LABELS_KEEP = frozenset({"ORG", "PRODUCT", "EVENT", "WORK_OF_ART"})`.

### A4. Canonicalization module

**File**: `lavandula/nlp/canonicalize.py`

Implement `canonicalize_term(span_or_text, nlp, is_entity=False) -> tuple[str, str]` returning `(canonical, raw)`.

Rules (from spec Section: Design Principles > Term canonicalization policy):
1. Lowercase all tokens
2. Lemmatize each token via spaCy (skip lemmatization for named entities)
3. Join with single space
4. Strip leading/trailing whitespace
5. Collapse internal whitespace
6. Preserve hyphens ("trauma-informed" stays)
7. Strip possessives ("children's" → "child")
8. Plurals lemmatized ("food pantries" → "food pantry")

### A5. Tests for Phase A

- Unit test: `canonicalize_term` handles all cases (hyphen, possessive, plural, entity)
- Unit test: boilerplate frozenset contains expected terms
- Schema DDL: run against test DB, verify all tables exist

---

## Phase B: Term Extraction (`extract_terms`)

### B1. Extractor core logic

**File**: `lavandula/nlp/extractor.py`

```python
class SectionExtractor:
    """Extracts terms from a single section's body_text."""
    
    def __init__(self, nlp, boilerplate: frozenset):
        ...
    
    def extract(self, body_text: str, section_index: int, 
                heading: str, parent_headings: list[str]) -> list[Observation]:
        """Returns list of Observation namedtuples."""
        ...
```

**Per-section extraction pipeline:**
1. Guard: skip if `len(body_text) < min_section_chars`
2. Truncate to 100K chars if exceeding (log warning)
3. Run `nlp(body_text)` to get spaCy Doc
4. Extract noun phrases from `doc.noun_chunks` (multi-word only)
5. Extract named entities matching `ENTITY_LABELS_KEEP`
6. Extract POS-pattern n-grams: (ADJ NOUN), (NOUN NOUN), (ADJ NOUN NOUN)
7. Canonicalize all terms
8. Filter: remove single-word non-entities, remove boilerplate terms
9. Deduplicate by canonical form, sum frequencies
10. Return observations with: term, term_raw, term_type, pos_pattern, frequency, section_index, heading, parent_headings

**C-value computation** (per-document, across all sections):
```python
def compute_cvalue(observations: list[Observation], min_score: float = 1.0) -> list[Observation]:
    """C-value for multi-word candidates (2-5 words).
    
    Formula: log2(|a|) * f(a) for non-nested terms, adjusted for nested.
    Returns observations with term_type='cvalue'.
    """
```

The C-value algorithm:
1. Collect all candidate terms (2-5 word noun-phrase POS patterns)
2. Sort by length descending
3. For each term `a`:
   - If `a` is not nested in any longer term: `cvalue = log2(len(a)) * freq(a)`
   - If `a` is nested: `cvalue = log2(len(a)) * (freq(a) - (1/P(T_a)) * sum_freq(terms containing a))`
   where `P(T_a)` = number of longer terms containing `a`
4. Keep terms with cvalue > `min_score`

### B2. Management command

**File**: `lavandula/dashboard/pipeline/management/commands/extract_terms.py`

Follow the same structure as `reclassify_corpus.py`:
- `BaseCommand` subclass
- `add_arguments`: all CLI args from spec (run_tag, --ntee, --material, --workers, --batch-size, --min-section-chars, --dry-run, --resume)
- Input validation at entry (run_tag regex, ntee regex, material types against DB)
- Uses `make_app_engine()` from `lavandula.common.db`

**Main loop:**
```
1. Validate inputs (exit early on invalid)
2. Create/resume extraction_run record
3. Query eligible docs: JOIN lava_parse.documents + lava_corpus.corpus 
   WHERE classification = ANY(material) AND ntee matches
   AND content_sha256 NOT IN (SELECT content_sha256 FROM lava_vocab.doc_extractions WHERE run_id = ?)
4. If --dry-run: print count and exit
5. Load spaCy model (en_core_web_lg) — ONCE, shared across workers
6. Process in batches:
   a. Fetch batch of docs (content_sha256 list)
   b. For each doc: fetch its sections from lava_parse.sections
   c. Extract terms per section (SectionExtractor)
   d. Compute C-value across all sections for the doc
   e. Filter >80% frequency terms (against docs processed so far in this run)
   f. INSERT observations + doc_extractions record
   g. Update cursor in extraction_runs.config_json
   h. Check circuit breaker (>20% error rate → pause)
7. Update extraction_runs.stats_json and finished_at
```

**Concurrency model:** `--workers` controls `concurrent.futures.ProcessPoolExecutor` for CPU-bound spaCy processing. Each worker gets its own spaCy `nlp` instance (loaded per-process). DB writes are serialized in the main process after workers return results.

**Why ProcessPoolExecutor not ThreadPoolExecutor:** spaCy is CPU-bound and holds the GIL during processing. Multiple processes give true parallelism. The main thread collects results and does batch DB writes.

**Memory note from spec:** On t3.large (8 GiB), default `--workers 2`. Each process loads ~1.5 GiB for spaCy. The command should check available memory at startup and warn if `workers * 1.5 GiB > 75% available RAM`.

### B3. 80% frequency filter

The spec says terms in >80% of documents are excluded. This requires knowing the total doc count for the vertical in this run.

**Implementation:** After all extraction is complete (or in a post-pass), compute per-term document frequency. Delete observations where `doc_frequency / total_docs > 0.80`. This is a cleanup step after the main extraction, not during — because we don't know the denominator until extraction finishes.

Alternative: maintain a running count and apply the filter at scoring time (Step 2). **Chosen approach:** defer to Step 2. Store all observations during extraction, then apply the 80% filter during `score_keyness`. This avoids a second pass over observations and keeps extraction idempotent.

**Rationale:** The spec says "Terms appearing in >80% of documents are excluded (computed per-vertical)". The per-vertical denominator is only known after extraction completes. Scoring is the natural place to apply this filter since it already computes document frequencies.

### B4. Tests for Phase B

- Unit test: `SectionExtractor` on known text → expected noun phrases, entities, n-grams
- Unit test: `compute_cvalue` on known frequency data → expected scores
- Unit test: canonicalization pipeline end-to-end
- Unit test: 100K char truncation fires
- Unit test: circuit breaker triggers at >20% error rate
- Unit test: `--dry-run` produces no DB writes
- Integration test: known sections in `lava_parse.sections` → observations appear in `lava_vocab.observations`

---

## Phase C: Keyness Scoring (`score_keyness`)

### C1. Keyness computation logic

**File**: `lavandula/nlp/keyness.py`

```python
def compute_tfidf_scores(
    engine, run_id: int, ntee_prefix: str, min_docs: int = 3
) -> list[dict]:
    """Compute TF-IDF within-vertical and cross-vertical keyness.
    
    Returns list of score dicts ready for DB insertion.
    """
```

**Algorithm:**
1. Build document-term matrix for target vertical:
   - Query: all observations WHERE run_id AND source_org_ein in orgs matching ntee_prefix
   - Binary presence (1 if term appears in doc, 0 otherwise)
   - Filter: only terms appearing in >= `min_docs` documents
   - **Also filter:** exclude terms where doc_frequency > 80% of vertical docs (the deferred filter from Phase B)
2. Compute TF-IDF using `sklearn.feature_extraction.text.TfidfTransformer` on the binary matrix
3. Compute corpus-wide doc frequency: count distinct content_sha256 per term across ALL ntee prefixes in this run
4. Compute keyness ratio: `tfidf_within / tfidf_corpus` (NULL if only one vertical)
5. Return score rows for insertion into `lava_vocab.tfidf_scores`

```python
def aggregate_cvalue_scores(
    engine, run_id: int, ntee_prefix: str
) -> list[dict]:
    """Aggregate C-value across docs in the vertical. Compute NC-value."""
```

**NC-value computation:**
1. For each C-value term, find context words (words that appear adjacent to the term in observations)
2. Weight the C-value by context diversity: `NC-value = 0.8 * C-value + 0.2 * sum(context_weight * context_freq)`
3. Store in `lava_vocab.cvalue_terms`

### C2. Management command

**File**: `lavandula/dashboard/pipeline/management/commands/score_keyness.py`

```
Usage:
  python manage.py score_keyness <run_tag> [--ntee P2%] [--min-docs 3]
```

**Flow:**
1. Validate inputs (run_tag exists in extraction_runs and is finished)
2. Check if observations exist for this run (exit with message if none)
3. Determine available verticals in this run
4. Compute TF-IDF scores for target vertical
5. If multiple verticals exist: compute cross-vertical keyness
6. Aggregate C-value scores
7. Insert into `tfidf_scores` and `cvalue_terms` (UPSERT on unique constraint)
8. Print summary: term count, top 20 terms by keyness, top 20 by C-value

### C3. Tests for Phase C

- Unit test: TF-IDF computation on known document-term matrix
- Unit test: keyness ratio = NULL when only one vertical
- Unit test: min_docs filter works correctly
- Unit test: 80% frequency exclusion applied
- Unit test: NC-value computation
- Integration test: observations in DB → scores computed and stored correctly

---

## Phase D: Archetype Discovery (`discover_archetypes`)

### D1. Archetype logic

**File**: `lavandula/nlp/archetypes.py`

```python
def build_org_term_matrix(
    engine, run_id: int, ntee_prefix: str,
    min_org_count: int = 3, min_keyness: float | None = None
) -> tuple[np.ndarray, list[str], list[str]]:
    """Build binary org-term matrix.
    
    Returns (matrix, org_eins, term_labels).
    """

def find_clusters(
    matrix: np.ndarray, k: int | None = None, seed: int = 42
) -> tuple[np.ndarray, int, dict]:
    """Hierarchical clustering with auto-k via silhouette.
    
    Returns (labels, k_chosen, diagnostics_dict).
    """

def run_fp_growth(
    matrix: np.ndarray, term_labels: list[str],
    cluster_labels: np.ndarray, cluster_id: int,
    min_support: float = 0.05, min_lift: float = 1.5
) -> list[dict]:
    """FP-Growth on a single cluster's subset.
    
    Returns association rules as dicts.
    """

def compute_lift_per_term(
    matrix: np.ndarray, cluster_labels: np.ndarray,
    cluster_id: int, term_labels: list[str]
) -> list[tuple[str, float]]:
    """Per-term lift vs full population for an archetype."""

def auto_label(term_lifts: list[tuple[str, float]]) -> str:
    """Top 2 terms by lift, joined with ' + '. Alphabetical tiebreak."""
```

**Clustering details:**
- Distance: cosine (via `scipy.spatial.distance.pdist`)
- Linkage: complete (`scipy.cluster.hierarchy.linkage`)
- Auto-k: iterate k=2..min(20, N/3), compute silhouette score (`sklearn.metrics.silhouette_score`)
- If silhouette flat (max - min < 0.05): default k=5, log warning
- `--k` flag overrides auto-selection
- `fcluster` to assign labels

### D2. Management command

**File**: `lavandula/dashboard/pipeline/management/commands/discover_archetypes.py`

```
Usage:
  python manage.py discover_archetypes <run_tag> [options]
  
Options:
  --ntee P2%
  --min-support 0.05
  --min-lift 1.5
  --min-keyness (optional, float)
  --k (int or 'auto', default: auto)
  --min-org-count 3
  --seed 42
```

**Flow:**
1. Validate inputs (run_tag exists, tfidf_scores populated for this vertical)
2. Build org-term matrix
3. Run hierarchical clustering
4. For each cluster:
   a. Run FP-Growth → association rules
   b. Compute per-term lift
   c. Auto-label (top 2 terms by lift)
5. Store in `archetypes`, `archetype_members`, `association_rules`
6. Print summary to stdout:
   ```
   === Archetype Discovery: run_tag (P2%) ===
   k=6 (silhouette: 0.34)
   
   [1] food_pantry + nutrition (47 orgs)
       Top terms: food_pantry (lift 6.1), nutrition_program (5.8), ...
       Example orgs: 12-3456789, 98-7654321, ...
   
   [2] early_childhood + head_start (31 orgs)
       ...
   ```

### D3. Tests for Phase D

- Unit test: `build_org_term_matrix` filters by min_org_count and min_keyness
- Unit test: `find_clusters` with known matrix → deterministic clusters (fixed seed)
- Unit test: silhouette auto-k selection
- Unit test: flat silhouette → defaults to k=5
- Unit test: `run_fp_growth` with known binary data → expected rules
- Unit test: `compute_lift_per_term` arithmetic correct
- Unit test: `auto_label` alphabetical tiebreak
- Integration test: full pipeline observations → scores → archetypes in DB

---

## Phase E: Integration & Pilot Validation

### E1. Test fixtures

**File**: `lavandula/nlp/tests/fixtures/` (or `lavandula/dashboard/pipeline/tests/`)

Create 10-20 synthetic `lava_parse.sections` rows representing known P20 annual reports. Use real text snippets from sample PDFs we have in `sample_pdfs/`. This allows testing the full pipeline without waiting for Docling to run.

### E2. End-to-end integration test

A single test that:
1. Creates fixture sections in `lava_parse`
2. Runs `extract_terms` on them
3. Runs `score_keyness`
4. Runs `discover_archetypes`
5. Asserts: observations exist, scores computed, archetypes created, rules have lift > 1.0
6. Cleans up (drops test run data)

### E3. Requirements file update

Add to `requirements.txt` (or the project's equivalent):
```
spacy>=3.7,<4.0
scikit-learn>=1.3
mlxtend>=0.23
scipy>=1.11
```

Document in a README or inline comment: `python -m spacy download en_core_web_lg` is required before first run.

### E4. Schema deployment script

**File**: `lavandula/migrations/lava_vocab/apply.sh`

```bash
#!/bin/bash
# Apply lava_vocab schema to RDS
psql "$DATABASE_URL" -f lavandula/migrations/lava_vocab/001_create_schema.sql
```

Simple. No rollback — schema is additive and isolated.

---

## File Summary

| File | Purpose |
|------|---------|
| `lavandula/migrations/lava_vocab/001_create_schema.sql` | Schema DDL |
| `lavandula/migrations/lava_vocab/apply.sh` | Deployment script |
| `lavandula/nlp/__init__.py` | Package init |
| `lavandula/nlp/extractor.py` | spaCy extraction + C-value |
| `lavandula/nlp/keyness.py` | TF-IDF scoring |
| `lavandula/nlp/archetypes.py` | FP-Growth + clustering |
| `lavandula/nlp/stopwords.py` | Boilerplate term lists |
| `lavandula/nlp/canonicalize.py` | Term normalization |
| `lavandula/dashboard/pipeline/management/commands/extract_terms.py` | Management command |
| `lavandula/dashboard/pipeline/management/commands/score_keyness.py` | Management command |
| `lavandula/dashboard/pipeline/management/commands/discover_archetypes.py` | Management command |
| `lavandula/nlp/tests/test_extractor.py` | Extraction unit tests |
| `lavandula/nlp/tests/test_keyness.py` | Scoring unit tests |
| `lavandula/nlp/tests/test_archetypes.py` | Discovery unit tests |
| `lavandula/nlp/tests/test_canonicalize.py` | Canonicalization tests |
| `lavandula/nlp/tests/test_integration.py` | End-to-end test |

---

## Build Order & Dependencies

```
Phase A (no dependencies)
    ↓
Phase B (depends on A: schema + extractor module)
    ↓
Phase C (depends on B: observations must exist)
    ↓
Phase D (depends on C: scores must exist)
    ↓
Phase E (integration across all phases)
```

Phases A and the test infrastructure can be built in parallel. Phases B-D are sequential due to data dependencies.

---

## Acceptance Criteria Mapping

| AC | Phase | Verified By |
|----|-------|-------------|
| AC1-3 | A | Schema DDL review + test |
| AC4-12a | B | Unit + integration tests |
| AC13-16 | C | Unit + integration tests |
| AC17-24 | D | Unit + integration tests |
| AC25-29 | B,C,D | Operational checks in integration test |

---

## Operational Notes

- **Host**: cloud2 (t3.large, 8 GiB). Default `--workers 2`.
- **No systemd service**: these are manual batch commands, not daemons.
- **Pilot sequence**: extract P20 first (1.4K docs, ~1-2 hours). Then a contrasting vertical for keyness validation.
- **Monitoring**: `extraction_runs.stats_json` provides progress. `SELECT * FROM lava_vocab.extraction_runs WHERE finished_at IS NULL` shows active runs.
- **Disk**: observations for 44K docs ≈ 220 MiB. Well within RDS limits.
- **No interaction with existing crawl/enrich pipeline**: completely isolated schema and commands.

---

## Traps to Avoid (from spec — non-negotiable)

1. **No LLM calls anywhere in this pipeline.** Statistical methods only.
2. **No pre-defined categories.** Let FP-Growth discover clusters.
3. **No aggressive normalization.** Keep "food pantry" ≠ "food bank".
4. **No skipping C-value.** TF-IDF alone misses multi-word phrases.
5. **No cross-vertical dependency to start.** Pipeline works with one vertical.
6. **No dashboard/UI.** Output = DB tables + stdout.
7. **No embeddings/vectors.** Raw co-occurrence only.
8. **No merging with Layer 1 tables.** Clean schema separation.

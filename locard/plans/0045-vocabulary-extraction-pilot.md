# Plan 0045: Vocabulary Extraction & Archetype Discovery — Pilot

**Spec:** locard/specs/0045-vocabulary-extraction-pilot.md
**Protocol:** SPIDER
**Estimated Effort:** ~6-8 hours builder time

## Overview

Build two Django management commands (`extract_vocabulary`, `analyze_vocabulary`) and a new PostgreSQL schema (`lava_vocab`) for vocabulary extraction and archetype discovery. Scoped to P20 Human Services annual/impact reports (~1,400 docs). Uses the existing `DeepSeekAPIClient` for extraction and `mlxtend` for Market Basket Analysis.

## Dependencies

**Python packages to install:**
- `mlxtend` (FP-Growth, association rules)
- `scikit-learn` (silhouette score for auto-k selection)
- `scipy` (hierarchical clustering — likely already installed)
- `numpy` (matrix ops — likely already installed)

**Database:** RDS access via Django ORM + raw SQL for schema creation.

**Existing code reused:**
- `lavandula/reports/classifier_clients.py` → `DeepSeekAPIClient`
- `lavandula/common/secrets.py` → `get_secret`
- Resume/checkpoint pattern from `reclassify_corpus.py`

## File Layout

```
lavandula/
  vocab/
    __init__.py
    prompts/
      v1_extract.txt              # Extraction prompt (versioned)
    extraction.py                 # Core extraction logic (DeepSeek call, parsing, validation)
    analysis.py                   # Core analysis logic (clustering, FP-Growth, lift)
    filters.py                    # 990 pre-filter, term validation, dedup
  dashboard/
    pipeline/
      management/
        commands/
          extract_vocabulary.py   # Django management command
          analyze_vocabulary.py   # Django management command
  migrations/
    vocab/
      001_create_lava_vocab_schema.sql   # DDL for all 5 tables + indexes
```

## Implementation Phases

### Phase 1: Schema & Infrastructure (1 hour)

**Goal:** Create `lava_vocab` schema, install dependencies, set up module structure.

**Steps:**

1.1. Write SQL migration `lavandula/migrations/vocab/001_create_lava_vocab_schema.sql`:
   - `CREATE SCHEMA IF NOT EXISTS lava_vocab`
   - All 5 tables exactly as specified
   - All indexes
   - `GRANT USAGE ON SCHEMA lava_vocab TO research_app`
   - `GRANT ALL ON ALL TABLES IN SCHEMA lava_vocab TO research_app`
   - `GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA lava_vocab TO research_app`

1.2. Create module structure:
   - `lavandula/vocab/__init__.py` (empty)
   - `lavandula/vocab/prompts/` directory
   - `lavandula/vocab/prompts/v1_extract.txt` (extraction prompt from spec)

1.3. Install Python dependencies on cloud2:
   - `pip3 install --user mlxtend scikit-learn`
   - Verify: `python3 -c "from mlxtend.frequent_patterns import fpgrowth; print('ok')"`

1.4. Apply migration to RDS:
   - `psql` with research_app credentials
   - Run the DDL script
   - Verify: `\dt lava_vocab.*`

**Acceptance:** Schema exists, prompt file exists, imports work.

### Phase 2: Extraction Core (2 hours)

**Goal:** Build the extraction logic — DeepSeek call, response parsing, validation, observation storage.

**Steps:**

2.1. Write `lavandula/vocab/filters.py`:

```python
import re
import hashlib

_RUN_TAG_RE = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')
_NTEE_RE = re.compile(r'^[A-Z][0-9]*%?$')
_VALID_CATEGORIES = frozenset(['stakeholder', 'metric', 'outcome', 'program', 'methodology'])
_990_MARKERS = [
    "return of organization exempt from income tax",
    "form 990",
    "department of the treasury",
    "internal revenue service",
]
MAX_OBS_PER_DOC = 100
MAX_TEXT_LEN = 32_000

def validate_run_tag(tag: str) -> str: ...
def validate_ntee(ntee: str) -> str: ...
def is_990(pages_text: str) -> bool: ...
def validate_observation(obs: dict) -> dict | None: ...
def dedup_observations(observations: list[dict]) -> list[dict]: ...
def truncate_text(pages_text: str) -> str: ...
def prompt_sha256(prompt_path: Path) -> str: ...
```

Key validations:
- `validate_observation`: check term length (3-60), confidence ≥ 0.5, category in allowed set, evidence_span truncated to 150 chars
- `dedup_observations`: group by (content_sha256, term), keep highest confidence
- `truncate_text`: cap at MAX_TEXT_LEN chars, log warning if truncated

2.2. Write `lavandula/vocab/extraction.py`:

```python
def build_extraction_prompt(org_name, ntee_code, state, pages_text, prompt_path) -> str: ...
def extract_one(client, prompt: str) -> list[dict]: ...
def parse_response(raw: str) -> list[dict]: ...
```

- `build_extraction_prompt`: reads prompt template from file, formats with org metadata and pages_text
- `extract_one`: calls `DeepSeekAPIClient._call_api()` directly (no tool-use schema needed — we want raw JSON output)
- `parse_response`: strips code fences, parses JSON array, validates each observation

**Important design decision:** Unlike classification (which uses the Anthropic SDK duck-type with tool_choice), extraction just needs a raw JSON array response. Use `DeepSeekAPIClient._call_api()` directly with `max_tokens=2048` (extraction responses will be longer than classification — 20-40 observations × ~30 tokens each).

2.3. Write unit tests:
- `test_filters.py`: 990 detection, term validation, dedup, run_tag validation
- `test_extraction.py`: prompt building, response parsing (valid JSON, malformed JSON, code-fenced JSON, empty array, oversized array)

**Acceptance:** AC5, AC8, AC9, AC10, AC28, AC29 testable via unit tests.

### Phase 3: Extract Command (2 hours)

**Goal:** Django management command that orchestrates extraction across the eligible document set.

**Steps:**

3.1. Write `extract_vocabulary.py` management command:

Structure follows `reclassify_corpus.py` pattern:
- `add_arguments()`: all CLI args from spec
- `handle()`: acquire advisory lock, call `_run()`
- `_run()`: init run record, fetch eligible docs, process in batches
- `_process_batch()`: concurrent workers via ThreadPoolExecutor
- `_extract_one()`: call extraction.extract_one, validate, store

Key implementation details:
- Advisory lock keyed on `f"vocab-extract-{run_tag}"` (prevent duplicate runs)
- Eligible doc query as specified (JOIN corpus + classification_context + nonprofits_seed)
- Resume: store last `content_sha256` in `config_json.cursor`; on resume, add `AND c.content_sha256 > :cursor` with same ORDER BY
- Rate limiting: sliding window per worker (same as reclassify_corpus)
- Dry-run: execute count query + 990 pre-filter estimate, print count and cost, exit
- Stats tracking: `stats_json = {total, extracted, skipped_990, errors: [], error_count}`
- Progress: emit to fd 3 via `emit_progress()` for dashboard integration

3.2. Wire up database operations:
- Use Django's `connections['default'].cursor()` for raw SQL INSERT (observations table is not a Django model — it's in a separate schema)
- Batch INSERT using `execute_values` or `executemany` for performance (100 observations at a time)

3.3. Write integration test:
- Mock DeepSeekAPIClient, verify observations land in database
- Test `--resume` continues from cursor without duplicates
- Test `--dry-run` writes nothing

**Acceptance:** AC4, AC6, AC7, AC11, AC12, AC13, AC14, AC15, AC30, AC31.

### Phase 4: Analysis Core (1.5 hours)

**Goal:** Build the analysis logic — term matrix, clustering, FP-Growth, lift scoring.

**Steps:**

4.1. Write `lavandula/vocab/analysis.py`:

```python
def build_term_matrix(observations, min_org_count=3) -> tuple[np.ndarray, list[str], list[str]]:
    """Returns (binary_matrix, org_eins, term_list)"""
    ...

def cluster_orgs(matrix, k='auto', seed=42) -> tuple[np.ndarray, int]:
    """Returns (labels, selected_k)"""
    # Complete linkage on cosine distance
    # Auto-k via silhouette score (range 4-20)
    ...

def compute_lift(matrix, labels, terms) -> dict[int, list[tuple[str, float]]]:
    """Per-cluster lift for each term relative to full population."""
    ...

def run_fpgrowth_per_cluster(matrix, labels, orgs, terms, min_support=0.05, min_lift=1.5):
    """Returns dict[cluster_id, DataFrame of rules]"""
    ...

def label_archetypes(lift_scores: dict) -> dict[int, str]:
    """Auto-label: top 2 terms by lift joined with ' + '"""
    ...
```

4.2. Key implementation notes:
- `build_term_matrix`: group observations by `source_org_ein`, create binary term presence matrix, filter terms below min_org_count
- `cluster_orgs`: use `scipy.spatial.distance.pdist(matrix, 'cosine')` → `scipy.cluster.hierarchy.linkage(distances, method='complete')` → `fcluster(Z, t=k, criterion='maxclust')`. For auto-k, iterate k=4..20, compute silhouette score, pick max.
- `run_fpgrowth_per_cluster`: for each cluster, subset matrix, convert to DataFrame, run `mlxtend.frequent_patterns.fpgrowth`, then `association_rules` with metric='lift'
- Deterministic: `np.random.seed(seed)` at start (silhouette tie-breaking)

4.3. Write unit tests:
- `test_analysis.py`: fixed 10-org × 8-term matrix → verify clustering is deterministic with same seed, verify lift calculation matches manual computation, verify FP-Growth output format

**Acceptance:** AC16, AC17, AC18, AC19, AC20.

### Phase 5: Analyze Command (1 hour)

**Goal:** Django management command that runs analysis on stored observations and writes results to archetype tables.

**Steps:**

5.1. Write `analyze_vocabulary.py` management command:

- `add_arguments()`: run_tag, --min-support, --min-lift, --k, --min-org-count, --seed
- `handle()`:
  1. Load observations for the given run_tag from `lava_vocab.observations`
  2. Call `build_term_matrix()` → cluster → FP-Growth → lift
  3. Store results in `archetypes`, `archetype_members`, `association_rules`
  4. Print summary report

5.2. Summary report format (stdout):

```
=== Vocabulary Analysis: {run_tag} ===
Observations: {N} from {M} orgs
Terms (after min_org_count filter): {T}
Clusters (k={k}, method=complete_cosine): {k}

--- Archetype 1: {label} ({org_count} orgs) ---
  Top terms by lift:
    {term1:<30} lift={lift:.2f}
    {term2:<30} lift={lift:.2f}
    ...
  Top association rules:
    {ant} → {con}  (support={s:.2f}, confidence={c:.2f}, lift={l:.2f})
  Example orgs:
    - {org_name_1}
    - {org_name_2}
    - {org_name_3}

--- Archetype 2: ... ---
...

=== Quality Metrics ===
  Avg terms/org: {avg}
  Archetypes with 3+ orgs: {count}
  Archetypes with 3+ high-lift terms: {count}
```

5.3. Write integration test:
- Seed observations table with known data
- Run analyze_vocabulary
- Verify archetypes, members, and rules written correctly

**Acceptance:** AC21, AC22.

### Phase 6: End-to-End Validation (0.5 hours)

**Goal:** Run the full pipeline on a small subset (50 docs) to validate everything works end-to-end before the full pilot.

**Steps:**

6.1. Run dry-run to verify eligible count:
```bash
python manage.py extract_vocabulary smoke-test-50 --ntee P2% --dry-run --sample 50
```

6.2. Run extraction on 50 docs:
```bash
python manage.py extract_vocabulary smoke-test-50 --ntee P2% --sample 50 --workers 2
```

6.3. Verify observations in database:
```sql
SELECT count(*), avg(confidence), count(DISTINCT source_org_ein)
FROM lava_vocab.observations WHERE run_id = (SELECT id FROM lava_vocab.extraction_runs WHERE run_tag = 'smoke-test-50');
```

6.4. Run analysis:
```bash
python manage.py analyze_vocabulary smoke-test-50 --min-org-count 2 --k 4
```

6.5. Verify output looks reasonable (archetypes make sense, lift scores > 1.5 exist).

6.6. If smoke test passes, ready for full pilot run:
```bash
python manage.py extract_vocabulary P20-pilot-v1 --ntee P2% --workers 4
python manage.py analyze_vocabulary P20-pilot-v1
```

**Acceptance:** Full pipeline executes without error on subset. Results structurally match expectations.

## Test Strategy

| Layer | Scope | Tools |
|-------|-------|-------|
| Unit | filters, extraction parsing, analysis math | pytest, no DB |
| Integration | command → DB → verify rows | pytest-django, test DB |
| Smoke | 50-doc real extraction + analysis | Real DeepSeek API, real RDS |

**Test count estimate:** ~25-30 tests across unit + integration.

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| DeepSeek returns non-JSON garbage | Strip code fences, retry once, skip + log |
| Pages_text too short for many P20 docs | `--min-text-len 500` filter; log skipped count |
| FP-Growth OOM on large term matrix | Pilot is ~1,400 docs — matrix fits easily in memory |
| Silhouette auto-k picks poor k | Override with `--k` if needed; report all k scores |
| `mlxtend` not available on cloud2 | Phase 1 installs it; verified before Phase 2 starts |

## Rollback

If anything goes wrong:
```sql
DROP SCHEMA lava_vocab CASCADE;
```
Layer 1 is completely untouched. No cleanup needed beyond removing the schema.

## Operator Steps (Post-Build)

After the builder completes and PR merges:
1. Apply migration: `psql -f lavandula/migrations/vocab/001_create_lava_vocab_schema.sql`
2. Install packages: `pip3 install --user mlxtend scikit-learn`
3. Run pilot extraction: `python manage.py extract_vocabulary P20-pilot-v1 --ntee P2%`
4. Run analysis: `python manage.py analyze_vocabulary P20-pilot-v1`
5. Review archetype report output against success metrics SM1-SM5

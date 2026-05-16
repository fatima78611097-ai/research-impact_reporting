# Plan 0045: Vocabulary Extraction & Archetype Discovery — Pilot

**Spec:** locard/specs/0045-vocabulary-extraction-pilot.md
**Protocol:** SPIDER
**Estimated Effort:** ~6-8 hours builder time

## Overview

Build two Django management commands (`extract_vocabulary`, `analyze_vocabulary`) and a new PostgreSQL schema (`lava_vocab`) for vocabulary extraction and archetype discovery. Scoped to P20 Human Services annual/impact reports (~1,400 docs). Uses the existing `DeepSeekAPIClient` for extraction and `mlxtend` for Market Basket Analysis.

## Dependencies

**Python packages to install (pinned versions):**
- `mlxtend==0.23.3` (FP-Growth, association rules)
- `scikit-learn==1.6.1` (silhouette score for auto-k selection)
- `scipy` (hierarchical clustering — likely already installed)
- `numpy` (matrix ops — likely already installed)

Version pins ensure reproducible builds and protect against supply-chain attacks. Update only after reviewing changelogs and testing.

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
   - All 5 tables exactly as specified: `extraction_runs`, `observations`, `archetypes`, `archetype_members`, `association_rules`
   - All 6 indexes as specified
   - UNIQUE constraint on `archetype_members(archetype_id, source_org_ein)`
   - No cross-schema foreign keys (only TEXT references to `content_sha256` / `source_org_ein`)
   - All timestamp columns use `TIMESTAMPTZ` (UTC)
   - `GRANT USAGE ON SCHEMA lava_vocab TO research_app`
   - `GRANT ALL ON ALL TABLES IN SCHEMA lava_vocab TO research_app`
   - `GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA lava_vocab TO research_app`

1.2. Create module structure:
   - `lavandula/vocab/__init__.py` (empty)
   - `lavandula/vocab/prompts/` directory
   - `lavandula/vocab/prompts/v1_extract.txt` (extraction prompt from spec — version-controlled static asset, not runtime-modifiable)

**Prompt file security:** Prompt paths are hardcoded constants (e.g., `Path(__file__).parent / "prompts" / "v1_extract.txt"`). The `prompt_path` parameter in `prompt_sha256()` accepts only `Path` objects resolved relative to the module directory. No user input flows into file path construction.

**Note:** Package installation (`mlxtend`, `scikit-learn`) and migration execution are **operator steps** (see bottom of plan), not builder steps. Builder writes the migration file and code; operator applies to production.

**Acceptance:** Migration SQL file exists, module structure exists, prompt file exists.

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
_VALID_MATERIALS = frozenset(['annual', 'impact'])  # V2 classification values
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
def validate_materials(materials: list[str]) -> list[str]: ...
def is_990(pages_text: str) -> bool: ...
def is_990_structured(pages_text: str) -> dict | None:
    """Returns structured 990 record or None: {type, content_sha256, source_org_ein, marker}"""
    ...
def validate_observation(obs: dict) -> dict | None: ...
def dedup_observations(observations: list[dict]) -> list[dict]: ...
def cap_observations(observations: list[dict], max_n=MAX_OBS_PER_DOC) -> list[dict]:
    """Keep top max_n by confidence. Log if capped."""
    ...
def truncate_text(pages_text: str) -> tuple[str, bool]: ...
def prompt_sha256(prompt_path: Path) -> str: ...
```

Key validations:
- `validate_run_tag`: regex match `^[a-zA-Z0-9_-]{1,64}$`, raise CommandError if invalid
- `validate_materials`: each value must be in `_VALID_MATERIALS`, raise CommandError if invalid
- `validate_observation`: check term length (3-60), confidence ≥ 0.5, category in `_VALID_CATEGORIES` (reject if not), evidence_span truncated to 150 chars
- `dedup_observations`: group by (content_sha256, term), keep highest confidence
- `cap_observations`: sort by confidence DESC, keep top 100
- `truncate_text`: cap at MAX_TEXT_LEN chars, return (text, was_truncated) for logging
- `is_990_structured`: returns `{"type": "990_contamination", "content_sha256": ..., "source_org_ein": ..., "marker": ...}` for structured logging in `stats_json.contamination_990[]`

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
- **Advisory lock:** `pg_try_advisory_lock(hashtext('vocab-extract-{run_tag}'))` — same `hashtext()` pattern as reclassify_corpus. Single integer lock via `hashtext`.
- **Eligible doc query:** as specified (JOIN corpus + classification_context + nonprofits_seed)
- **Resume:** On `--resume`, query `SELECT DISTINCT content_sha256 FROM lava_vocab.observations WHERE run_id = :run_id` to get already-processed SHAs. Add `AND c.content_sha256 NOT IN (:processed)` to eligible query. For 1,400 docs this set is small and efficient. This approach is order-independent and handles partial batch failures correctly (no duplicate observations).
- **Rate limiting:** sliding window per worker (same as reclassify_corpus)
- **Dry-run:** Execute eligible doc query (same JOINs + filters), then scan first 500 chars of each doc's `pages_text` to count 990 hits. Report: `{eligible} total - {990_count} contaminated = {extractable} docs × $0.0012 = ${cost}`. For 1,400 docs this scan is fast (~2s on RDS).
- **Stats tracking:** `stats_json = {total, extracted, skipped_990, contamination_990: [{type, sha, ein, marker}...], truncated_text: N, capped_observations: N, errors: [{sha, message}...], error_count}`
- **Metadata lifecycle (AC11/AC29):** At run start: INSERT `extraction_runs` with `extractor_version = prompt_sha256(prompt_path)`, model name, config (ntee, materials, workers, batch_size, min_text_len). At run end (or on failure): UPDATE `finished_at = now()` and final `stats_json`.
- **Progress:** emit to fd 3 via `emit_progress()` for dashboard integration

3.2. Wire up database operations:
- Use Django's `connections['default'].cursor()` for raw SQL INSERT (observations table is not a Django model — it's in a separate schema)
- Batch INSERT using `psycopg2.extras.execute_values` for performance (100 observations at a time)
- **CRITICAL: All raw SQL must use parameterized queries exclusively.** Values go via `%s` placeholders and the `params` tuple — NEVER via f-strings, `.format()`, or string concatenation. Example: `cursor.execute("INSERT INTO lava_vocab.observations (...) VALUES %s", values_list)`. This applies to all phases that write raw SQL (Phase 3 observations, Phase 5 archetypes/members/rules).

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
def build_term_matrix(observations, min_org_count=3) -> tuple[np.ndarray, np.ndarray, list[str], list[str]]:
    """Returns (count_matrix, tf_normalized_matrix, org_eins, term_list)
    
    count_matrix: raw term counts per org (for FP-Growth binary conversion)
    tf_normalized_matrix: row-normalized (for clustering via cosine distance)
    """
    ...

def cluster_orgs(tf_matrix, k='auto', seed=42) -> tuple[np.ndarray, int, dict]:
    """Returns (labels, selected_k, silhouette_scores)
    
    Uses TF-normalized matrix with cosine distance + complete linkage.
    """
    # Complete linkage on cosine distance
    # Auto-k via silhouette score (range 4-20)
    ...

def compute_lift(count_matrix, labels, terms) -> dict[int, list[tuple[str, float]]]:
    """Per-cluster lift for each term relative to full population.
    Uses binary presence (count > 0) for lift calculation."""
    ...

def run_fpgrowth_per_cluster(count_matrix, labels, orgs, terms, min_support=0.05, min_lift=1.5):
    """Returns dict[cluster_id, DataFrame of rules]
    
    Converts count_matrix to binary (presence/absence) for FP-Growth.
    FP-Growth operates on binary transactions, not counts.
    """
    ...

def label_archetypes(lift_scores: dict) -> dict[int, str]:
    """Auto-label: top 2 terms by lift joined with ' + '"""
    ...
```

4.2. Key implementation notes:
- `build_term_matrix`: group observations by `source_org_ein` (union across all reports for the org), count term occurrences per org. Filter terms appearing in fewer than `min_org_count` orgs. Return BOTH raw count matrix AND TF-normalized matrix (row_counts / row_sum). Clustering uses TF-normalized; FP-Growth uses binary (count > 0).
- `cluster_orgs`: takes TF-normalized matrix. Use `scipy.spatial.distance.pdist(tf_matrix, 'cosine')` → `scipy.cluster.hierarchy.linkage(distances, method='complete')` → `fcluster(Z, t=k, criterion='maxclust')`. For auto-k, iterate k=4..20, compute silhouette score on cosine metric, pick max. Return all silhouette scores for reporting.
- `run_fpgrowth_per_cluster`: for each cluster, subset the count matrix to that cluster's rows, convert to binary DataFrame (>0 → True), run `mlxtend.frequent_patterns.fpgrowth`, then `association_rules` with metric='lift'
- Deterministic: `np.random.seed(seed)` at start. Hierarchical clustering + FP-Growth are deterministic given same input; silhouette score uses precomputed distances (no randomness). Output is fully reproducible under same seed + same observations.

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
2. Install packages: `pip3 install --user mlxtend==0.23.3 scikit-learn==1.6.1`
3. Run pilot extraction: `python manage.py extract_vocabulary P20-pilot-v1 --ntee P2%`
4. Run analysis: `python manage.py analyze_vocabulary P20-pilot-v1`
5. Review archetype report output against success metrics SM1-SM5

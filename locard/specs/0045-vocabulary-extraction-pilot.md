# Spec 0045: Vocabulary Extraction & Archetype Discovery — Pilot

**Status:** Draft
**Author:** Architect
**Created:** 2026-05-16
**Dependencies:** 0035 (Multi-Page Classification Context), 0038 (Classifier Pipeline Rebuild)
**Spike:** locard/spikes/001-vocabulary-extraction-poc.md (PASS — 4 archetypes, lift 2.9–6.45)

## Problem Statement

Layer 1 classified the corpus into material types (annual_report, impact_report, etc.) but tells us nothing about *what the organizations actually do* or *how they talk about their work*. To build the AI Interviewer and lexicon products, we need vocabulary observations — the domain-specific terms, metrics, and methodologies that differentiate a food bank from a homeless shelter from a youth development program.

Spike 001 proved this is extractable via LLM + Market Basket Analysis on a 75-doc sample. This spec takes that finding to production scale on a single NTEE vertical (P20: Human Services, ~1,400 annual/impact reports) with a schema designed to support the full corpus if results are favorable.

## Goals

1. **Production schema** — tables designed for the full corpus but populated with one vertical
2. **Credible extraction** — structured, repeatable, with provenance back to source text
3. **Archetype discovery** — hierarchical clustering discovers sub-archetypes; FP-Growth + lift scoring characterizes them
4. **Reusable pipeline** — same code runs on any NTEE vertical without modification
5. **Clean separation** — Layer 2 tables in their own schema, no mutation of Layer 1 data

## Non-Goals

- Full corpus extraction (186K docs) — this is a pilot on ~1,400 docs
- Pre-defined concept taxonomy — archetypes emerge from data, not hand-curated categories
- Embedding generation or pgvector (future phase, after vocabulary observations exist)
- Dashboard UI for vocabulary browsing (future spec)
- Real-time extraction (batch is fine)

## Design Principles

From Spike 001 conclusions:
1. **Don't pre-define concept categories.** Let terms cluster bottom-up via co-occurrence. The spike's hand-curated 12 concepts captured only 32% of observations.
2. **Single-pass extraction is sufficient.** The differentiation filter happens via lift scoring in the analysis phase, not a second LLM pass.
3. **Use full pages_text.** First-page text gave 9.5 terms/org. Multi-page context should yield 20-40+ terms, making clustering much denser.
4. **Filter 990s at ingestion.** 16% of "annual reports" are actually IRS Form 990s. Check first 500 chars before extraction.

## Technical Context

### Available Data

- **P20 (Human Services) annual/impact reports:** ~1,426 docs from V2 classification
- **pages_text:** available via `lava_corpus.classification_context` table (Spec 0035)
- **Org metadata:** `lava_corpus.nonprofits_seed` has name, state, NTEE code, revenue
- **DeepSeek API:** $0.14/Mtok input (cache miss), $0.28/Mtok output — extraction at ~$0.001/doc

### Spike Results (Baseline)

From 75 P20 first-page-only samples:
- 629 raw observations → 483 after 990 decontamination
- 9.5 avg unique terms per org
- 4 distinct archetypes: Housing/Homelessness, Food Security, Family Services, Early Childhood/Youth
- Lift range: 2.9–6.45 (strong separation)
- 32% concept-match rate against 12 hand-curated categories (68% were org-specific — expected and positive)

### Expected Pilot Results

With ~1,400 docs using full pages_text:
- 20-40+ unique terms per org (vs 9.5 from first-page only)
- 8-15 sub-archetypes (vs 4 from 75 samples)
- Denser co-occurrence patterns enabling finer-grained archetype separation

## Technical Implementation

### Schema: `lava_vocab`

A new PostgreSQL schema, cleanly separated from Layer 1 tables.

```sql
CREATE SCHEMA IF NOT EXISTS lava_vocab;

-- Extraction runs (audit trail)
CREATE TABLE lava_vocab.extraction_runs (
    id SERIAL PRIMARY KEY,
    run_tag TEXT NOT NULL UNIQUE,
    ntee_filter TEXT,                    -- e.g., 'P2%' or NULL for all
    material_filter TEXT[],              -- e.g., '{annual,impact}'
    extractor_version TEXT NOT NULL,     -- prompt version identifier
    model TEXT NOT NULL,                 -- e.g., 'deepseek-v4-flash'
    config_json JSONB DEFAULT '{}',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    stats_json JSONB DEFAULT '{}'
);

-- One row per extracted vocabulary observation
CREATE TABLE lava_vocab.observations (
    id BIGSERIAL PRIMARY KEY,
    run_id INT NOT NULL REFERENCES lava_vocab.extraction_runs(id),
    content_sha256 TEXT NOT NULL,        -- FK to corpus document
    source_org_ein TEXT NOT NULL,        -- denormalized for query speed
    term TEXT NOT NULL,                  -- canonical lowercase phrase (1-4 words)
    category TEXT NOT NULL,              -- stakeholder|metric|outcome|program|methodology
    evidence_span TEXT,                  -- source sentence fragment (max 150 chars)
    confidence REAL NOT NULL,            -- 0.0-1.0 extraction confidence
    page_number INT,                     -- which page the term appeared on
    extracted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_obs_ein ON lava_vocab.observations(source_org_ein);
CREATE INDEX idx_obs_term ON lava_vocab.observations(term);
CREATE INDEX idx_obs_run ON lava_vocab.observations(run_id);
CREATE INDEX idx_obs_sha ON lava_vocab.observations(content_sha256);

-- Archetype analysis results (output of MBA)
CREATE TABLE lava_vocab.archetypes (
    id SERIAL PRIMARY KEY,
    run_id INT NOT NULL REFERENCES lava_vocab.extraction_runs(id),
    label TEXT NOT NULL,                 -- human-readable name (assigned post-clustering)
    method TEXT NOT NULL DEFAULT 'fp_growth_ward',
    cluster_id INT NOT NULL,
    org_count INT NOT NULL,
    config_json JSONB DEFAULT '{}',      -- clustering params (min_support, k, etc.)
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Which orgs belong to which archetype
CREATE TABLE lava_vocab.archetype_members (
    id SERIAL PRIMARY KEY,
    archetype_id INT NOT NULL REFERENCES lava_vocab.archetypes(id),
    source_org_ein TEXT NOT NULL,
    membership_strength REAL,            -- NULL for pilot; future: cosine similarity metric
    UNIQUE(archetype_id, source_org_ein)
);

-- Association rules (MBA output)
CREATE TABLE lava_vocab.association_rules (
    id SERIAL PRIMARY KEY,
    archetype_id INT NOT NULL REFERENCES lava_vocab.archetypes(id),
    antecedent TEXT[] NOT NULL,          -- e.g., '{food_pantry,nutrition}'
    consequent TEXT[] NOT NULL,          -- e.g., '{food_insecurity}'
    support REAL NOT NULL,
    confidence REAL NOT NULL,
    lift REAL NOT NULL,
    conviction REAL
);

CREATE INDEX idx_rules_archetype ON lava_vocab.association_rules(archetype_id);
CREATE INDEX idx_rules_lift ON lava_vocab.association_rules(lift DESC);
```

**Why this schema works for production:**
- `extraction_runs` tracks every run with full config (reproducible)
- `observations` is append-only — re-running extraction creates a new run, doesn't delete old data
- `archetypes` and `association_rules` are analysis outputs tied to a specific run
- No foreign keys into Layer 1 tables (just TEXT references to `content_sha256` and `source_org_ein`)
- Can drop all `lava_vocab` tables without touching anything else

### Extraction Prompt (v1)

Informed by Spike 001's successful prompt, enhanced for multi-page context:

```
You are analyzing a nonprofit annual/impact report. Extract domain-specific vocabulary.

Organization: {org_name}
NTEE Code: {ntee_code} (Human Services)
State: {state}

For each domain-specific term or phrase, provide:
- term: the exact phrase as used, lowercase, 1-4 words
- category: one of [stakeholder, metric, outcome, program, methodology]
- evidence_span: the sentence fragment containing the term (max 150 chars)
- confidence: 0.0-1.0 how domain-specific this term is
- page: which page number this appears on (if identifiable)

Categories:
- stakeholder: what they call the people they serve (e.g., "neighbors", "participants", "guests")
- metric: what they measure (e.g., "meals distributed", "bed nights", "recidivism rate")
- outcome: what they claim to achieve (e.g., "food security", "housing stability", "school readiness")
- program: named programs or service types (e.g., "mobile pantry", "rapid rehousing", "head start")
- methodology: how they deliver services (e.g., "trauma-informed care", "harm reduction", "collective impact")

IGNORE generic nonprofit language: impact, community, mission, stakeholders, donors, volunteers,
support, serve, help, change, make a difference, board of directors, fiscal year, annual report,
strategic plan, partnership, collaboration, engagement, sustainability, capacity building.

IGNORE financial/IRS language: form 990, tax-exempt, gross receipts, net assets, fund balance,
contributions and grants, program service revenue, total expenses, governing body.

Return ONLY a JSON array. No explanation, no markdown.

Document text:
{pages_text}
```

**Prompt versioning:** The prompt text is stored in a dedicated file (`lavandula/vocab/prompts/v1_extract.txt`) and its SHA256 is recorded in `extraction_runs.extractor_version`. Any prompt change creates a new version, enabling A/B comparison.

### Extraction Pipeline

**Management command:** `python manage.py extract_vocabulary`

```
Usage:
  extract_vocabulary <run_tag> [options]

Options:
  --ntee TEXT          NTEE prefix filter (default: 'P2%')
  --material TEXT[]    Material types (default: annual,impact)
  --workers INT        Concurrent DeepSeek workers (default: 4)
  --batch-size INT     Docs per DB fetch (default: 100)
  --min-text-len INT   Minimum pages_text length (default: 500)
  --dry-run            Show eligible doc count without extracting
  --resume             Resume from last checkpoint
```

**Pipeline steps:**

1. **Select eligible documents:**
```sql
SELECT c.content_sha256, c.source_org_ein, cc.pages_text,
       ns.name, ns.ntee_code, ns.state
FROM lava_corpus.corpus c
JOIN lava_corpus.classification_context cc ON cc.content_sha256 = c.content_sha256
JOIN lava_corpus.nonprofits_seed ns ON ns.ein = c.source_org_ein
WHERE c.classification IN ('annual', 'impact')
  AND ns.ntee_code LIKE 'P2%'
  AND cc.pages_text IS NOT NULL
  AND LENGTH(cc.pages_text) >= 500
ORDER BY c.source_org_ein, c.report_year DESC
```

2. **Pre-filter 990 contamination:**
```python
def is_990(pages_text: str) -> bool:
    header = pages_text[:500].lower()
    return any(marker in header for marker in [
        "return of organization exempt from income tax",
        "form 990",
        "department of the treasury",
        "internal revenue service",
    ])
```
Documents flagged as 990s are skipped and logged (useful for improving Layer 1 classification later).

3. **Extract via DeepSeek:** Same `DeepSeekAPIClient` from `classifier_clients.py`. Send prompt + pages_text, parse JSON response, validate against schema.

4. **Post-process observations:**
   - Lowercase and strip whitespace from terms
   - Deduplicate within same document (keep highest confidence)
   - Reject terms shorter than 3 chars or longer than 60 chars
   - Reject observations with confidence < 0.5
   - Insert into `lava_vocab.observations`

5. **Checkpoint:** After each batch, update `extraction_runs.stats_json` with progress and store the last processed `content_sha256` in `config_json.cursor`. The extraction query has a deterministic sort order (`source_org_ein, report_year DESC, content_sha256`), so `--resume` adds `WHERE content_sha256 > :cursor` to skip already-processed docs. SHA256 is a unique, deterministic tie-breaker.

**Page number extraction:** `pages_text` from Spec 0035's `classification_context` table contains `--- PAGE N ---` markers between pages. The extraction prompt asks for page numbers; the model reads them from these markers. If a term's page is not identifiable (marker absent or ambiguous), store `page_number = NULL`.

**Multiple reports per org:** All reports for an org are extracted independently. The analysis phase unions terms across reports at the org level (one vector per org, not per document).

**Error handling:**
- DeepSeek timeout/error: retry once, then skip document and log to `extraction_runs.stats_json.errors[]` (content_sha256 + error message only — no raw response stored)
- JSON parse failure: skip document, log SHA + first 200 chars of response to stats_json (truncated, no PII retention)
- Batch failures don't abort the run — continue with next batch
- Total error count tracked in `stats_json.error_count`

**Rate limiting:** Same pattern as `reclassify_corpus` — track request timestamps, enforce 60 RPM ceiling per worker.

**PII consideration:** Source documents are published annual/impact reports (public documents). No redaction required. Evidence spans are limited to 150 chars of text that's already in the public PDF.

### Analysis Pipeline

**Management command:** `python manage.py analyze_vocabulary`

```
Usage:
  analyze_vocabulary <run_tag> [options]

Options:
  --min-support FLOAT   Minimum itemset support (default: 0.05)
  --min-lift FLOAT      Minimum lift for association rules (default: 1.5)
  --k INT               Number of clusters for archetype discovery (default: auto)
  --min-org-count INT   Minimum orgs for a term to be included (default: 3)
  --seed INT            Random seed for reproducibility (default: 42)
```

**Two complementary analyses** (both stored, serving different purposes):

| Analysis | Method | Output | Purpose |
|----------|--------|--------|---------|
| Archetype discovery | Hierarchical clustering | Org → archetype assignment | "What kinds of orgs exist in P20?" |
| Archetype characterization | FP-Growth per archetype | Association rules with lift | "What vocabulary defines each archetype?" |

Clustering answers "who groups together." MBA answers "what makes each group distinctive."

**Steps:**

1. **Build term frequency matrix:**
   - Group observations by org — union of terms across ALL reports for that org (multiple reports per org contribute to the same row)
   - Terms must appear in `--min-org-count` orgs to be included (noise filter)
   - Binary presence matrix (term appears/doesn't appear for this org)

2. **Hierarchical clustering for archetype discovery:**
   - Cosine distance on TF-normalized term vectors
   - Complete linkage (appropriate for cosine distance; Ward assumes Euclidean)
   - Auto-select k via silhouette score if `--k auto`, else use provided k
   - Assign orgs to clusters
   - Fixed random seed (`--seed`) ensures deterministic results under same inputs

3. **Per-archetype FP-Growth:**
   - For each cluster, subset the binary matrix to just that cluster's orgs
   - Run FP-Growth (`mlxtend.frequent_patterns.fpgrowth`) on the subset
   - Generate association rules with support, confidence, lift, conviction
   - Rules are per-archetype (not global) — they describe vocabulary co-occurrence within a specific org type

4. **Per-archetype lift scoring:**
   - For each cluster, compute lift of every term relative to full population
   - Terms with lift > 2.0 are archetype-specific
   - Terms with lift > 3.0 are definitional

5. **Archetype labeling (heuristic):**
   - Auto-label = top 2 terms by lift, joined with " + " (e.g., "food_pantry + nutrition")
   - Stored in `archetypes.label` as initial label
   - Can be manually overridden post-analysis (the label is descriptive, not structural)

6. **Store results:**
   - Insert archetypes into `lava_vocab.archetypes`
   - Insert org memberships into `lava_vocab.archetype_members` (membership_strength = NULL for pilot; future phases may compute centroid similarity)
   - Insert association rules into `lava_vocab.association_rules`

7. **Generate report:** Print summary to stdout (archetype labels, top terms, lift scores, org examples) — same format as spike's CONCLUSION.md output.

### 990 Contamination Report

As a side-effect, the extraction pipeline identifies documents that are actually 990s despite being classified as annual/impact reports. This is logged as:

```json
{"type": "990_contamination", "content_sha256": "...", "source_org_ein": "...", "marker": "form 990"}
```

This data can later feed back into Layer 1 to fix misclassifications (but that's out of scope for this spec).

## Acceptance Criteria

### Schema
- AC1: `lava_vocab` schema exists with tables: `extraction_runs`, `observations`, `archetypes`, `archetype_members`, `association_rules` — plus all defined indexes
- AC2: Tables have no foreign key constraints to other schemas (TEXT references only)
- AC3: Schema can be dropped entirely without affecting Layer 1 tables

### Extraction
- AC4: `extract_vocabulary` command processes P20 annual/impact docs with pages_text
- AC5: 990 contamination pre-filter skips documents with IRS markers in first 500 chars
- AC6: Extraction uses DeepSeek-v4-flash via existing `DeepSeekAPIClient`
- AC7: Each observation stored with: term, category, evidence_span, confidence, page_number, document reference, org reference
- AC8: Observations are deduplicated within same document (highest confidence wins)
- AC9: Terms outside 3-60 char range are rejected
- AC10: Observations with confidence < 0.5 are rejected
- AC11: Run metadata (config, model, prompt version, timing, stats) stored in `extraction_runs`
- AC12: `--resume` flag allows continuing from last checkpoint after interruption
- AC13: `--dry-run` shows eligible document count (after 990 filter) and estimated cost (count × $0.0012) without extracting
- AC14: Rate limiting enforces ≤60 RPM per worker
- AC15: Individual document failures don't abort the run

### Analysis
- AC16: `analyze_vocabulary` reads observations for a given run and produces archetype clusters
- AC17: Terms appearing in fewer than `--min-org-count` orgs are excluded from analysis
- AC18: FP-Growth produces frequent itemsets and association rules with support, confidence, lift
- AC19: Hierarchical clustering assigns orgs to archetypes
- AC20: Per-archetype lift scores identify archetype-specific terms (lift > 2.0)
- AC21: Results stored in `archetypes`, `archetype_members`, and `association_rules` tables
- AC22: Summary report printed to stdout with: archetype labels, org counts, top terms by lift, example orgs

### Pilot Success Metrics (not build gates)

These measure whether the approach works, not whether the code is correct. A correct implementation may fail these if the corpus doesn't contain the expected signal. Evaluate after the pilot run.

- SM1: Average unique terms per org ≥ 15 (up from spike's 9.5, due to pages_text)
- SM2: At least 5 distinct archetypes emerge with 3+ orgs each
- SM3: Each archetype has at least 3 terms with lift > 2.0
- SM4: Manual inspection of top 3 archetypes confirms they map to recognizable org types
- SM5: Cost per document stays below $0.002 (budget: ~$2.85 for 1,426 docs)

### Operational
- AC28: Extraction prompt stored in a versioned file (not hardcoded in Python)
- AC29: Prompt SHA256 recorded in `extraction_runs.extractor_version`
- AC30: Run can be repeated with different prompt versions for A/B comparison
- AC31: All timestamps in UTC

## Security Considerations

- DeepSeek API key fetched from SSM (existing pattern via `get_secret`)
- Document text sent to external API (same as classification — no new data exposure)
- No user-supplied input in SQL queries (parameterized throughout)
- `lava_vocab` schema accessible only to `research_app` role (same grants as other schemas)
- Evidence spans are truncated to 150 chars (no full document storage in observations table)

## Cost Estimate

For P20 pilot (~1,426 docs):
- Input per doc: ~5,400 tokens (system prompt cached + pages_text fresh)
- Output per doc: ~400 tokens (JSON array of 20-40 observations + reasoning)
- Cost per doc: ~$0.0012
- **Total pilot: ~$1.70**

(Significantly less than classification because the extraction prompt is shorter than the classifier definition + schema, and output is a flat JSON array rather than tool-use format.)

## Testing Requirements

- Unit tests for 990 pre-filter (positive cases, negative cases, edge cases)
- Unit tests for observation post-processing (dedup, length filter, confidence filter)
- Unit tests for term normalization (lowercase, strip, unicode handling)
- Integration test: mock DeepSeek response → observations in database
- Integration test: observations in database → FP-Growth → archetypes in database
- Unit tests for analysis pipeline (min_support filtering, lift calculation, cluster assignment)
- Test that `--resume` correctly continues from checkpoint without duplicating
- Test that `--dry-run` doesn't write any data
- **Reproducibility:** analysis tests use a fixed `--seed` and assert deterministic output given the same observation input. Clustering and FP-Growth are deterministic under fixed inputs + fixed random seed.

## Traps to Avoid

1. **Don't pre-define concept categories in the analysis.** The spike proved that hand-curated categories capture only 32% of observations. Let FP-Growth discover what clusters together.
2. **Don't normalize terms aggressively.** "Food pantry" and "food bank" are different terms that may co-occur. Aggressive stemming/lemmatization destroys signal. Keep exact phrases, let MBA find co-occurrence.
3. **V2 classification is acceptable for the pilot filter.** `classification IN ('annual', 'impact')` covers the full corpus and is good enough for selecting the pilot set. V3's `material_type` is more accurate but only covers ~28K docs so far. When scaling beyond the pilot, switch to V3 `material_type IN ('annual_report', 'impact_report')` for states that have been reclassified.
4. **Don't build a dashboard yet.** The analysis output is a printed report + database tables. UI comes after validating the approach works at scale.
5. **Don't try to embed or vectorize observations in this spec.** That's a future phase. Raw term co-occurrence via MBA is the validated approach.
6. **Don't merge observation tables with Layer 1 corpus tables.** Clean schema separation means we can iterate (or even drop and rebuild) without risk.

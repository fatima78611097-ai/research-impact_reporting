# Spec 0049: Statistical NLP Extraction & Sub-Archetype Discovery

**Status:** Draft
**Author:** Architect
**Created:** 2026-05-23
**Dependencies:** 0046 (Docling Full-Document Parsing)
**Supersedes:** 0045 (Vocabulary Extraction — LLM approach, abandoned)
**Spike:** locard/spikes/001-vocabulary-extraction-poc.md (PASS — 4 archetypes, lift 2.9–6.45)

## Problem Statement

Layer 1 classified the corpus into material types (annual_report, impact_report, etc.) but tells us nothing about *what the organizations actually do* or *how they talk about their work*. To build the AI Interviewer and published lexicon, we need domain-specific vocabulary — the terms, metrics, and methodologies that differentiate a food bank from a homeless shelter from a youth development program.

Spec 0045 attempted this via LLM prompting and was abandoned: LLMs project training priors rather than discovering what's in the documents, and the input was limited to ~5 pages of unstructured text. Spec 0046 (Docling) solves the input problem — full structured text with section headings, tables, and page provenance. This spec builds the extraction and analysis pipeline that operates on Docling output using statistical NLP methods that can only surface terms that are actually present in the documents.

## Goals

1. **Extract domain vocabulary** from Docling-parsed sections using NLP (not LLM prompting)
2. **Score term significance** via TF-IDF keyness (within-vertical vs cross-vertical comparison)
3. **Extract multi-word terms** via C-value/NC-value for domain phrases (e.g., "trauma-informed care")
4. **Discover sub-archetypes** via FP-Growth on term co-occurrence within NTEE verticals
5. **Store all results** in PostgreSQL (`lava_vocab` schema) for downstream consumption by the interviewer knowledge layer
6. **Run on CPU** — no GPU, no API costs. Same hosts that run crawlers.

## Non-Goals

- LLM-based extraction (explicitly rejected — see Design Principles)
- Embedding generation or vector search (future phase after vocabulary is validated)
- Dashboard UI for browsing results (future spec)
- Cross-vertical archetype comparison (requires 2+ verticals extracted; this spec handles one at a time, comparison is analysis-phase work)
- Pre-defined concept taxonomy (archetypes emerge from data)
- Real-time extraction (batch pipeline)

## Design Principles

1. **Statistical methods only for extraction.** spaCy NER, TF-IDF, C-value extract what's IN the documents. They cannot hallucinate terms. The taxonomy must be statistically grounded because it becomes the runtime constraint for the production AI interviewer.

2. **Don't pre-define concept categories.** Spike 001 proved hand-curated categories capture only 32% of observations. Let FP-Growth discover what clusters together.

3. **Don't normalize terms aggressively.** "Food pantry" and "food bank" are different terms that may co-occur. Stemming/lemmatization destroys signal. Keep lemmatized forms but preserve the original term alongside for display.

4. **Vertical-relative keyness is essential.** "Community" appears everywhere — it's not distinctive. A term's value comes from being frequent in one vertical but rare across others. TF-IDF across verticals surfaces this automatically.

5. **Section context matters.** A term found under "Our Programs" heading carries different weight than one under "Acknowledgments." Use section heading context as a feature.

## Technical Context

### Input: Docling Output (`lava_parse`)

From Spec 0046, each document produces:
- **`lava_parse.sections`**: section_index, heading, heading_level, body_text, char_count, page_start, page_end, parent_headings
- **`lava_parse.tables`**: structured table data linked to sections
- **`lava_parse.documents`**: parse metadata (page_count, section_count, total_text_chars)

The extraction pipeline reads `sections.body_text` and `sections.heading` as its primary input.

### Available Tools (Python, CPU-only)

| Tool | Purpose | Cost |
|------|---------|------|
| spaCy `en_core_web_lg` | NER, noun phrase extraction, POS tagging | Free, ~3GB model |
| scikit-learn TfidfVectorizer | TF-IDF term scoring | Free |
| C-value algorithm | Multi-word term extraction | Free (implement or use `pyate`) |
| mlxtend FP-Growth | Frequent itemset mining + association rules | Free |
| scipy hierarchical clustering | Org-level archetype discovery | Free |

### Spike 001 Baseline

From 75 P20 first-page-only samples (LLM extraction):
- 629 raw observations → 483 after 990 decontamination
- 9.5 avg unique terms per org
- 4 distinct archetypes (Housing, Food Security, Family Services, Early Childhood)
- Lift range: 2.9–6.45

With full Docling text (30+ pages vs 1 page), statistical extraction should produce significantly denser vocabulary per org.

### Corpus Scale

| Subset | Docs | Est. Sections | Est. Text |
|--------|------|---------------|-----------|
| Annual + impact reports | ~44K | ~700K | ~2 GiB |
| Full corpus (all types) | ~260K | ~4M | ~12 GiB |
| Single vertical (e.g., P20) | ~1.4K | ~22K | ~70 MiB |

Processing time estimate (CPU): ~2-5 seconds per document for spaCy + TF-IDF + C-value. Single vertical (~1.4K docs): ~1-2 hours. Full report corpus (~44K docs): ~1-3 days on a single host.

## Technical Implementation

### Schema: `lava_vocab`

```sql
CREATE SCHEMA IF NOT EXISTS lava_vocab;

-- Extraction runs (audit trail)
CREATE TABLE lava_vocab.extraction_runs (
    id SERIAL PRIMARY KEY,
    run_tag TEXT NOT NULL UNIQUE,
    ntee_filter TEXT,                    -- e.g., 'P2%' or NULL for all
    material_filter TEXT[],              -- e.g., '{annual_report,impact_report}'
    extractor_version TEXT NOT NULL,     -- pipeline version identifier
    config_json JSONB DEFAULT '{}',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    stats_json JSONB DEFAULT '{}'
);

-- Per-document extraction status
CREATE TABLE lava_vocab.doc_extractions (
    content_sha256 TEXT PRIMARY KEY,
    run_id INT NOT NULL REFERENCES lava_vocab.extraction_runs(id),
    source_org_ein TEXT NOT NULL,
    term_count INT NOT NULL,
    extracted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    error TEXT                           -- NULL if successful
);

-- Individual term observations (one row per term per document)
CREATE TABLE lava_vocab.observations (
    id BIGSERIAL PRIMARY KEY,
    run_id INT NOT NULL REFERENCES lava_vocab.extraction_runs(id),
    content_sha256 TEXT NOT NULL,
    source_org_ein TEXT NOT NULL,
    term TEXT NOT NULL,                  -- canonical form (lowercased, lemmatized)
    term_raw TEXT NOT NULL,              -- original surface form as found in text
    term_type TEXT NOT NULL,             -- 'noun_phrase' | 'named_entity' | 'ngram' | 'cvalue'
    pos_pattern TEXT,                    -- POS tag pattern (e.g., 'ADJ NOUN', 'NOUN NOUN')
    frequency INT NOT NULL DEFAULT 1,   -- count within this document
    section_index INT,                  -- which section it appeared in
    section_heading TEXT,               -- heading of the section (denormalized for convenience)
    heading_context TEXT[],             -- parent_headings from the section
    UNIQUE(run_id, content_sha256, term, section_index)
);

CREATE INDEX idx_obs_ein ON lava_vocab.observations(source_org_ein);
CREATE INDEX idx_obs_term ON lava_vocab.observations(term);
CREATE INDEX idx_obs_run ON lava_vocab.observations(run_id);
CREATE INDEX idx_obs_sha ON lava_vocab.observations(content_sha256);
CREATE INDEX idx_obs_type ON lava_vocab.observations(term_type);

-- TF-IDF scores (computed per vertical, not per document)
CREATE TABLE lava_vocab.tfidf_scores (
    id BIGSERIAL PRIMARY KEY,
    run_id INT NOT NULL REFERENCES lava_vocab.extraction_runs(id),
    term TEXT NOT NULL,
    ntee_prefix TEXT NOT NULL,           -- vertical (e.g., 'P2')
    doc_frequency INT NOT NULL,          -- how many docs in this vertical contain this term
    corpus_doc_frequency INT NOT NULL,   -- how many docs corpus-wide contain this term
    tfidf_within REAL NOT NULL,          -- TF-IDF score within the vertical
    tfidf_keyness REAL,                  -- ratio: within-vertical TF-IDF / cross-corpus TF-IDF
    UNIQUE(run_id, term, ntee_prefix)
);

CREATE INDEX idx_tfidf_keyness ON lava_vocab.tfidf_scores(tfidf_keyness DESC NULLS LAST);
CREATE INDEX idx_tfidf_run ON lava_vocab.tfidf_scores(run_id);

-- C-value multi-word term scores
CREATE TABLE lava_vocab.cvalue_terms (
    id BIGSERIAL PRIMARY KEY,
    run_id INT NOT NULL REFERENCES lava_vocab.extraction_runs(id),
    term TEXT NOT NULL,
    ntee_prefix TEXT NOT NULL,
    word_count INT NOT NULL,             -- number of words in the term
    cvalue REAL NOT NULL,                -- C-value score
    nc_value REAL,                       -- NC-value (C-value weighted by context words)
    doc_frequency INT NOT NULL,
    UNIQUE(run_id, term, ntee_prefix)
);

CREATE INDEX idx_cvalue_score ON lava_vocab.cvalue_terms(cvalue DESC);

-- Archetype analysis results
CREATE TABLE lava_vocab.archetypes (
    id SERIAL PRIMARY KEY,
    run_id INT NOT NULL REFERENCES lava_vocab.extraction_runs(id),
    label TEXT NOT NULL,
    ntee_prefix TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT 'fp_growth_ward',
    cluster_id INT NOT NULL,
    org_count INT NOT NULL,
    config_json JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Org-to-archetype membership
CREATE TABLE lava_vocab.archetype_members (
    id SERIAL PRIMARY KEY,
    archetype_id INT NOT NULL REFERENCES lava_vocab.archetypes(id),
    source_org_ein TEXT NOT NULL,
    membership_strength REAL,
    UNIQUE(archetype_id, source_org_ein)
);

-- Association rules (FP-Growth output per archetype)
CREATE TABLE lava_vocab.association_rules (
    id SERIAL PRIMARY KEY,
    archetype_id INT NOT NULL REFERENCES lava_vocab.archetypes(id),
    antecedent TEXT[] NOT NULL,
    consequent TEXT[] NOT NULL,
    support REAL NOT NULL,
    confidence REAL NOT NULL,
    lift REAL NOT NULL,
    conviction REAL
);

CREATE INDEX idx_rules_archetype ON lava_vocab.association_rules(archetype_id);
CREATE INDEX idx_rules_lift ON lava_vocab.association_rules(lift DESC);

-- Grants
GRANT USAGE ON SCHEMA lava_vocab TO research_app;
GRANT ALL ON ALL TABLES IN SCHEMA lava_vocab TO research_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA lava_vocab TO research_app;
```

### Pipeline Overview

```
lava_parse.sections (Docling output)
    │
    ▼
[Step 1: Term Extraction]  ──  spaCy NLP + C-value
    │
    ▼
lava_vocab.observations (raw terms per doc per section)
    │
    ▼
[Step 2: Keyness Scoring]  ──  TF-IDF within-vertical vs cross-corpus
    │
    ▼
lava_vocab.tfidf_scores + lava_vocab.cvalue_terms
    │
    ▼
[Step 3: Archetype Discovery]  ──  FP-Growth + hierarchical clustering
    │
    ▼
lava_vocab.archetypes + archetype_members + association_rules
```

Steps 1 and 2 run per document/vertical. Step 3 runs after extraction is complete for a vertical (or set of verticals).

### Step 1: Term Extraction

**Management command:** `python manage.py extract_terms`

```
Usage:
  extract_terms <run_tag> [options]

Options:
  --ntee TEXT          NTEE prefix filter (default: 'P2%')
  --material TEXT[]    Material types (default: annual_report,impact_report)
  --workers INT        Concurrent extraction workers (default: 4)
  --batch-size INT     Docs per DB fetch (default: 100)
  --min-section-chars INT   Minimum section body_text length (default: 50)
  --dry-run            Show eligible doc count without extracting
  --resume             Resume from last checkpoint
```

**For each document:**

1. **Load sections** from `lava_parse.sections` ordered by section_index
2. **Filter sections**: skip sections shorter than `--min-section-chars` (acknowledgments, copyright, etc.)
3. **Run spaCy pipeline** on each section's `body_text`:
   - **Noun phrases** (`doc.noun_chunks`): extract multi-word phrases, lowercase, deduplicate
   - **Named entities** (`doc.ents`): ORG, PRODUCT, EVENT, WORK_OF_ART labels only (skip PERSON, GPE, DATE, CARDINAL — not domain vocabulary)
   - **POS-pattern n-grams**: extract (ADJ NOUN), (NOUN NOUN), (ADJ NOUN NOUN) patterns from the token stream — catches domain phrases spaCy doesn't chunk
4. **Run C-value extraction** on the concatenated text of all sections:
   - Extract candidate multi-word terms (2-5 words) matching noun-phrase POS patterns
   - Score by C-value formula: `log2(|a|) * f(a)` for terms not nested, adjusted for nested terms
   - Keep terms with C-value > 1.0
5. **Store observations**: one row per unique (term, section_index) with frequency count

**Stopword / noise filtering:**
- Standard English stopwords (spaCy defaults)
- Generic nonprofit boilerplate: "community", "mission", "impact", "stakeholders", "board of directors", "fiscal year", "annual report", "strategic plan" (same list validated in Spike 001)
- Financial/IRS language: "form 990", "tax-exempt", "gross receipts", "net assets"
- Single-character and single-word terms are excluded (only multi-word or named entities kept)
- Terms appearing in >80% of documents in the vertical are excluded (too common to be distinctive)

**Section heading context:**
Each observation carries the section heading and parent_headings from the section it was found in. This enables downstream analysis like "terms found under 'Programs' headings cluster differently than terms under 'Financial Summary'."

### Step 2: Keyness Scoring

**Management command:** `python manage.py score_keyness`

```
Usage:
  score_keyness <run_tag> [options]

Options:
  --ntee TEXT          Target vertical (default: 'P2%')
  --min-docs INT       Minimum docs a term must appear in (default: 3)
```

Runs after Step 1 completes for at least one vertical (ideally two or more for cross-vertical comparison).

**TF-IDF keyness:**
1. Build document-term matrix for the target vertical (binary presence, not raw counts)
2. Compute TF-IDF within the vertical using scikit-learn `TfidfVectorizer`
3. Compute corpus-wide document frequency for each term
4. **Keyness ratio**: `tfidf_within / tfidf_corpus`. High ratio = term is distinctive to this vertical.
5. Store in `lava_vocab.tfidf_scores`

**C-value aggregation:**
1. Aggregate C-value scores across all documents in the vertical
2. Compute NC-value by weighting with context words that frequently appear with the term
3. Store in `lava_vocab.cvalue_terms`

**When only one vertical is available** (initial pilot), `tfidf_keyness` is NULL — within-vertical TF-IDF still works but cross-vertical comparison requires a second vertical. The system works with one vertical but gets better with more.

### Step 3: Archetype Discovery

**Management command:** `python manage.py discover_archetypes`

```
Usage:
  discover_archetypes <run_tag> [options]

Options:
  --ntee TEXT          Target vertical (default: 'P2%')
  --min-support FLOAT  Minimum itemset support (default: 0.05)
  --min-lift FLOAT     Minimum lift for association rules (default: 1.5)
  --min-keyness FLOAT  Minimum tfidf_keyness to include a term (default: null — use all terms)
  --k INT              Number of clusters (default: auto via silhouette)
  --min-org-count INT  Minimum orgs for a term to be included (default: 3)
  --seed INT           Random seed (default: 42)
```

Runs after Step 2 completes for a vertical.

**Process:**

1. **Build org-term matrix:**
   - Group observations by `source_org_ein` — union of terms across all documents for that org
   - Filter to terms appearing in `--min-org-count` orgs
   - Optionally filter to terms with `tfidf_keyness >= --min-keyness` (if available)
   - Binary presence matrix (term appears / doesn't)

2. **Hierarchical clustering:**
   - Cosine distance on the org-term matrix
   - Complete linkage (appropriate for cosine distance)
   - Auto-select k via silhouette score, or use provided `--k`
   - Assign orgs to clusters

3. **Per-archetype FP-Growth:**
   - For each cluster, run FP-Growth on the cluster's org-term subset
   - Generate association rules with support, confidence, lift, conviction
   - Rules describe vocabulary co-occurrence within a specific org type

4. **Per-archetype lift scoring:**
   - For each cluster, compute per-term lift vs full population
   - lift > 2.0 = archetype-specific, lift > 3.0 = definitional

5. **Auto-label archetypes:**
   - Label = top 2 terms by lift, joined with " + " (e.g., "food_pantry + nutrition")
   - Stored in `archetypes.label` — can be overridden manually

6. **Store results** in `archetypes`, `archetype_members`, `association_rules`

7. **Print summary** to stdout: archetype labels, org counts, top terms by lift, example orgs

### Pilot Execution Plan

**Phase 1: Single vertical (P20 Human Services)**
1. Docling must have parsed P20 annual/impact reports (Spec 0046 prerequisite)
2. Run `extract_terms p20-pilot-v1 --ntee P2%`
3. Run `score_keyness p20-pilot-v1 --ntee P2%` (no cross-vertical keyness yet)
4. Run `discover_archetypes p20-pilot-v1 --ntee P2%`
5. Evaluate against spike baseline (expect ≥15 terms/org, ≥5 archetypes)

**Phase 2: Second vertical (validate cross-vertical keyness)**
1. Pick a contrasting vertical (e.g., E (Healthcare), T (Arts/Culture), or K (Food/Nutrition))
2. Run extraction + scoring on both verticals
3. Cross-vertical keyness becomes available — terms distinctive to P20 vs the other vertical
4. Re-run archetype discovery with `--min-keyness` filtering

**Phase 3: Full report corpus**
1. Run extraction across all annual/impact reports (~44K docs)
2. Compute keyness across all verticals
3. Discover archetypes per vertical
4. Validate: do archetypes make intuitive sense across diverse verticals?

### Testing with Existing Corpus

Since Docling hasn't parsed any documents yet, we can **test the extraction pipeline** using synthetic or manually-created `lava_parse` sections:

1. **Test fixtures:** Create 10-20 `lava_parse.sections` rows from known annual reports (manually extract text from a few PDFs we have in S3)
2. **Unit tests:** Verify spaCy extraction, C-value scoring, TF-IDF computation, FP-Growth, clustering — all testable with fixture data
3. **Integration test:** Full pipeline on fixtures → verify observations, scores, and archetypes in DB

This lets us build and validate the pipeline while the crawl finishes and before Docling runs.

## Acceptance Criteria

### Schema
- AC1: `lava_vocab` schema exists with all tables: `extraction_runs`, `doc_extractions`, `observations`, `tfidf_scores`, `cvalue_terms`, `archetypes`, `archetype_members`, `association_rules`
- AC2: No foreign keys reference tables outside `lava_vocab` (TEXT references only for cross-schema)
- AC3: Schema can be dropped entirely without affecting Layer 1 or `lava_parse`

### Term Extraction (Step 1)
- AC4: `extract_terms` reads sections from `lava_parse.sections` for documents matching NTEE + material filters
- AC5: spaCy extracts noun phrases, named entities (ORG/PRODUCT/EVENT/WORK_OF_ART), and POS-pattern n-grams
- AC6: C-value extracts multi-word terms (2-5 words) with score > 1.0
- AC7: Each observation stored with: term (canonical), term_raw, term_type, frequency, section_index, section_heading, heading_context
- AC8: Stopword/boilerplate terms filtered per the defined lists
- AC9: Terms appearing in >80% of documents excluded
- AC10: `--resume` allows continuing from checkpoint after interruption
- AC11: `--dry-run` shows eligible document count without extracting
- AC12: Individual document failures don't abort the run

### Keyness Scoring (Step 2)
- AC13: `score_keyness` computes within-vertical TF-IDF for all extracted terms
- AC14: Corpus-wide document frequency computed for cross-vertical keyness (when multiple verticals available)
- AC15: Terms with `--min-docs` filter applied
- AC16: Results stored in `tfidf_scores` and `cvalue_terms`

### Archetype Discovery (Step 3)
- AC17: `discover_archetypes` builds binary org-term matrix from observations
- AC18: Hierarchical clustering assigns orgs to archetypes
- AC19: Auto-k via silhouette score when `--k auto`
- AC20: FP-Growth produces association rules per archetype with support, confidence, lift
- AC21: Per-archetype lift scores identify archetype-specific terms (lift > 2.0)
- AC22: Results stored in `archetypes`, `archetype_members`, `association_rules`
- AC23: Summary printed to stdout with archetype labels, org counts, top terms, examples
- AC24: Fixed random seed ensures deterministic results given same input

### Pilot Success Metrics (not build gates)
- SM1: Average unique terms per org ≥ 15 (up from spike's 9.5, due to full document text)
- SM2: At least 5 distinct archetypes in P20 with 3+ orgs each
- SM3: Each archetype has at least 3 terms with lift > 2.0
- SM4: Manual inspection of top 3 archetypes confirms recognizable org types
- SM5: Zero API cost for extraction

### Operational
- AC25: Pipeline runs on CPU (cloud2 t3.large or equivalent) — no GPU required
- AC26: `en_core_web_lg` model bundled or documented as prerequisite
- AC27: All timestamps in UTC
- AC28: Run can be repeated with different config for comparison (new run_tag)

## Security Considerations

- No external API calls — all processing is local CPU
- No user-supplied input reaches SQL queries (parameterized throughout)
- `lava_vocab` schema accessible only to `research_app` role
- Source text stays in `lava_parse` — observations contain only extracted terms and short headings
- `run_tag` validated: `^[a-zA-Z0-9_-]{1,64}$`
- `--ntee` validated: `^[A-Z][0-9]*%?$`
- `--material` validated against known material_type values
- No shell commands, subprocess calls, or file path construction from user input

## Cost Estimate

- **Compute:** CPU-only. ~2-5 sec/doc on t3.large. P20 pilot (~1.4K docs): ~1-2 hours. Full report corpus (~44K docs): ~1-3 days.
- **Storage:** Observations table: ~44K docs × ~25 terms/doc × ~200 bytes/row ≈ 220 MiB. Well within RDS limits.
- **Dependencies:** spaCy `en_core_web_lg` (~560 MiB download), scikit-learn, mlxtend, scipy (all pip-installable)
- **API cost:** $0.00

## Testing Requirements

- Unit tests for spaCy noun phrase extraction (known input → expected terms)
- Unit tests for C-value computation (known term frequencies → expected scores)
- Unit tests for stopword/boilerplate filtering
- Unit tests for TF-IDF keyness computation
- Unit tests for FP-Growth + lift scoring (known binary matrix → expected rules)
- Unit tests for hierarchical clustering (deterministic under fixed seed)
- Integration test: sections in DB → extract → score → discover → verify all tables populated
- Test that `--resume` continues correctly without duplicating
- Test that `--dry-run` writes nothing

## Traps to Avoid

1. **Don't use LLM for extraction.** Statistical methods extract what's IN the documents. LLMs project priors. This is a non-negotiable design decision. See `feedback_llm_not_for_extraction.md`.
2. **Don't pre-define concept categories.** Spike proved hand-curated categories capture only 32%. Let co-occurrence patterns emerge.
3. **Don't normalize terms aggressively.** "Food pantry" and "food bank" are distinct signals. Keep canonical (lemmatized) + raw forms. Aggressive stemming destroys archetype signal.
4. **Don't skip C-value.** TF-IDF alone misses multi-word terms because it treats "trauma" and "informed" and "care" separately. C-value captures "trauma-informed care" as a unit.
5. **Don't require cross-vertical data to start.** The pipeline works with a single vertical — keyness ratio is simply unavailable. It improves with more verticals but doesn't depend on them.
6. **Don't build a dashboard.** Output is DB tables + stdout summary. UI comes after validating the approach.
7. **Don't embed or vectorize.** That's a future phase. Raw co-occurrence via FP-Growth is the validated approach.
8. **Don't merge with Layer 1 tables.** Clean schema separation means we can iterate or rebuild without risk.

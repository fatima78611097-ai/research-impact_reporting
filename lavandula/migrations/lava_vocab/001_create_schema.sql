-- Migration 001: Create lava_vocab schema for NLP extraction & archetype discovery
-- Spec 0049: Statistical NLP Extraction & Sub-Archetype Discovery
--
-- This schema stores extracted domain vocabulary, TF-IDF scores, C-value terms,
-- and discovered organizational sub-archetypes. It is additive — no existing
-- schemas are modified. Cross-schema references use TEXT (no foreign keys).
-- Rollback: DROP SCHEMA lava_vocab CASCADE;

CREATE SCHEMA IF NOT EXISTS lava_vocab;

-- Extraction runs (audit trail)
CREATE TABLE IF NOT EXISTS lava_vocab.extraction_runs (
    id SERIAL PRIMARY KEY,
    run_tag TEXT NOT NULL UNIQUE,
    ntee_filter TEXT,
    material_filter TEXT[],
    extractor_version TEXT NOT NULL,
    config_json JSONB DEFAULT '{}',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    stats_json JSONB DEFAULT '{}'
);

-- Per-document extraction status
CREATE TABLE IF NOT EXISTS lava_vocab.doc_extractions (
    content_sha256 TEXT PRIMARY KEY,
    run_id INT NOT NULL REFERENCES lava_vocab.extraction_runs(id),
    source_org_ein TEXT NOT NULL,
    term_count INT NOT NULL,
    extracted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    error TEXT
);

-- Individual term observations (one row per term per document per section)
CREATE TABLE IF NOT EXISTS lava_vocab.observations (
    id BIGSERIAL PRIMARY KEY,
    run_id INT NOT NULL REFERENCES lava_vocab.extraction_runs(id),
    content_sha256 TEXT NOT NULL,
    source_org_ein TEXT NOT NULL,
    term TEXT NOT NULL,
    term_raw TEXT NOT NULL,
    term_type TEXT NOT NULL,
    pos_pattern TEXT,
    frequency INT NOT NULL DEFAULT 1,
    section_index INT,
    section_heading TEXT,
    heading_context TEXT[],
    UNIQUE(run_id, content_sha256, term, section_index)
);

CREATE INDEX IF NOT EXISTS idx_obs_ein ON lava_vocab.observations(source_org_ein);
CREATE INDEX IF NOT EXISTS idx_obs_term ON lava_vocab.observations(term);
CREATE INDEX IF NOT EXISTS idx_obs_run ON lava_vocab.observations(run_id);
CREATE INDEX IF NOT EXISTS idx_obs_sha ON lava_vocab.observations(content_sha256);
CREATE INDEX IF NOT EXISTS idx_obs_type ON lava_vocab.observations(term_type);

-- TF-IDF scores (computed per vertical, not per document)
CREATE TABLE IF NOT EXISTS lava_vocab.tfidf_scores (
    id BIGSERIAL PRIMARY KEY,
    run_id INT NOT NULL REFERENCES lava_vocab.extraction_runs(id),
    term TEXT NOT NULL,
    ntee_prefix TEXT NOT NULL,
    doc_frequency INT NOT NULL,
    corpus_doc_frequency INT NOT NULL,
    tfidf_within REAL NOT NULL,
    tfidf_keyness REAL,
    UNIQUE(run_id, term, ntee_prefix)
);

CREATE INDEX IF NOT EXISTS idx_tfidf_keyness ON lava_vocab.tfidf_scores(tfidf_keyness DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_tfidf_run ON lava_vocab.tfidf_scores(run_id);

-- C-value multi-word term scores
CREATE TABLE IF NOT EXISTS lava_vocab.cvalue_terms (
    id BIGSERIAL PRIMARY KEY,
    run_id INT NOT NULL REFERENCES lava_vocab.extraction_runs(id),
    term TEXT NOT NULL,
    ntee_prefix TEXT NOT NULL,
    word_count INT NOT NULL,
    cvalue REAL NOT NULL,
    nc_value REAL,
    doc_frequency INT NOT NULL,
    UNIQUE(run_id, term, ntee_prefix)
);

CREATE INDEX IF NOT EXISTS idx_cvalue_score ON lava_vocab.cvalue_terms(cvalue DESC);

-- Archetype analysis results
CREATE TABLE IF NOT EXISTS lava_vocab.archetypes (
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
CREATE TABLE IF NOT EXISTS lava_vocab.archetype_members (
    id SERIAL PRIMARY KEY,
    archetype_id INT NOT NULL REFERENCES lava_vocab.archetypes(id),
    source_org_ein TEXT NOT NULL,
    membership_strength REAL,
    UNIQUE(archetype_id, source_org_ein)
);

-- Association rules (FP-Growth output per archetype)
CREATE TABLE IF NOT EXISTS lava_vocab.association_rules (
    id SERIAL PRIMARY KEY,
    archetype_id INT NOT NULL REFERENCES lava_vocab.archetypes(id),
    antecedent TEXT[] NOT NULL,
    consequent TEXT[] NOT NULL,
    support REAL NOT NULL,
    confidence REAL NOT NULL,
    lift REAL NOT NULL,
    conviction REAL
);

CREATE INDEX IF NOT EXISTS idx_rules_archetype ON lava_vocab.association_rules(archetype_id);
CREATE INDEX IF NOT EXISTS idx_rules_lift ON lava_vocab.association_rules(lift DESC);

-- Grants (least-privilege)
GRANT USAGE ON SCHEMA lava_vocab TO research_app;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA lava_vocab TO research_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA lava_vocab TO research_app;
GRANT DELETE ON lava_vocab.extraction_runs, lava_vocab.doc_extractions TO research_app;

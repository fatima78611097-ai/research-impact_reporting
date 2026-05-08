-- Migration 014: classification_context, classification_runs, classification_results
-- Spec 0035: Multi-Page Classification Context & Hardened Classifier
--
-- Depends on: corpus table (001), classifier columns (007, 009)

BEGIN;

INSERT INTO lava_corpus.schema_version (version, description)
VALUES (14, 'classification_context, classification_runs, classification_results, v3 columns');

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

COMMIT;

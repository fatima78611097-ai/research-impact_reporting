-- 016_llm_extraction_tables.sql
-- Spec 0051: LLM Impact Extraction (Metrics + Stories)
--
-- Creates tables for storing LLM-extracted impact metrics and stories
-- in lava_vocab schema, alongside existing extraction infrastructure.
-- Reuses lava_vocab.extraction_runs for run tracking.

BEGIN;

CREATE TABLE IF NOT EXISTS lava_vocab.llm_metrics (
    id BIGSERIAL PRIMARY KEY,
    run_id INT NOT NULL REFERENCES lava_vocab.extraction_runs(id),
    content_sha256 TEXT NOT NULL,
    source_org_ein TEXT NOT NULL,
    metric_text TEXT NOT NULL,
    metric_type TEXT,
    metric_value REAL,
    unit TEXT,
    geo_impact TEXT,
    source_snippet TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_llm_metrics_ein ON lava_vocab.llm_metrics(source_org_ein);
CREATE INDEX idx_llm_metrics_sha ON lava_vocab.llm_metrics(content_sha256);
CREATE INDEX idx_llm_metrics_run ON lava_vocab.llm_metrics(run_id);

GRANT SELECT, INSERT, DELETE ON lava_vocab.llm_metrics TO research_app;
GRANT USAGE, SELECT ON lava_vocab.llm_metrics_id_seq TO research_app;


CREATE TABLE IF NOT EXISTS lava_vocab.llm_stories (
    id BIGSERIAL PRIMARY KEY,
    run_id INT NOT NULL REFERENCES lava_vocab.extraction_runs(id),
    content_sha256 TEXT NOT NULL,
    source_org_ein TEXT NOT NULL,
    story_title TEXT NOT NULL,
    story_summary TEXT,
    people_mentioned TEXT[],
    program TEXT,
    themes TEXT[],
    source_snippet TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_llm_stories_ein ON lava_vocab.llm_stories(source_org_ein);
CREATE INDEX idx_llm_stories_sha ON lava_vocab.llm_stories(content_sha256);
CREATE INDEX idx_llm_stories_run ON lava_vocab.llm_stories(run_id);

GRANT SELECT, INSERT, DELETE ON lava_vocab.llm_stories TO research_app;
GRANT USAGE, SELECT ON lava_vocab.llm_stories_id_seq TO research_app;

COMMIT;

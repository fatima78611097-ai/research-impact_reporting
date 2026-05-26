-- Migration 003: Create metric_observations table for metric extraction (Spec 0050)
-- Stores term + numeric value pairs with context snippets from parsed sections.
-- Rollback: DROP TABLE IF EXISTS lava_vocab.metric_observations CASCADE;

CREATE TABLE IF NOT EXISTS lava_vocab.metric_observations (
    id BIGSERIAL PRIMARY KEY,
    run_id INT NOT NULL REFERENCES lava_vocab.extraction_runs(id),
    content_sha256 TEXT NOT NULL,
    source_org_ein TEXT NOT NULL,
    term TEXT NOT NULL,
    numeric_value TEXT NOT NULL,
    numeric_parsed REAL,
    unit_hint TEXT,
    snippet TEXT NOT NULL,
    snippet_heading TEXT,
    section_index INT,
    source_type TEXT NOT NULL DEFAULT 'narrative',
    archetype_id INT REFERENCES lava_vocab.archetypes(id),
    confidence TEXT NOT NULL DEFAULT 'medium',
    UNIQUE(run_id, content_sha256, term, numeric_value, section_index)
);

CREATE INDEX IF NOT EXISTS idx_metric_ein ON lava_vocab.metric_observations(source_org_ein);
CREATE INDEX IF NOT EXISTS idx_metric_term ON lava_vocab.metric_observations(term);
CREATE INDEX IF NOT EXISTS idx_metric_run ON lava_vocab.metric_observations(run_id);
CREATE INDEX IF NOT EXISTS idx_metric_archetype ON lava_vocab.metric_observations(archetype_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON lava_vocab.metric_observations TO research_app;
GRANT USAGE, SELECT ON lava_vocab.metric_observations_id_seq TO research_app;

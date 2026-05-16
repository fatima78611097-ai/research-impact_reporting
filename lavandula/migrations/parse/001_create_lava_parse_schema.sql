-- Migration 001: Create lava_parse schema for Docling document parsing
-- Spec 0046: Docling Full-Document Parsing
--
-- This schema stores structured text extracted from PDFs by the Docling
-- GPU worker. It is additive — no existing schemas are modified.
-- Rollback: DROP SCHEMA lava_parse CASCADE;

CREATE SCHEMA IF NOT EXISTS lava_parse;

-- One row per parsed document
CREATE TABLE lava_parse.documents (
    content_sha256 TEXT PRIMARY KEY,
    source_org_ein TEXT NOT NULL,
    parse_version TEXT NOT NULL,
    parsed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    page_count INT NOT NULL,
    section_count INT NOT NULL,
    table_count INT NOT NULL,
    figure_count INT NOT NULL,
    total_text_chars INT NOT NULL,
    parse_duration_ms INT,
    error TEXT,
    metadata_json JSONB
);

-- One row per section/chunk (HierarchicalChunker output)
CREATE TABLE lava_parse.sections (
    id BIGSERIAL PRIMARY KEY,
    content_sha256 TEXT NOT NULL,
    section_index INT NOT NULL,
    heading TEXT,
    heading_level INT,
    body_text TEXT NOT NULL,
    char_count INT NOT NULL,
    page_start INT,
    page_end INT,
    parent_headings TEXT[],
    UNIQUE(content_sha256, section_index)
);

-- One row per table found in document
CREATE TABLE lava_parse.tables (
    id BIGSERIAL PRIMARY KEY,
    content_sha256 TEXT NOT NULL,
    table_index INT NOT NULL,
    section_id BIGINT REFERENCES lava_parse.sections(id) ON DELETE CASCADE,
    page_number INT,
    caption TEXT,
    row_count INT NOT NULL,
    col_count INT NOT NULL,
    data_json JSONB NOT NULL,
    markdown TEXT,
    UNIQUE(content_sha256, table_index)
);

-- Parse run tracking (batch management)
CREATE TABLE lava_parse.parse_runs (
    id SERIAL PRIMARY KEY,
    run_tag TEXT NOT NULL UNIQUE,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    config_json JSONB NOT NULL,
    stats_json JSONB,
    instance_id TEXT
);

-- Indexes
CREATE INDEX idx_sections_sha ON lava_parse.sections(content_sha256);
CREATE INDEX idx_sections_heading ON lava_parse.sections(heading) WHERE heading IS NOT NULL;
CREATE INDEX idx_tables_sha ON lava_parse.tables(content_sha256);
CREATE INDEX idx_documents_org ON lava_parse.documents(source_org_ein);
CREATE INDEX idx_documents_parsed_at ON lava_parse.documents(parsed_at);

-- Grants for application role
GRANT USAGE ON SCHEMA lava_parse TO research_app;
GRANT ALL ON ALL TABLES IN SCHEMA lava_parse TO research_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA lava_parse TO research_app;

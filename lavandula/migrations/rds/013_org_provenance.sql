-- Migration 013: Org Provenance Table
-- Unified pipeline status tracking per EIN across all stages.
-- Lives in lava_pipeline schema (operational, not data).

CREATE SCHEMA IF NOT EXISTS lava_pipeline;

CREATE TABLE lava_pipeline.org_provenance (
    ein TEXT PRIMARY KEY,
    seed_status TEXT NOT NULL DEFAULT 'not_started'
        CHECK (seed_status IN ('not_started','in_progress','completed','failed','not_applicable')),
    seed_completed_at TIMESTAMPTZ,
    resolve_status TEXT NOT NULL DEFAULT 'not_started'
        CHECK (resolve_status IN ('not_started','in_progress','completed','failed','not_applicable')),
    resolve_completed_at TIMESTAMPTZ,
    crawl_status TEXT NOT NULL DEFAULT 'not_started'
        CHECK (crawl_status IN ('not_started','in_progress','completed','failed','not_applicable')),
    crawl_completed_at TIMESTAMPTZ,
    classify_status TEXT NOT NULL DEFAULT 'not_started'
        CHECK (classify_status IN ('not_started','in_progress','completed','failed','not_applicable')),
    classify_completed_at TIMESTAMPTZ,
    filing_990_status TEXT NOT NULL DEFAULT 'not_started'
        CHECK (filing_990_status IN ('not_started','in_progress','completed','failed','not_applicable')),
    filing_990_completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_provenance_resolve ON lava_pipeline.org_provenance(resolve_status);
CREATE INDEX idx_provenance_crawl ON lava_pipeline.org_provenance(crawl_status);
CREATE INDEX idx_provenance_classify ON lava_pipeline.org_provenance(classify_status);
CREATE INDEX idx_provenance_updated ON lava_pipeline.org_provenance(updated_at);

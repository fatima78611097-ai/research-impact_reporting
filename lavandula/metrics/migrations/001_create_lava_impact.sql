-- Metric engine — schema migration 001
-- Creates the lava_impact schema + the metrics table the prod harness writes to.
-- OPERATOR RUNS THIS (Claude cannot apply RDS DDL). Idempotent: safe to re-run.
--
-- Design: purpose-built for the current Metric contract (lavandula/metrics/core/types.py).
-- A clean home, isolated from the deprecated Spec-0049 term tables in lava_vocab.
-- Grants mirror research_app's existing access on lava_vocab.

CREATE SCHEMA IF NOT EXISTS lava_impact;
GRANT USAGE ON SCHEMA lava_impact TO research_app;

-- ── run bookkeeping ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lava_impact.runs (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind         text NOT NULL,              -- 'extract' | 'gate'
    note         text,
    doc_count    integer,
    metric_count integer,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz
);

-- ── the metrics ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lava_impact.metrics (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id          bigint REFERENCES lava_impact.runs(id),
    -- identity
    content_sha256  text NOT NULL,
    idx             integer NOT NULL,         -- position within the doc's metric list
    ein             text,
    report_year     integer,
    url             text,
    -- the metric (statement is the model's own text — never rewritten)
    statement       text NOT NULL,
    value           double precision,
    value_text      text,
    unit            text,
    logic_tier      text,                     -- outcome_impact | reach_output | capacity_input | activity_count | financial
    subject         text,
    -- provenance
    value_ref       text,
    subject_ref     text,
    value_page      integer,
    subject_page    integer,
    value_bbox      jsonb,
    subject_bbox    jsonb,
    same_marker     boolean DEFAULT false,
    source_snippet  text,
    -- gate result
    gate_decision   text,                     -- publish | quarantine
    gate_reason     text,
    gate_flags      jsonb,                     -- ["incomplete","mispair_suspect", ...]
    gate_run_id     bigint REFERENCES lava_impact.runs(id),
    -- audit
    extraction_model text,
    extracted_at    timestamptz NOT NULL DEFAULT now(),
    gated_at        timestamptz,
    -- one row per (run, doc, metric position): re-gating UPDATEs in place
    UNIQUE (run_id, content_sha256, idx)
);

CREATE INDEX IF NOT EXISTS metrics_sha_idx      ON lava_impact.metrics (content_sha256);
CREATE INDEX IF NOT EXISTS metrics_run_idx      ON lava_impact.metrics (run_id);
CREATE INDEX IF NOT EXISTS metrics_decision_idx ON lava_impact.metrics (gate_decision);

-- ── grants: mirror research_app's access on lava_vocab ──────────────────────
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA lava_impact TO research_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA lava_impact TO research_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA lava_impact
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO research_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA lava_impact
    GRANT USAGE, SELECT ON SEQUENCES TO research_app;

-- Migration 0068: Per-metric Docling-location markers + resolved coordinates
-- Spec 0068: Coordinate Handoff (the linchpin — markers resolving to page coords)
--
-- Adds, additively, to lava_vocab.llm_metrics:
--   * value_ref / subject_ref       — the cited Docling-location marker (⟨t#⟩/⟨c#⟩)
--   * value_*/subject_* coords       — server-resolved snapshot at extraction time
--   * marker_resolved                — true iff the value ref resolves to a located element
--   * parse_version / render_version — provenance (which parse + render produced the coords)
--
-- INTERIM table (plan §3 / spec §5.5): these columns are named IDENTICALLY to the
-- 0066 clean-schema target, so the future move is a schema *relocation*, not a
-- re-derivation. 0068 produces fresh rows under its own run_tag; legacy Spec-0051
-- rows stay marker-less by design (no backfill) and are retained read-only.
--
-- Idempotency key already exists on llm_metrics: (run_id, content_sha256). This
-- migration adds NO key column (resolves Codex CRITICAL).
--
-- Provenance NOT NULL (Codex HIGH): parse_version/render_version are NOT NULL. The
-- table has existing rows, so a constant DEFAULT sentinel backfills legacy rows;
-- ADD COLUMN with a *constant* default is a metadata-only change on Postgres 11+
-- (no table rewrite, minimal lock — Codex LOW DDL note). The runner ALWAYS writes
-- explicit real values for new rows; the default only labels pre-0068 legacy rows.
--
-- Strict bbox shape (Gemini MEDIUM jsonb-DoS): a CHECK constrains value_bbox /
-- subject_bbox to exactly {l,t,r,b,coord_origin} — no nested objects / extra keys
-- reach the DB. This mirrors the app-layer guard (marker_resolve._strict_bbox).
--
-- Operator-run migration (single-operator DB, no zero-downtime needed). Apply in a
-- safe window. Rollback: see 0068_metric_markers_rollback.sql.

BEGIN;

-- ============================================================
-- Marker refs (the cited Docling-location ids)
-- ============================================================
ALTER TABLE lava_vocab.llm_metrics
    ADD COLUMN IF NOT EXISTS value_ref   TEXT,
    ADD COLUMN IF NOT EXISTS subject_ref TEXT;

-- ============================================================
-- Server-resolved coordinate snapshot (value)
-- ============================================================
ALTER TABLE lava_vocab.llm_metrics
    ADD COLUMN IF NOT EXISTS value_page  INTEGER,
    ADD COLUMN IF NOT EXISTS value_bbox  JSONB,
    ADD COLUMN IF NOT EXISTS value_row   INTEGER,
    ADD COLUMN IF NOT EXISTS value_col   INTEGER,
    ADD COLUMN IF NOT EXISTS value_table INTEGER;

-- ============================================================
-- Server-resolved coordinate snapshot (subject)
-- ============================================================
ALTER TABLE lava_vocab.llm_metrics
    ADD COLUMN IF NOT EXISTS subject_page  INTEGER,
    ADD COLUMN IF NOT EXISTS subject_bbox  JSONB,
    ADD COLUMN IF NOT EXISTS subject_row   INTEGER,
    ADD COLUMN IF NOT EXISTS subject_col   INTEGER,
    ADD COLUMN IF NOT EXISTS subject_table INTEGER;

-- ============================================================
-- Resolution flag + provenance (NOT NULL with sentinel default for legacy rows)
-- ============================================================
ALTER TABLE lava_vocab.llm_metrics
    ADD COLUMN IF NOT EXISTS marker_resolved BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE lava_vocab.llm_metrics
    ADD COLUMN IF NOT EXISTS parse_version  TEXT NOT NULL DEFAULT 'legacy-unknown';

ALTER TABLE lava_vocab.llm_metrics
    ADD COLUMN IF NOT EXISTS render_version TEXT NOT NULL DEFAULT 'legacy-unknown';

-- ============================================================
-- Strict bbox shape — exactly {l,t,r,b,coord_origin}, no extra/nested keys.
-- (subtracting all 5 keys must leave the empty object, and all 5 must be present.)
-- ============================================================
ALTER TABLE lava_vocab.llm_metrics
    ADD CONSTRAINT llm_metrics_value_bbox_shape CHECK (
        value_bbox IS NULL OR (
            jsonb_typeof(value_bbox) = 'object'
            AND value_bbox ?& array['l','t','r','b','coord_origin']
            AND value_bbox - 'l' - 't' - 'r' - 'b' - 'coord_origin' = '{}'::jsonb
            AND jsonb_typeof(value_bbox->'l') = 'number'
            AND jsonb_typeof(value_bbox->'t') = 'number'
            AND jsonb_typeof(value_bbox->'r') = 'number'
            AND jsonb_typeof(value_bbox->'b') = 'number'
            AND jsonb_typeof(value_bbox->'coord_origin') = 'string'
        )
    );

ALTER TABLE lava_vocab.llm_metrics
    ADD CONSTRAINT llm_metrics_subject_bbox_shape CHECK (
        subject_bbox IS NULL OR (
            jsonb_typeof(subject_bbox) = 'object'
            AND subject_bbox ?& array['l','t','r','b','coord_origin']
            AND subject_bbox - 'l' - 't' - 'r' - 'b' - 'coord_origin' = '{}'::jsonb
            AND jsonb_typeof(subject_bbox->'l') = 'number'
            AND jsonb_typeof(subject_bbox->'t') = 'number'
            AND jsonb_typeof(subject_bbox->'r') = 'number'
            AND jsonb_typeof(subject_bbox->'b') = 'number'
            AND jsonb_typeof(subject_bbox->'coord_origin') = 'string'
        )
    );

-- ============================================================
-- Index for the 0069 gate-input surface (resolved markers per run)
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_llm_metrics_marker_resolved
    ON lava_vocab.llm_metrics(run_id, marker_resolved);

COMMIT;

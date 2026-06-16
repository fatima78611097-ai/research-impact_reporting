-- Rollback for migration 0068: Remove per-metric marker + coordinate fields
-- Run this to undo 0068_metric_markers.sql

BEGIN;

DROP INDEX IF EXISTS lava_vocab.idx_llm_metrics_marker_resolved;

ALTER TABLE lava_vocab.llm_metrics
    DROP CONSTRAINT IF EXISTS llm_metrics_value_bbox_shape,
    DROP CONSTRAINT IF EXISTS llm_metrics_subject_bbox_shape;

ALTER TABLE lava_vocab.llm_metrics
    DROP COLUMN IF EXISTS value_ref,
    DROP COLUMN IF EXISTS subject_ref,
    DROP COLUMN IF EXISTS value_page,
    DROP COLUMN IF EXISTS value_bbox,
    DROP COLUMN IF EXISTS value_row,
    DROP COLUMN IF EXISTS value_col,
    DROP COLUMN IF EXISTS value_table,
    DROP COLUMN IF EXISTS subject_page,
    DROP COLUMN IF EXISTS subject_bbox,
    DROP COLUMN IF EXISTS subject_row,
    DROP COLUMN IF EXISTS subject_col,
    DROP COLUMN IF EXISTS subject_table,
    DROP COLUMN IF EXISTS marker_resolved,
    DROP COLUMN IF EXISTS parse_version,
    DROP COLUMN IF EXISTS render_version;

COMMIT;

-- Rollback for migration 0069: Remove gate decision fields + gate_runs registry
-- Run this to undo 0069_gate_decision.sql
-- (Roll back 0069_published_view.sql and 0069_gate_review.sql FIRST — they depend on
--  these columns / this table.)

BEGIN;

DROP INDEX IF EXISTS lava_vocab.idx_llm_metrics_gate;

ALTER TABLE lava_vocab.llm_metrics
    DROP CONSTRAINT IF EXISTS llm_metrics_gate_decision_chk;

ALTER TABLE lava_vocab.llm_metrics
    DROP COLUMN IF EXISTS gate_decision,
    DROP COLUMN IF EXISTS gate_reason,
    DROP COLUMN IF EXISTS gate_run_id,
    DROP COLUMN IF EXISTS gate_confidence;

DROP TRIGGER IF EXISTS gate_runs_immutable ON lava_vocab.gate_runs;
DROP FUNCTION IF EXISTS lava_vocab.gate_runs_no_mutate();
DROP INDEX IF EXISTS lava_vocab.idx_gate_runs_source;
DROP TABLE IF EXISTS lava_vocab.gate_runs;

COMMIT;

-- Rollback for migration 0069 Phase 6: drop the spot-review table.
-- Run this to undo 0069_gate_review.sql.

BEGIN;

DROP TRIGGER IF EXISTS gate_review_immutable ON lava_vocab.gate_review;
DROP FUNCTION IF EXISTS lava_vocab.gate_review_no_mutate();
DROP INDEX IF EXISTS lava_vocab.idx_gate_review_run;
DROP TABLE IF EXISTS lava_vocab.gate_review;

COMMIT;

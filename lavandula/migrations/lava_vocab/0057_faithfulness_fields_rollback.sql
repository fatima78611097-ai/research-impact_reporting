-- Rollback for migration 0057: Remove faithfulness verification fields
-- Run this to undo 0057_faithfulness_fields.sql

BEGIN;

-- Drop indexes first
DROP INDEX IF EXISTS lava_vocab.idx_llm_metrics_tier;
DROP INDEX IF EXISTS lava_vocab.idx_llm_stories_tier;

-- llm_metrics columns
ALTER TABLE lava_vocab.llm_metrics
    DROP COLUMN IF EXISTS verification_tier,
    DROP COLUMN IF EXISTS grounding_rule,
    DROP COLUMN IF EXISTS grounding_source,
    DROP COLUMN IF EXISTS grounding_offsets,
    DROP COLUMN IF EXISTS context_window,
    DROP COLUMN IF EXISTS tier_b_confidence,
    DROP COLUMN IF EXISTS modality,
    DROP COLUMN IF EXISTS numerator,
    DROP COLUMN IF EXISTS denominator;

-- llm_stories columns
ALTER TABLE lava_vocab.llm_stories
    DROP COLUMN IF EXISTS verification_tier,
    DROP COLUMN IF EXISTS grounding_rule,
    DROP COLUMN IF EXISTS grounding_source,
    DROP COLUMN IF EXISTS grounding_offsets,
    DROP COLUMN IF EXISTS context_window,
    DROP COLUMN IF EXISTS tier_b_confidence;

COMMIT;

-- Migration 0057: Add faithfulness verification fields to llm_metrics and llm_stories
-- Spec 0057: LLM Extraction Faithfulness Verification (Snippet Grounding)
--
-- Adds verification tier, grounding rule/offsets/source, context window,
-- tier-B confidence, modality enum, and numerator/denominator for ratio
-- preservation. All columns are nullable; existing rows default to
-- verification_tier='unverified_legacy'.
--
-- Operator-run migration (single-operator DB, no zero-downtime needed).
-- Rollback: see 0057_faithfulness_fields_rollback.sql

BEGIN;

-- ============================================================
-- llm_metrics: verification + provenance fields
-- ============================================================

-- Verification tier: the gate's verdict
ALTER TABLE lava_vocab.llm_metrics
    ADD COLUMN IF NOT EXISTS verification_tier TEXT
    DEFAULT 'unverified_legacy';

-- Which grounding rule matched (R1/R2/none)
ALTER TABLE lava_vocab.llm_metrics
    ADD COLUMN IF NOT EXISTS grounding_rule TEXT;

-- Which source text the verdict grounded against (docling/pdftotext-repaired)
ALTER TABLE lava_vocab.llm_metrics
    ADD COLUMN IF NOT EXISTS grounding_source TEXT;

-- Character offsets into the source text [[start, end], ...]
ALTER TABLE lava_vocab.llm_metrics
    ADD COLUMN IF NOT EXISTS grounding_offsets JSONB;

-- Surrounding context for display (NOT used in grounding; bounded length)
ALTER TABLE lava_vocab.llm_metrics
    ADD COLUMN IF NOT EXISTS context_window TEXT;

-- Tier-B OCR confidence score (nullable; from 0060 signal)
ALTER TABLE lava_vocab.llm_metrics
    ADD COLUMN IF NOT EXISTS tier_b_confidence NUMERIC;

-- Modality: achieved/proposed/advocated/projected/unspecified
ALTER TABLE lava_vocab.llm_metrics
    ADD COLUMN IF NOT EXISTS modality TEXT DEFAULT 'unspecified';

-- Ratio preservation: numerator and denominator
ALTER TABLE lava_vocab.llm_metrics
    ADD COLUMN IF NOT EXISTS numerator NUMERIC;

ALTER TABLE lava_vocab.llm_metrics
    ADD COLUMN IF NOT EXISTS denominator NUMERIC;

-- Index for filtering by verification tier (published-surface queries)
CREATE INDEX IF NOT EXISTS idx_llm_metrics_tier
    ON lava_vocab.llm_metrics(verification_tier);

-- ============================================================
-- llm_stories: same provenance fields (except numerator/denominator)
-- ============================================================

ALTER TABLE lava_vocab.llm_stories
    ADD COLUMN IF NOT EXISTS verification_tier TEXT
    DEFAULT 'unverified_legacy';

ALTER TABLE lava_vocab.llm_stories
    ADD COLUMN IF NOT EXISTS grounding_rule TEXT;

ALTER TABLE lava_vocab.llm_stories
    ADD COLUMN IF NOT EXISTS grounding_source TEXT;

ALTER TABLE lava_vocab.llm_stories
    ADD COLUMN IF NOT EXISTS grounding_offsets JSONB;

ALTER TABLE lava_vocab.llm_stories
    ADD COLUMN IF NOT EXISTS context_window TEXT;

ALTER TABLE lava_vocab.llm_stories
    ADD COLUMN IF NOT EXISTS tier_b_confidence NUMERIC;

CREATE INDEX IF NOT EXISTS idx_llm_stories_tier
    ON lava_vocab.llm_stories(verification_tier);

-- ============================================================
-- Grants (least-privilege, matching existing pattern)
-- ============================================================

GRANT UPDATE ON lava_vocab.llm_metrics TO research_app;
GRANT UPDATE ON lava_vocab.llm_stories TO research_app;

COMMIT;

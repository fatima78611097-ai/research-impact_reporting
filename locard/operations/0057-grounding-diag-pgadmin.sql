-- Spec 0057 amendment — grounding diagnostics column.
-- Run in pgAdmin as the owner/master role. Idempotent.
-- Adds grounding_diag (JSONB) to llm_metrics + llm_stories so EVERY verified/
-- quarantined fact records WHY: {word_coverage, longest_run, table_coverage,
-- source_chars}. Turns the boolean gate into a debuggable one — the quarantine
-- bucket self-categorizes (fragmentation vs garble/fabrication vs missing-source
-- vs table-artifact) via a plain query, no re-fetching/sampling.

BEGIN;
ALTER TABLE lava_vocab.llm_metrics ADD COLUMN IF NOT EXISTS grounding_diag JSONB;
ALTER TABLE lava_vocab.llm_stories ADD COLUMN IF NOT EXISTS grounding_diag JSONB;
GRANT UPDATE (grounding_diag) ON lava_vocab.llm_metrics TO research_app;
GRANT UPDATE (grounding_diag) ON lava_vocab.llm_stories TO research_app;
COMMIT;

-- verify:
-- SELECT column_name FROM information_schema.columns
--  WHERE table_schema='lava_vocab' AND table_name IN ('llm_metrics','llm_stories')
--    AND column_name='grounding_diag';   -- expect 2 rows

-- ROLLBACK (if needed):
-- BEGIN;
--   ALTER TABLE lava_vocab.llm_metrics DROP COLUMN IF EXISTS grounding_diag;
--   ALTER TABLE lava_vocab.llm_stories DROP COLUMN IF EXISTS grounding_diag;
-- COMMIT;

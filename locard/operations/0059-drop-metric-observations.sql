-- Spec 0059 — drop the superseded statistical metric extractor's table.
-- Run in pgAdmin as the owner/master role, ONLY after confirming nothing reads
-- lava_vocab.metric_observations (verified 2026-05-31: sole writer is the dead
-- extract_metrics.py, not in STAGE_REGISTRY; read by nothing; superseded by
-- lava_vocab.llm_metrics). 1,551,882 rows.
BEGIN;
DROP TABLE IF EXISTS lava_vocab.metric_observations;
COMMIT;
-- verify: SELECT to_regclass('lava_vocab.metric_observations');  -- expect (null)

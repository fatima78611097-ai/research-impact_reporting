-- Rollback for migration 014: classification_context, classification_runs, classification_results
BEGIN;

ALTER TABLE lava_corpus.corpus DROP COLUMN IF EXISTS v3_material_type;
ALTER TABLE lava_corpus.corpus DROP COLUMN IF EXISTS v3_confidence;
ALTER TABLE lava_corpus.corpus DROP COLUMN IF EXISTS v3_reasoning;
ALTER TABLE lava_corpus.corpus DROP COLUMN IF EXISTS v3_classified_at;
ALTER TABLE lava_corpus.corpus DROP COLUMN IF EXISTS v3_run_tag;
ALTER TABLE lava_corpus.corpus DROP COLUMN IF EXISTS v3_classified_by;

DROP TABLE IF EXISTS lava_corpus.classification_results;
DROP TABLE IF EXISTS lava_corpus.classification_runs;
DROP TABLE IF EXISTS lava_corpus.classification_context;

DELETE FROM lava_corpus.schema_version WHERE version = 14;

COMMIT;

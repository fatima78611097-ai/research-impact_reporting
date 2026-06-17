-- Rollback for migration 0069 Phase 5: drop the published view.
-- Run this to undo 0069_published_view.sql. Idempotent — dropping the view cascades its
-- grants, so no explicit REVOKE/GRANT-restore is needed (and none is attempted against a
-- role that may not exist).

BEGIN;

DROP VIEW IF EXISTS lava_vocab.published_metrics;

COMMIT;

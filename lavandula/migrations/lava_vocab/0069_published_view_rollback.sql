-- Rollback for migration 0069 Phase 5: drop the published view + restore raw grants.
-- Run this to undo 0069_published_view.sql.

BEGIN;

REVOKE SELECT ON lava_vocab.published_metrics FROM dashboard_user1;
DROP VIEW IF EXISTS lava_vocab.published_metrics;

-- Restore the product role's prior read on raw llm_metrics (pre-0069 behavior).
GRANT SELECT ON lava_vocab.llm_metrics TO dashboard_user1;

COMMIT;

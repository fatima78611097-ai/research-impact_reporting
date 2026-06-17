-- Migration 0069 (Phase 5): Published-data contract — the verified-only slot view
-- Spec 0069 §5.6. Depends on 0069_gate_decision.sql (gate_* columns).
--
-- The product reads ONLY this view. It exposes a FIXED slot column set (no metric_text /
-- composed sentence) for rows the gate decided 'publish'. "Active run" is INTRINSIC: the
-- gate_* columns hold each metric's CURRENT decision (re-gating UPDATEs them in place),
-- so the view needs NO run-join and can never surface a superseded gate_run_id (plan §5,
-- resolves Codex active-run / Gemini multi-run-state).
--
-- ACCESS BOUNDARY (Gemini HIGH / S6 — corrected per architect review): the dashboard
-- currently connects as research_app (rds-app-user) — the WRITER — so a REVOKE on the
-- unused dashboard_user1 role was a no-op and we cannot revoke from the writer itself.
-- We therefore (a) GRANT SELECT on the view to research_ro (the existing read-only role)
-- AND to research_app so the repointed org-detail query works today, and (b) enforce the
-- boundary at the QUERY layer: the product reads ONLY published_metrics (OrgDetailView),
-- never raw llm_metrics.
--   FOLLOW-UP (operator, recorded): to make the boundary a true *privilege* boundary,
--   repoint the dashboard DB connection to research_ro (read-only) — then research_ro has
--   SELECT on the view but no access to quarantined raw rows. Tracked for 0066/scale.
--
-- Coord columns are NULL only for a text-located publish row (no cell). marker_resolved
-- is implied true by publish (unmarked rows quarantine, §4.1).
--
-- Operator-run. Rollback: 0069_published_view_rollback.sql.

BEGIN;

CREATE OR REPLACE VIEW lava_vocab.published_metrics AS
    SELECT
        content_sha256,
        source_org_ein,
        metric_value,
        metric_type AS label,     -- 0068 stored the slot label in metric_type
        unit,
        geo_impact,
        value_ref,
        value_page,
        value_bbox,
        value_row,
        value_col,
        gate_run_id
    FROM lava_vocab.llm_metrics
    WHERE gate_decision = 'publish';

-- ============================================================
-- Grants: the read-only role + the current app reader can SELECT the view. No REVOKE on
-- raw llm_metrics (research_app is the writer and must keep access; see header).
-- ============================================================
GRANT SELECT ON lava_vocab.published_metrics TO research_ro;
GRANT SELECT ON lava_vocab.published_metrics TO research_app;

COMMIT;

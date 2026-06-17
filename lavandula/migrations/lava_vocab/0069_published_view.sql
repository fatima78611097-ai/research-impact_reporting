-- Migration 0069 (Phase 5): Published-data contract — the verified-only slot view
-- Spec 0069 §5.6. Depends on 0069_gate_decision.sql (gate_* columns).
--
-- The product reads ONLY this view. It exposes a FIXED slot column set (no metric_text /
-- composed sentence) for rows the gate decided 'publish'. "Active run" is INTRINSIC: the
-- gate_* columns hold each metric's CURRENT decision (re-gating UPDATEs them in place),
-- so the view needs NO run-join and can never surface a superseded gate_run_id (plan §5,
-- resolves Codex active-run / Gemini multi-run-state).
--
-- LEAST PRIVILEGE (Gemini HIGH / S6): the product role reads the view and is REVOKED from
-- raw llm_metrics, so an injection/logic flaw in the product role cannot read quarantined
-- (raw) rows. Creating the view is not enough — the boundary is the GRANT/REVOKE below.
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
-- Enforce the boundary: product role reads the view, NOT raw llm_metrics.
-- (dashboard_user1 is the product/dashboard DB role.)
-- ============================================================
REVOKE ALL ON lava_vocab.llm_metrics FROM dashboard_user1;
GRANT SELECT ON lava_vocab.published_metrics TO dashboard_user1;

COMMIT;

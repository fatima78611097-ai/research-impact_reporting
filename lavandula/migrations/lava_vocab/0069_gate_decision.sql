-- Migration 0069: Marker-grounded publish/quarantine gate decisions
-- Spec 0069: Precision Gates to Production
--
-- Adds, additively, the per-metric gate decision to lava_vocab.llm_metrics and an
-- append-only gate_runs audit registry. The published view + read-only-role grant land
-- in 0069_published_view.sql (Phase 5); the spot-review table in 0069_gate_review.sql
-- (Phase 6).
--
-- STORAGE MODEL (plan §3, resolves Gemini #1 / Codex #1,#4): gate decisions are
-- SINGLE-VALUED per metric row — the gate_* columns hold the metric's CURRENT decision;
-- re-gating UPDATEs them in place (no per-run decision rows, no delete/insert of metric
-- rows; the 0068 marker payload is never touched). "Active run" is therefore intrinsic
-- (the column IS the current decision), so the published view needs no run-join. The
-- gate_runs registry is the audit trail; gate_run_id on the metric records which run
-- last wrote it.
--
-- RUN INTEGRITY (spec §5.6, Codex CRITICAL): gate_runs.id is GENERATED ALWAYS AS
-- IDENTITY — server-assigned, monotonic, NOT client-supplied and NOT backdatable. A
-- poisoned/backdated run cannot become active (the runner only ever advances to the
-- greatest id; see gate_runner). The row is immutable: an append-only trigger rejects
-- UPDATE/DELETE so a recorded gate run / its provenance cannot be rewritten.
--
-- INTERIM table (plan §3): these columns are named for the 0066 clean-schema target, so
-- the future move is a relocation, not a re-derivation.
--
-- Operator-run migration (single-operator DB, no zero-downtime needed). Apply in a safe
-- window. Rollback: see 0069_gate_decision_rollback.sql.

BEGIN;

-- ============================================================
-- gate_runs — append-only audit registry (server-assigned monotonic id)
-- ============================================================
CREATE TABLE IF NOT EXISTS lava_vocab.gate_runs (
    id            INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_run_id INTEGER NOT NULL REFERENCES lava_vocab.extraction_runs(id),
    gate_version  TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    stats_json    JSONB DEFAULT '{}',
    notes         TEXT
);

CREATE INDEX IF NOT EXISTS idx_gate_runs_source ON lava_vocab.gate_runs(source_run_id);

-- Append-only enforcement (spec §8 immutable audit): block UPDATE/DELETE on gate_runs so
-- a recorded run's identity/timestamp/provenance cannot be rewritten or backdated. The
-- runner writes stats_json once at allocation-finish via a fresh row only if needed; the
-- per-run report is also mirrored to extraction_runs.stats_json (plan §4). To keep the
-- registry strictly append-only we forbid mutation entirely; the report lives on
-- extraction_runs.
CREATE OR REPLACE FUNCTION lava_vocab.gate_runs_no_mutate()
    RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'lava_vocab.gate_runs is append-only (no UPDATE/DELETE)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS gate_runs_immutable ON lava_vocab.gate_runs;
CREATE TRIGGER gate_runs_immutable
    BEFORE UPDATE OR DELETE ON lava_vocab.gate_runs
    FOR EACH ROW EXECUTE FUNCTION lava_vocab.gate_runs_no_mutate();

-- ============================================================
-- Per-metric current gate decision (additive on llm_metrics)
-- ============================================================
ALTER TABLE lava_vocab.llm_metrics
    ADD COLUMN IF NOT EXISTS gate_decision   TEXT,
    ADD COLUMN IF NOT EXISTS gate_reason     TEXT,
    ADD COLUMN IF NOT EXISTS gate_run_id     INTEGER REFERENCES lava_vocab.gate_runs(id),
    ADD COLUMN IF NOT EXISTS gate_confidence NUMERIC;

-- gate_decision is a closed enum (NULL = not yet gated).
ALTER TABLE lava_vocab.llm_metrics
    ADD CONSTRAINT llm_metrics_gate_decision_chk
    CHECK (gate_decision IS NULL OR gate_decision IN ('publish', 'quarantine'));

-- ============================================================
-- Index for the published view / product queries (Gemini)
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_llm_metrics_gate
    ON lava_vocab.llm_metrics(gate_run_id, gate_decision);

-- ============================================================
-- Grants — the runner (research_app) writes gate_* in place + the audit registry.
-- 016 granted SELECT/INSERT/DELETE on llm_metrics; the in-place gate UPDATE needs UPDATE.
-- ============================================================
GRANT UPDATE ON lava_vocab.llm_metrics TO research_app;
GRANT SELECT, INSERT ON lava_vocab.gate_runs TO research_app;

COMMIT;

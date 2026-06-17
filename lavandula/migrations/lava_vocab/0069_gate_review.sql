-- Migration 0069 (Phase 6): Human spot-review records (append-only)
-- Spec 0069 §5.7. Depends on 0069_gate_decision.sql (gate_runs).
--
-- The spot-review grades a frozen, hashed sample of the published set for right-number
-- AND right-label. Records are APPEND-ONLY (Codex HIGH / spec §5.7): a new review is a
-- NEW ROW keyed (gate_run_id, metric_id, reviewer, reviewed_at) — never an overwrite —
-- so a sample can't be relabeled or replayed to fake SLA compliance. Reviewer identity
-- is the authenticated session/shell identity (set by the app, not a spoofable arg).
--
-- Operator-run. Rollback: 0069_gate_review_rollback.sql.

BEGIN;

CREATE TABLE IF NOT EXISTS lava_vocab.gate_review (
    id           INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    gate_run_id  INTEGER NOT NULL REFERENCES lava_vocab.gate_runs(id),
    metric_id    BIGINT  NOT NULL,
    reviewer     TEXT    NOT NULL,
    reviewed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    right_number BOOLEAN NOT NULL,
    right_label  BOOLEAN NOT NULL,
    sample_hash  TEXT,                 -- the frozen-sample content hash this grade belongs to
    notes        TEXT
);

CREATE INDEX IF NOT EXISTS idx_gate_review_run ON lava_vocab.gate_review(gate_run_id);

-- append-only: block UPDATE/DELETE so a grade cannot be rewritten.
CREATE OR REPLACE FUNCTION lava_vocab.gate_review_no_mutate()
    RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'lava_vocab.gate_review is append-only (no UPDATE/DELETE)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS gate_review_immutable ON lava_vocab.gate_review;
CREATE TRIGGER gate_review_immutable
    BEFORE UPDATE OR DELETE ON lava_vocab.gate_review
    FOR EACH ROW EXECUTE FUNCTION lava_vocab.gate_review_no_mutate();

GRANT SELECT, INSERT ON lava_vocab.gate_review TO research_app;

COMMIT;

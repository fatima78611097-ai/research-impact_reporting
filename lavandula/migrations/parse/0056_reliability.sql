-- Spec 0056: Parse Orchestrator Reliability & Observability
-- Operator-run migration. Apply BEFORE deploying new worker tarball.

BEGIN;

CREATE TABLE IF NOT EXISTS lava_parse.worker_heartbeats (
    instance_id     TEXT NOT NULL,
    run_id          INTEGER NOT NULL,
    last_heartbeat  TIMESTAMPTZ NOT NULL DEFAULT now(),
    docs_completed  INTEGER DEFAULT 0,
    current_doc_sha TEXT,
    PRIMARY KEY (instance_id, run_id)
);

ALTER TABLE lava_parse.parse_runs
    ADD COLUMN IF NOT EXISTS exit_reason TEXT;

GRANT SELECT, INSERT, UPDATE ON lava_parse.worker_heartbeats TO docling_writer;
GRANT SELECT ON lava_parse.worker_heartbeats TO research_app;
GRANT UPDATE (exit_reason) ON lava_parse.parse_runs TO docling_writer;

COMMIT;

-- Migration 003: Work queue for multi-instance SKIP LOCKED coordination
-- Spec 0055: Multi-Instance Parse
--
-- Prerequisites: 001 (lava_parse schema), 002 (docling_writer role)
-- Must be run as superuser / table owner on RDS before deploying Spec 0055 code.

-- 1. Create the work queue table
CREATE TABLE IF NOT EXISTS lava_parse.work_queue (
    id              BIGSERIAL PRIMARY KEY,
    run_id          INTEGER NOT NULL REFERENCES lava_parse.parse_runs(id),
    content_sha256  TEXT NOT NULL,
    source_org_ein  TEXT NOT NULL,
    claimed_by      TEXT,
    claimed_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    error           TEXT,
    UNIQUE (run_id, content_sha256)
);

-- Partial index for the unclaimed-row query used by SKIP LOCKED fetch
CREATE INDEX IF NOT EXISTS idx_work_queue_unclaimed
    ON lava_parse.work_queue (run_id)
    WHERE claimed_by IS NULL;

-- 2. Add instance_ids array column to parse_runs
ALTER TABLE lava_parse.parse_runs
    ADD COLUMN IF NOT EXISTS instance_ids TEXT[];

-- 3. Grants for docling_writer (GPU workers): SELECT + UPDATE only
GRANT SELECT, UPDATE ON lava_parse.work_queue TO docling_writer;

-- 4. Grants for dashboard_user1 (orchestrator): full access
GRANT ALL ON lava_parse.work_queue TO dashboard_user1;
GRANT USAGE, SELECT ON SEQUENCE lava_parse.work_queue_id_seq TO dashboard_user1;

-- 5. Grants for dashboard_reader: SELECT only
GRANT SELECT ON lava_parse.work_queue TO dashboard_reader;

-- 6. Enable Row-Level Security
ALTER TABLE lava_parse.work_queue ENABLE ROW LEVEL SECURITY;

-- Workers can only claim unclaimed rows or complete their own claims
CREATE POLICY worker_claim_policy ON lava_parse.work_queue
    FOR UPDATE TO docling_writer
    USING (claimed_by IS NULL OR claimed_by = current_setting('app.worker_id', true));

-- Workers can read all rows (needed for the SKIP LOCKED SELECT)
CREATE POLICY worker_select_policy ON lava_parse.work_queue
    FOR SELECT TO docling_writer
    USING (true);

-- Orchestrator (dashboard_user1) bypasses RLS via permissive policy
CREATE POLICY orchestrator_full_access ON lava_parse.work_queue
    FOR ALL TO dashboard_user1
    USING (true) WITH CHECK (true);

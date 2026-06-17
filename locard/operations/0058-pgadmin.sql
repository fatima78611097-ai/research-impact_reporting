-- ============================================================================
-- Spec 0058 — Parse Performance & Robustness : pgAdmin migration script
-- Run as the master / DDL role (the role that owns lava_parse), in pgAdmin's
-- Query Tool. Run top-to-bottom. Each step is its own transaction.
-- Migration-BEFORE-tarball gate: apply this, then the worker tarball is deployed.
-- Idempotent (IF NOT EXISTS / ON CONFLICT) — safe to re-run.
-- ============================================================================


-- STEP 1 — observability columns on lava_parse.documents -----------------------
BEGIN;

ALTER TABLE lava_parse.documents
    ADD COLUMN IF NOT EXISTS docling_convert_ms INTEGER,
    ADD COLUMN IF NOT EXISTS parse_outcome TEXT;   -- 'ok'|'timeout'|'downgraded'|'error'

CREATE INDEX IF NOT EXISTS idx_documents_parse_outcome
    ON lava_parse.documents (parse_outcome)
    WHERE parse_outcome IS NOT NULL;

GRANT UPDATE (docling_convert_ms, parse_outcome) ON lava_parse.documents TO docling_writer;

COMMIT;


-- STEP 2 — governed quarantine blocklist --------------------------------------
BEGIN;

CREATE TABLE IF NOT EXISTS lava_parse.parse_blocklist (
    content_sha256  TEXT PRIMARY KEY,
    reason          TEXT NOT NULL,
    quarantined_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    quarantined_by  TEXT NOT NULL,          -- operator id, or 'auto:<rule>'
    evidence_json   JSONB
);

GRANT SELECT ON lava_parse.parse_blocklist TO docling_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON lava_parse.parse_blocklist TO research_app;

COMMIT;


-- STEP 3 — quarantine the known poison doc ------------------------------------
-- e038a9e75317ff86… is the 5.6 MB Illustrator annual report whose native parse
-- SEGFAULTS Docling's pdf_parsers.so (exit 139), reproduced in the Phase-0 spike.
BEGIN;

INSERT INTO lava_parse.parse_blocklist
    (content_sha256, reason, quarantined_by, evidence_json)
VALUES
    ('e038a9e75317ff86f1253667f654c2d41f411977712e6d7f44b7bce14aa8746c',
     'segfaults docling pdf_parsers.so (exit 139)',
     'operator',
     '{"spike": "0058", "exit_code": 139, "reproduced": true}'::jsonb)
ON CONFLICT (content_sha256) DO NOTHING;

COMMIT;


-- STEP 4 — verify (run these SELECTs; eyeball the output) ----------------------
-- 4a. columns present:
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'lava_parse' AND table_name = 'documents'
  AND column_name IN ('docling_convert_ms', 'parse_outcome')
ORDER BY column_name;
--   expect 2 rows: docling_convert_ms (integer), parse_outcome (text)

-- 4b. blocklist exists + poison doc quarantined:
SELECT content_sha256, reason, quarantined_by, quarantined_at
FROM lava_parse.parse_blocklist;
--   expect 1 row: the e038a9e7… poison doc

-- ============================================================================
-- ROLLBACK (only if needed)
-- ============================================================================
-- BEGIN;
--   DROP INDEX IF EXISTS lava_parse.idx_documents_parse_outcome;
--   ALTER TABLE lava_parse.documents
--       DROP COLUMN IF EXISTS docling_convert_ms,
--       DROP COLUMN IF EXISTS parse_outcome;
--   DROP TABLE IF EXISTS lava_parse.parse_blocklist;
-- COMMIT;

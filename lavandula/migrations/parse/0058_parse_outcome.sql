-- Spec 0058: Parse Performance & Robustness — observability columns
-- Operator-run migration. Apply BEFORE deploying the new worker tarball
-- (migration-before-tarball gate, same as 0056).
--
-- Adds two nullable columns to lava_parse.documents (existing rows stay NULL =
-- legacy, pre-0058). docling_writer already holds GRANT ALL on lava_parse
-- tables (migration 002), so new columns are covered; the explicit grants below
-- are belt-and-suspenders and match the 0056 pattern.

BEGIN;

ALTER TABLE lava_parse.documents
    -- Time inside Docling convert() alone, vs total parse_duration_ms which also
    -- includes chunking/extract/DB.
    ADD COLUMN IF NOT EXISTS docling_convert_ms INTEGER,
    -- Coarse per-document outcome enum: 'ok' | 'timeout' | 'downgraded' | 'error'.
    -- The fine-grained reason (parse_timeout / parse_oom / parse_crash /
    -- parse_malformed / docling_parse_failed / empty_parse / ...) lives in the
    -- existing `error` column. The dashboard rolls this up per run.
    ADD COLUMN IF NOT EXISTS parse_outcome TEXT;

-- Index for the dashboard per-run rollup.
CREATE INDEX IF NOT EXISTS idx_documents_parse_outcome
    ON lava_parse.documents (parse_outcome)
    WHERE parse_outcome IS NOT NULL;

-- docling_writer already holds table-level ALL on lava_parse tables (migration
-- 002), which covers INSERT of these new columns. This column-level UPDATE grant
-- mirrors the 0056 exit_reason idiom and is belt-and-suspenders.
GRANT UPDATE (docling_convert_ms, parse_outcome) ON lava_parse.documents TO docling_writer;

COMMIT;

-- ============================================================
-- ROLLBACK (run manually if needed)
-- ============================================================
-- BEGIN;
-- DROP INDEX IF EXISTS lava_parse.idx_documents_parse_outcome;
-- ALTER TABLE lava_parse.documents
--     DROP COLUMN IF EXISTS docling_convert_ms,
--     DROP COLUMN IF EXISTS parse_outcome;
-- COMMIT;

-- Spec 0060: Parse Fidelity Verification & pdftotext Repair
-- Operator-run migration. Apply on RDS via psql.
--
-- New table: lava_parse.pdftotext — stores pdftotext-extracted text per document
-- New columns on lava_parse.documents — fidelity scores and text source classification

BEGIN;

-- New table for pdftotext output
CREATE TABLE IF NOT EXISTS lava_parse.pdftotext (
    content_sha256    TEXT PRIMARY KEY,
    pdftotext_version TEXT NOT NULL,
    full_text         TEXT NOT NULL,
    char_count        INTEGER NOT NULL,
    extracted_at      TIMESTAMPTZ DEFAULT now()
);

-- Fidelity scoring and classification columns on documents
ALTER TABLE lava_parse.documents
    ADD COLUMN IF NOT EXISTS pdftotext_coverage  NUMERIC,
    ADD COLUMN IF NOT EXISTS pdftotext_reverse   NUMERIC,
    ADD COLUMN IF NOT EXISTS text_source         TEXT;

-- Index for batch runner queries (find docs not yet extracted)
CREATE INDEX IF NOT EXISTS idx_pdftotext_sha
    ON lava_parse.pdftotext (content_sha256);

-- Index for filtering by text_source classification
CREATE INDEX IF NOT EXISTS idx_documents_text_source
    ON lava_parse.documents (text_source)
    WHERE text_source IS NOT NULL;

-- Grants for application user
GRANT SELECT, INSERT, UPDATE ON lava_parse.pdftotext TO research_app;

COMMIT;

-- ============================================================
-- ROLLBACK (run manually if needed)
-- ============================================================
-- BEGIN;
-- DROP TABLE IF EXISTS lava_parse.pdftotext;
-- ALTER TABLE lava_parse.documents
--     DROP COLUMN IF EXISTS pdftotext_coverage,
--     DROP COLUMN IF EXISTS pdftotext_reverse,
--     DROP COLUMN IF EXISTS text_source;
-- COMMIT;

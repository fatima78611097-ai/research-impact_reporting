-- Metric-grounding / source-receipt: per-page dimensions as a first-class table.
--
-- OPERATOR-APPLIED DDL (Claude cannot apply migrations; single-operator RDS, ronp).
--
-- WHY: a bbox alone cannot be mapped to a pixel rectangle for a source-excerpt
-- overlay without the page's width/height, and landscape pages / two-page spreads
-- are placed wrong without orientation. Stored first-class (not in metadata_json)
-- so it joins cleanly to a metric's (content_sha256, page_no) at scale.
--
-- Backward-compatible: new table; existing rows/code unaffected. Populated ONLY by
-- the docling worker (lavandula/parse/chunking.py _page_dimensions + db.insert_document).
-- Pre-this-migration parses simply have no pages rows until re-parsed.
--
-- Apply order vs tarball: migration FIRST, then deploy the matching worker tarball
-- (a worker INSERTing into a non-existent table would error; the reverse — table
-- present, old worker — is harmless empty).
--
-- GRANTS: a NEW table does NOT inherit the existing table grants, so they are set
-- explicitly to match lava_parse.sections (role: research_app). If the parse worker
-- connects as a different role, grant that role too.

CREATE TABLE IF NOT EXISTS lava_parse.pages (
    content_sha256 text    NOT NULL,
    page_no        integer NOT NULL,
    width          numeric,
    height         numeric,
    orientation    text,
    PRIMARY KEY (content_sha256, page_no)
);

GRANT SELECT, INSERT, UPDATE, DELETE, REFERENCES, TRIGGER, TRUNCATE
    ON lava_parse.pages TO research_app;

COMMENT ON TABLE lava_parse.pages IS
  'Per-page dimensions in page coordinate space: {width, height, orientation}. '
  'Used to map a bbox -> pixel box for source-excerpt overlays and to place boxes '
  'on landscape/spread pages. No rows = parsed before this capture (re-parse to fill).';
COMMENT ON COLUMN lava_parse.pages.orientation IS 'portrait | landscape (derived: width>height).';

-- Verify (expect the table with PK on content_sha256,page_no):
-- SELECT column_name, data_type FROM information_schema.columns
-- WHERE table_schema='lava_parse' AND table_name='pages' ORDER BY ordinal_position;

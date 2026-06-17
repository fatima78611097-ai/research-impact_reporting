-- Infographic / vision-verify router: per-figure (picture) regions, first-class.
--
-- OPERATOR-APPLIED DDL (Claude cannot apply migrations; single-operator RDS, ronp).
--
-- WHY (the load-bearing reason): a value whose bbox sits inside a figure region came from
-- an INFOGRAPHIC, where Docling's text extraction silently drops/mangles digits (a stylized
-- "56" read as "6"). Text-only gates cannot detect that — there is nothing to compare the
-- wrong value against. The ONLY detector is the page image vs the value (vision-verify).
-- So figure regions are the ROUTER: a doc with figures is flagged at intake, and any metric
-- geometrically inside a figure is sent to vision. Infographics also hold the org's headline
-- metrics, so this is where both the most important numbers AND the worst parse errors live.
--
-- Backward-compatible: new table; existing rows/code unaffected. Populated by the docling
-- worker (chunking._picture_locations + db.insert_document). Pre-this parses have no figure
-- rows until re-parsed.
--
-- Apply order: migration FIRST, then deploy the matching worker tarball. Pairs with the
-- PARSE_SCHEMA_VERSION bump to s3 so reparse --min-version 'docling-X.Y.Z+s3' backfills it.
--
-- GRANTS: a NEW table does not inherit grants; set to match lava_parse.sections (research_app).

CREATE TABLE IF NOT EXISTS lava_parse.figures (
    content_sha256 text    NOT NULL,
    figure_index   integer NOT NULL,
    page_no        integer,
    bbox           jsonb,
    PRIMARY KEY (content_sha256, figure_index)
);

CREATE INDEX IF NOT EXISTS figures_sha_page_idx ON lava_parse.figures (content_sha256, page_no);

GRANT SELECT, INSERT, UPDATE, DELETE, REFERENCES, TRIGGER, TRUNCATE
    ON lava_parse.figures TO research_app;

COMMENT ON TABLE lava_parse.figures IS
  'Per-figure (picture) regions {page_no, bbox}. A value bbox inside one of these came from '
  'an infographic -> route to vision-verify (text parse drops digits there). Doc with rows '
  '= infographic present. No rows = parsed before this capture (re-parse to fill).';

-- Verify:
-- SELECT column_name, data_type FROM information_schema.columns
-- WHERE table_schema='lava_parse' AND table_name='figures' ORDER BY ordinal_position;

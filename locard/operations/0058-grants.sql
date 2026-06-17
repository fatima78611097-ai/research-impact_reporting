-- Spec 0058 / 0060 — grants the GPU worker (docling_writer) needs on the new
-- tables. Run in pgAdmin as the owner/master role. Idempotent.
-- WHY: the 0058 worker reads lava_parse.pdftotext (0060) for the conditional-OCR
-- detector; docling_writer was never granted on it -> "permission denied for
-- table pdftotext" -> rollback -> app.worker_id dropped -> claims fail (run 33).
GRANT SELECT ON lava_parse.pdftotext       TO docling_writer;
GRANT SELECT ON lava_parse.parse_blocklist TO docling_writer;
-- verify:
-- SELECT has_table_privilege('docling_writer','lava_parse.pdftotext','SELECT');       -- expect t
-- SELECT has_table_privilege('docling_writer','lava_parse.parse_blocklist','SELECT');  -- expect t

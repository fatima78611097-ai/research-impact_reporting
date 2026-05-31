-- Spec 0058 §3.5: DB-persisted, governed quarantine for known-poison docs.
-- Operator-run migration. Apply BEFORE deploying the new worker tarball.
--
-- This is the SINGLE source of truth for parse exclusion (plan-review — Codex):
-- populate_work_queue does one anti-join against parse_blocklist; there is NO
-- corpus-side flag, to avoid a split-brain exclusion path.
--
-- Quarantine is auditable + reversible (never silent / never a delete of data):
-- every row records reason, who, when, and the triggering evidence; removing a
-- row (db.unquarantine_doc) is a one-step recovery.

BEGIN;

CREATE TABLE IF NOT EXISTS lava_parse.parse_blocklist (
    content_sha256  TEXT PRIMARY KEY,
    reason          TEXT NOT NULL,
    quarantined_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    quarantined_by  TEXT NOT NULL,          -- operator id, or 'auto:<rule>'
    evidence_json   JSONB
);

-- Worker/orchestrator (docling_writer) reads it for the enqueue anti-join.
GRANT SELECT ON lava_parse.parse_blocklist TO docling_writer;
-- Dashboard/operator (research_app) manages entries.
GRANT SELECT, INSERT, UPDATE, DELETE ON lava_parse.parse_blocklist TO research_app;

COMMIT;

-- ============================================================
-- Flag the known poison doc NOW (spec §3.5, plan Phase 4).
-- e038a9e75317ff86... is the 5.6 MB Illustrator annual report whose native
-- parse SEGFAULTS Docling's pdf_parsers.so (exit 139), reproduced in the
-- Phase-0 spike. Replace <FULL-SHA> with the full 64-char content_sha256
-- (resolve via: SELECT content_sha256 FROM lava_corpus.corpus
--               WHERE content_sha256 LIKE 'e038a9e75317ff86%';)
-- or run: python manage.py quarantine_parse_doc --sha e038a9e75317ff86 \
--           --reason "segfaults docling pdf_parsers.so (exit 139)" --by operator
-- ============================================================
-- INSERT INTO lava_parse.parse_blocklist
--     (content_sha256, reason, quarantined_by, evidence_json)
-- VALUES ('<FULL-SHA>',
--         'segfaults docling pdf_parsers.so (exit 139)',
--         'operator',
--         '{"spike": "0058", "exit_code": 139, "reproduced": true}')
-- ON CONFLICT (content_sha256) DO NOTHING;

-- ============================================================
-- ROLLBACK (run manually if needed)
-- ============================================================
-- BEGIN;
-- DROP TABLE IF EXISTS lava_parse.parse_blocklist;
-- COMMIT;

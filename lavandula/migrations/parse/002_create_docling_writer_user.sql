-- Migration 002: Create dedicated least-privilege user for GPU worker
-- Spec 0046: Docling Full-Document Parsing
--
-- The docling_writer user can ONLY write to lava_parse and read the
-- corpus work queue. This limits blast radius if the GPU instance is
-- compromised.
--
-- Prerequisites: lava_parse schema must exist (001 applied first).
-- Must be run as a user with CREATEROLE privilege (e.g., postgres master).

DO $$BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'docling_writer') THEN
    CREATE ROLE docling_writer WITH LOGIN;
END IF;
END$$;

GRANT rds_iam TO docling_writer;

-- Full access to lava_parse (write results)
GRANT USAGE ON SCHEMA lava_parse TO docling_writer;
GRANT ALL ON ALL TABLES IN SCHEMA lava_parse TO docling_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA lava_parse TO docling_writer;

-- Read-only on corpus (for work-queue query)
GRANT USAGE ON SCHEMA lava_corpus TO docling_writer;
GRANT SELECT ON lava_corpus.corpus TO docling_writer;

-- Migration 015: Add 'cross_origin_pdf' to corpus attribution_confidence check constraint
-- Required by Spec 0047 cross-origin PDF recovery
-- Run as table owner (master)

BEGIN;

ALTER TABLE lava_corpus.corpus
  DROP CONSTRAINT corpus_attr_chk;

ALTER TABLE lava_corpus.corpus
  ADD CONSTRAINT corpus_attr_chk CHECK (
    attribution_confidence IN (
      'own_domain',
      'platform_verified',
      'platform_unverified',
      'wayback_archive',
      'cross_origin_pdf'
    )
  );

INSERT INTO lava_corpus.schema_version (version, name)
VALUES (15, 'Add cross_origin_pdf to attribution_confidence check constraint');

COMMIT;

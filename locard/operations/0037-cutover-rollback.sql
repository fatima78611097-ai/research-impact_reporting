-- 0037-cutover-rollback.sql — Symmetric reverse rename with idempotency guards.
--
-- Renames: research_app → app_user1, research_ro → ro_user1
-- Re-running after a successful rollback is a no-op (not an error).
--
-- Usage: psql -h $RDS_ENDPOINT -U $MASTER_USER -d $DB -f 0037-cutover-rollback.sql

BEGIN;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'research_app') THEN
    ALTER ROLE research_app RENAME TO app_user1;
    RAISE NOTICE 'Rolled back research_app → app_user1';
  ELSE
    RAISE NOTICE 'research_app does not exist (already rolled back or never renamed) — no-op';
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'research_ro') THEN
    ALTER ROLE research_ro RENAME TO ro_user1;
    RAISE NOTICE 'Rolled back research_ro → ro_user1';
  ELSE
    RAISE NOTICE 'research_ro does not exist (already rolled back or never renamed) — no-op';
  END IF;
END $$;
COMMIT;

-- Verify
SELECT rolname FROM pg_roles
WHERE rolname IN ('app_user1', 'ro_user1', 'research_app', 'research_ro')
ORDER BY rolname;

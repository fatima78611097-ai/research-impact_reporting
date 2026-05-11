-- 0037-cutover-rollback.sql — Symmetric reverse rename with idempotency guards.
--
-- Renames: research_app → app_user1, research_ro → ro_user1
-- Re-running after a successful rollback is a no-op (not an error).
--
-- Usage: psql -h $RDS_ENDPOINT -U $MASTER_USER -d $DB -f 0037-cutover-rollback.sql

BEGIN;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'research_app')
     AND NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user1') THEN
    ALTER ROLE research_app RENAME TO app_user1;
    RAISE NOTICE 'Rolled back research_app → app_user1';
  ELSIF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user1') THEN
    RAISE NOTICE 'app_user1 already exists — no-op';
  ELSE
    RAISE NOTICE 'research_app does not exist and app_user1 does not exist — nothing to roll back';
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'research_ro')
     AND NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ro_user1') THEN
    ALTER ROLE research_ro RENAME TO ro_user1;
    RAISE NOTICE 'Rolled back research_ro → ro_user1';
  ELSIF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ro_user1') THEN
    RAISE NOTICE 'ro_user1 already exists — no-op';
  ELSE
    RAISE NOTICE 'research_ro does not exist and ro_user1 does not exist — nothing to roll back';
  END IF;
END $$;
COMMIT;

-- Verify
SELECT rolname FROM pg_roles
WHERE rolname IN ('app_user1', 'ro_user1', 'research_app', 'research_ro')
ORDER BY rolname;

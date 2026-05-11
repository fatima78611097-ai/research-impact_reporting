-- 0037-cutover-rollback.sql — Symmetric reverse rename with idempotency guards.
--
-- Renames: research_app → app_user1, research_ro → ro_user1
-- Re-running after a successful rollback is a no-op (not an error).
--
-- Usage: psql -h $RDS_ENDPOINT -U $MASTER_USER -d $DB -f 0037-cutover-rollback.sql

BEGIN;
DO $$ BEGIN
  -- HALT on mixed state: both source and target exist simultaneously
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'research_app')
     AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user1') THEN
    RAISE EXCEPTION 'MIXED STATE: both research_app and app_user1 exist — '
                    'operator must investigate per spec §Rollback Decision Tree';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'research_ro')
     AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ro_user1') THEN
    RAISE EXCEPTION 'MIXED STATE: both research_ro and ro_user1 exist — '
                    'operator must investigate per spec §Rollback Decision Tree';
  END IF;

  -- Rollback or no-op
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'research_app') THEN
    ALTER ROLE research_app RENAME TO app_user1;
    RAISE NOTICE 'Rolled back research_app → app_user1';
  ELSIF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user1') THEN
    RAISE NOTICE 'app_user1 already exists — no-op';
  ELSE
    RAISE NOTICE 'Neither research_app nor app_user1 exist — nothing to roll back';
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'research_ro') THEN
    ALTER ROLE research_ro RENAME TO ro_user1;
    RAISE NOTICE 'Rolled back research_ro → ro_user1';
  ELSIF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ro_user1') THEN
    RAISE NOTICE 'ro_user1 already exists — no-op';
  ELSE
    RAISE NOTICE 'Neither research_ro nor ro_user1 exist — nothing to roll back';
  END IF;
END $$;
COMMIT;

-- Verify
SELECT rolname FROM pg_roles
WHERE rolname IN ('app_user1', 'ro_user1', 'research_app', 'research_ro')
ORDER BY rolname;

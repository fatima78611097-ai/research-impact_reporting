-- 0037-cutover-rename.sql — Transactional role rename with idempotency guards.
--
-- Renames: app_user1 → research_app, ro_user1 → research_ro
-- Re-running after a successful rename is a no-op (not an error).
-- Does NOT touch dashboard_user1 (see 0037-cutover-dashboard-user-drop.sql).
--
-- Usage: psql -h $RDS_ENDPOINT -U $MASTER_USER -d $DB -f 0037-cutover-rename.sql

BEGIN;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user1')
     AND NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'research_app') THEN
    ALTER ROLE app_user1 RENAME TO research_app;
    RAISE NOTICE 'Renamed app_user1 → research_app';
  ELSIF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'research_app') THEN
    RAISE NOTICE 'research_app already exists — no-op';
  ELSE
    RAISE NOTICE 'app_user1 does not exist and research_app does not exist — nothing to rename';
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ro_user1')
     AND NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'research_ro') THEN
    ALTER ROLE ro_user1 RENAME TO research_ro;
    RAISE NOTICE 'Renamed ro_user1 → research_ro';
  ELSIF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'research_ro') THEN
    RAISE NOTICE 'research_ro already exists — no-op';
  ELSE
    RAISE NOTICE 'ro_user1 does not exist and research_ro does not exist — nothing to rename';
  END IF;
END $$;
COMMIT;

-- Verify
SELECT rolname FROM pg_roles
WHERE rolname IN ('app_user1', 'ro_user1', 'research_app', 'research_ro')
ORDER BY rolname;

-- 0037-cutover-dashboard-user-drop.sql
--
-- Operator runs ONLY if pre-check determines dashboard_user1 exists AND has
-- zero GRANTs AND zero non-rds_iam memberships (per spec §"dashboard_user1
-- decision rule").
--
-- Defense-in-depth: re-validates all three conditions before dropping.
-- If any check fails, RAISEs an EXCEPTION (HALT — operator investigates).
--
-- Usage: psql -h $RDS_ENDPOINT -U $MASTER_USER -d $DB -f 0037-cutover-dashboard-user-drop.sql

DO $$
DECLARE
  v_grant_count int;
  v_owned_count int;
  v_noniam_member_count int;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dashboard_user1') THEN
    RAISE NOTICE 'dashboard_user1 does not exist — no-op';
    RETURN;
  END IF;

  -- Check 1: zero non-rds_iam memberships
  SELECT count(*) INTO v_noniam_member_count
  FROM pg_auth_members am
  JOIN pg_roles r ON r.oid = am.member
  JOIN pg_roles m ON m.oid = am.roleid
  WHERE r.rolname = 'dashboard_user1' AND m.rolname <> 'rds_iam';

  IF v_noniam_member_count > 0 THEN
    RAISE EXCEPTION 'dashboard_user1 has % non-rds_iam memberships — HALT per spec',
                    v_noniam_member_count;
  END IF;

  -- Check 2: zero object-privilege grants
  SELECT count(*) INTO v_grant_count
  FROM (
    SELECT 1 FROM information_schema.table_privileges
      WHERE grantee = 'dashboard_user1'
    UNION ALL
    SELECT 1 FROM information_schema.routine_privileges
      WHERE grantee = 'dashboard_user1'
    UNION ALL
    SELECT 1 FROM information_schema.usage_privileges
      WHERE grantee = 'dashboard_user1'
  ) g;

  IF v_grant_count > 0 THEN
    RAISE EXCEPTION 'dashboard_user1 has % object-privilege grants — HALT per spec',
                    v_grant_count;
  END IF;

  -- Check 3: zero objects owned by the role
  SELECT count(*) INTO v_owned_count
  FROM pg_class c JOIN pg_roles r ON c.relowner = r.oid
  WHERE r.rolname = 'dashboard_user1';

  IF v_owned_count > 0 THEN
    RAISE EXCEPTION 'dashboard_user1 owns % objects — HALT', v_owned_count;
  END IF;

  -- All checks passed; safe to drop.
  DROP ROLE dashboard_user1;
  RAISE NOTICE 'dashboard_user1 dropped (was vestigial)';
END $$;

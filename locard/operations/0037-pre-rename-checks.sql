-- 0037-pre-rename-checks.sql
-- Run as RDS instance master role (postgres / rds_superuser) for full output.
-- Steps 1, 3, 4a, 4d work with any IAM-tier role.
-- Steps 2, 4b, 4c require master/rds_superuser (skip if unavailable).
--
-- Usage: psql -h $RDS_ENDPOINT -U $MASTER_USER -d $DB -f 0037-pre-rename-checks.sql

-- Step 1: Determine the universe of roles to act on.
-- Expected: app_user1, ro_user1. dashboard_user1 may or may not appear.
\echo '=== Step 1: Role universe ==='
SELECT rolname FROM pg_roles
WHERE rolname IN ('app_user1', 'ro_user1', 'dashboard_user1')
ORDER BY rolname;

-- Step 2: (OPTIONAL — requires master/rds_superuser) Password encryption check.
-- IAM auth does not use stored passwords; this is informational only.
\echo '=== Step 2: Password encryption (requires master role) ==='
SELECT rolname,
       CASE
         WHEN rolpassword IS NULL THEN '(no stored password — IAM-only)'
         WHEN rolpassword LIKE 'SCRAM-%' THEN 'SCRAM (rename-safe)'
         WHEN rolpassword LIKE 'md5%' THEN 'MD5 (rename invalidates password)'
         ELSE 'unknown'
       END AS password_status
FROM pg_authid
WHERE rolname IN ('app_user1', 'ro_user1', 'dashboard_user1');

-- Step 3: Confirm rds_iam membership.
\echo '=== Step 3: rds_iam membership ==='
SELECT r.rolname, m.rolname AS member_of
FROM pg_roles r
JOIN pg_auth_members am ON r.oid = am.member
JOIN pg_roles m ON am.roleid = m.oid
WHERE r.rolname IN ('app_user1', 'ro_user1', 'dashboard_user1')
  AND m.rolname = 'rds_iam'
ORDER BY r.rolname;

-- Step 4a: Object-privilege snapshot (GRANT statements).
\echo '=== Step 4a: Object privileges — lava_corpus ==='
\dp lava_corpus.*
\echo '=== Step 4a: Object privileges — lava_pipeline ==='
\dp lava_pipeline.*
\echo '=== Step 4a: Object privileges — lava_dashboard ==='
\dp lava_dashboard.*

-- Step 4b: Default-ACL snapshot (ALTER DEFAULT PRIVILEGES).
\echo '=== Step 4b: Default ACLs ==='
SELECT n.nspname, r.rolname AS grantor, d.defaclobjtype, d.defaclacl
FROM pg_default_acl d
LEFT JOIN pg_namespace n ON d.defaclnamespace = n.oid
JOIN pg_roles r ON d.defaclrole = r.oid
WHERE n.nspname IN ('lava_corpus', 'lava_pipeline', 'lava_dashboard')
   OR n.nspname IS NULL
ORDER BY n.nspname, defaclobjtype;

-- Step 4c: Ownership snapshot — objects.
\echo '=== Step 4c: Ownership — objects ==='
SELECT n.nspname, c.relname, c.relkind, r.rolname AS owner
FROM pg_class c
JOIN pg_namespace n ON c.relnamespace = n.oid
JOIN pg_roles r ON c.relowner = r.oid
WHERE n.nspname IN ('lava_corpus', 'lava_pipeline', 'lava_dashboard')
ORDER BY n.nspname, c.relname;

-- Step 4c: Ownership snapshot — schemas.
\echo '=== Step 4c: Ownership — schemas ==='
SELECT n.nspname, r.rolname AS owner
FROM pg_namespace n
JOIN pg_roles r ON n.nspowner = r.oid
WHERE n.nspname IN ('lava_corpus', 'lava_pipeline', 'lava_dashboard')
ORDER BY n.nspname;

-- Step 4d: Membership snapshot.
\echo '=== Step 4d: Memberships ==='
SELECT r.rolname, m.rolname AS member_of
FROM pg_roles r
JOIN pg_auth_members am ON r.oid = am.member
JOIN pg_roles m ON am.roleid = m.oid
WHERE r.rolname IN ('app_user1', 'ro_user1', 'dashboard_user1')
ORDER BY r.rolname, m.rolname;

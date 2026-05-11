#!/usr/bin/env bash
# 0037-snapshot-pre.sh — Capture pre-rename privilege/ownership snapshots.
#
# Must run as the RDS instance master role (NOT as research_app/research_ro).
# Reason: pg_default_acl and pg_class.relowner require master/superuser access.
#
# Required env vars: RDS_ENDPOINT, DB, MASTER_USER, MASTER_PW
# Output: /tmp/0037_*.before.txt (8 files, overwritten on each run)
#
# Usage:
#   export RDS_ENDPOINT=... DB=... MASTER_USER=postgres MASTER_PW=...
#   bash locard/operations/0037-snapshot-pre.sh

set -euo pipefail

: "${RDS_ENDPOINT:?Set RDS_ENDPOINT}" "${DB:?Set DB}"
: "${MASTER_USER:?Set MASTER_USER}" "${MASTER_PW:?Set MASTER_PW}"

export PGPASSWORD="$MASTER_PW"
PSQL_ARGS=(-h "$RDS_ENDPOINT" -U "$MASTER_USER" -d "$DB" --set=sslmode=require -v ON_ERROR_STOP=1 -At)

SUFFIX="before"
PREFIX="/tmp/0037"

FILES=(
  grants_corpus
  grants_pipeline
  grants_dashboard
  routine_privileges
  default_acl
  ownership_objects
  ownership_schemas
  memberships
)

for f in "${FILES[@]}"; do
  OUT="${PREFIX}_${f}.${SUFFIX}.txt"
  if [[ -f "$OUT" ]]; then
    echo "WARNING: overwriting existing $OUT"
  fi
done

echo "Capturing pre-rename snapshots..."

# 4a: Object privileges per schema
psql "${PSQL_ARGS[@]}" -c "\dp lava_corpus.*" > "${PREFIX}_grants_corpus.${SUFFIX}.txt"
psql "${PSQL_ARGS[@]}" -c "\dp lava_pipeline.*" > "${PREFIX}_grants_pipeline.${SUFFIX}.txt"
psql "${PSQL_ARGS[@]}" -c "\dp lava_dashboard.*" > "${PREFIX}_grants_dashboard.${SUFFIX}.txt"

# 4a+: Function (EXECUTE) privileges — \dp does not cover functions
psql "${PSQL_ARGS[@]}" -c "
SELECT routine_schema, routine_name, grantee, privilege_type
FROM information_schema.routine_privileges
WHERE grantee IN ('app_user1','ro_user1','dashboard_user1',
                  'research_app','research_ro')
  AND routine_schema IN ('lava_corpus','lava_pipeline','lava_dashboard')
ORDER BY routine_schema, routine_name, grantee, privilege_type;
" > "${PREFIX}_routine_privileges.${SUFFIX}.txt"

# 4b: Default ACLs
psql "${PSQL_ARGS[@]}" -c "
SELECT n.nspname, r.rolname AS grantor, d.defaclobjtype, d.defaclacl
FROM pg_default_acl d
LEFT JOIN pg_namespace n ON d.defaclnamespace = n.oid
JOIN pg_roles r ON d.defaclrole = r.oid
WHERE n.nspname IN ('lava_corpus','lava_pipeline','lava_dashboard')
   OR n.nspname IS NULL
ORDER BY n.nspname, defaclobjtype;
" > "${PREFIX}_default_acl.${SUFFIX}.txt"

# 4c: Ownership — objects
psql "${PSQL_ARGS[@]}" -c "
SELECT n.nspname, c.relname, c.relkind, r.rolname AS owner
FROM pg_class c
JOIN pg_namespace n ON c.relnamespace = n.oid
JOIN pg_roles r ON c.relowner = r.oid
WHERE n.nspname IN ('lava_corpus','lava_pipeline','lava_dashboard')
ORDER BY n.nspname, c.relname;
" > "${PREFIX}_ownership_objects.${SUFFIX}.txt"

# 4c: Ownership — schemas
psql "${PSQL_ARGS[@]}" -c "
SELECT n.nspname, r.rolname AS owner
FROM pg_namespace n
JOIN pg_roles r ON n.nspowner = r.oid
WHERE n.nspname IN ('lava_corpus','lava_pipeline','lava_dashboard')
ORDER BY n.nspname;
" > "${PREFIX}_ownership_schemas.${SUFFIX}.txt"

# 4d: Memberships
psql "${PSQL_ARGS[@]}" -c "
SELECT r.rolname, m.rolname AS member_of
FROM pg_roles r
JOIN pg_auth_members am ON r.oid = am.member
JOIN pg_roles m ON am.roleid = m.oid
WHERE r.rolname IN ('app_user1','ro_user1','dashboard_user1',
                    'research_app','research_ro')
ORDER BY r.rolname, m.rolname;
" > "${PREFIX}_memberships.${SUFFIX}.txt"

echo "Pre-rename snapshots written:"
for f in "${FILES[@]}"; do
  OUT="${PREFIX}_${f}.${SUFFIX}.txt"
  echo "  $OUT ($(wc -l < "$OUT") lines)"
done
echo "Done."

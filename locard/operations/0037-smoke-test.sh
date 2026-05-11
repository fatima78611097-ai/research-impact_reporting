#!/usr/bin/env bash
# 0037-smoke-test.sh — Post-cutover smoke tests for renamed roles.
#
# Tests IAM-authenticated psql connect, SELECT permission, and INSERT
# permission (with ROLLBACK) for both research_app and research_ro.
#
# Required env vars: RDS_ENDPOINT, DB
# Exit code: 0 if all pass, 1 on first failure.
#
# Usage:
#   export RDS_ENDPOINT=... DB=...
#   bash locard/operations/0037-smoke-test.sh

set -euo pipefail

: "${RDS_ENDPOINT:?Set RDS_ENDPOINT}" "${DB:?Set DB}"

REGION="us-east-1"
FAIL=0

check() {
  local desc="$1"
  shift
  if "$@"; then
    echo "PASS: $desc"
  else
    echo "FAIL: $desc"
    FAIL=1
    return 1
  fi
}

psql_as() {
  local role="$1"
  shift
  local tok
  tok=$(aws rds generate-db-auth-token \
    --hostname "$RDS_ENDPOINT" --port 5432 \
    --username "$role" --region "$REGION")
  PGPASSWORD="$tok" psql -h "$RDS_ENDPOINT" -U "$role" -d "$DB" \
    --set=sslmode=require -v ON_ERROR_STOP=1 "$@"
}

# --- research_ro checks ---
echo "=== research_ro ==="

check "research_ro: identity" \
  psql_as research_ro -Atc "SELECT current_user" \
  | grep -q "research_ro"

check "research_ro: SELECT on lava_corpus.corpus" \
  psql_as research_ro -Atc "SELECT count(*) FROM lava_corpus.corpus LIMIT 1"

# --- research_app checks ---
echo ""
echo "=== research_app ==="

check "research_app: identity" \
  psql_as research_app -Atc "SELECT current_user" \
  | grep -q "research_app"

check "research_app: SELECT on lava_corpus.corpus" \
  psql_as research_app -Atc "SELECT count(*) FROM lava_corpus.corpus LIMIT 1"

# Write check: INSERT into lava_pipeline.org_provenance then ROLLBACK.
echo "--- research_app: write check (INSERT + ROLLBACK) ---"
psql_as research_app <<'EOSQL'
BEGIN;
INSERT INTO lava_pipeline.org_provenance (ein, seed_status, updated_at)
  VALUES ('ZZ-SMOKETEST-37', 'completed', NOW())
  ON CONFLICT (ein) DO NOTHING;
ROLLBACK;
EOSQL
check "research_app: INSERT permission verified (rolled back)" test $? -eq 0

# Verify the smoke row did NOT persist
ROW_COUNT=$(psql_as research_app -Atc \
  "SELECT count(*) FROM lava_pipeline.org_provenance WHERE ein = 'ZZ-SMOKETEST-37'")
check "research_app: smoke row absent after ROLLBACK (count=$ROW_COUNT)" \
  test "$ROW_COUNT" -eq 0

echo ""
if [[ $FAIL -eq 0 ]]; then
  echo "All smoke tests passed."
else
  echo "SOME SMOKE TESTS FAILED — see above."
fi
exit $FAIL

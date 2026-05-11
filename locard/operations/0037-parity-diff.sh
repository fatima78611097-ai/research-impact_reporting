#!/usr/bin/env bash
# 0037-parity-diff.sh — Verify privilege parity across the rename.
#
# Reads /tmp/0037_*.before.txt and /tmp/0037_*.after.txt,
# applies sed substitution (old→new role names) to the before files,
# then diffs against after files. Zero diff = parity preserved.
#
# If --dashboard-dropped is passed, dashboard_user1 rows are filtered
# from before-snapshots before diffing (the DROP was intentional, not
# a privilege regression).
#
# Implements spec AC #6 (privilege parity across all 4 dimensions / 7 files).
#
# Usage: bash locard/operations/0037-parity-diff.sh [--dashboard-dropped]
# Exit code: 0 if all pass, 1 if any diff is non-empty.

set -uo pipefail

DASHBOARD_DROPPED=0
if [[ "${1:-}" == "--dashboard-dropped" ]]; then
  DASHBOARD_DROPPED=1
  echo "NOTE: --dashboard-dropped flag set; filtering dashboard_user1 from before-snapshots"
fi

PREFIX="/tmp/0037"
FAIL=0

FILES=(
  grants_corpus
  grants_pipeline
  grants_dashboard
  default_acl
  ownership_objects
  ownership_schemas
  memberships
)

for f in "${FILES[@]}"; do
  BEFORE="${PREFIX}_${f}.before.txt"
  AFTER="${PREFIX}_${f}.after.txt"

  if [[ ! -f "$BEFORE" ]]; then
    echo "FAIL: $f — missing $BEFORE"
    FAIL=1
    continue
  fi
  if [[ ! -f "$AFTER" ]]; then
    echo "FAIL: $f — missing $AFTER"
    FAIL=1
    continue
  fi

  SED_CMD='s/app_user1/research_app/g; s/ro_user1/research_ro/g'
  if [[ $DASHBOARD_DROPPED -eq 1 ]]; then
    SED_CMD="${SED_CMD}; /dashboard_user1/d"
  fi

  DIFF_OUTPUT=$(sed "$SED_CMD" "$BEFORE" \
    | diff -u - "$AFTER" || true)

  if [[ -n "$DIFF_OUTPUT" ]]; then
    echo "FAIL: $f"
    echo "$DIFF_OUTPUT"
    FAIL=1
  else
    echo "PASS: $f"
  fi
done

exit "${FAIL}"

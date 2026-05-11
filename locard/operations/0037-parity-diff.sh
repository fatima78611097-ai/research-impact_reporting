#!/usr/bin/env bash
# 0037-parity-diff.sh — Verify privilege parity across the rename.
#
# Reads /tmp/0037_*.before.txt and /tmp/0037_*.after.txt,
# applies sed substitution (old→new role names) to the before files,
# then diffs against after files. Zero diff = parity preserved.
#
# Implements spec AC #6 (privilege parity across all 4 dimensions / 7 files).
#
# Usage: bash locard/operations/0037-parity-diff.sh
# Exit code: 0 if all pass, 1 if any diff is non-empty.

set -uo pipefail

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

  DIFF_OUTPUT=$(sed 's/app_user1/research_app/g; s/ro_user1/research_ro/g' "$BEFORE" \
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

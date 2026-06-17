#!/usr/bin/env bash
# Spec 0058 Phase 6 — rebuild the Docling worker code tarball.
#
# Workers fetch s3://lavandula-nonprofit-collaterals/deploy/worker-code.tar.gz and
# extract it into /opt/docling/lib/python3.10/site-packages/ (see
# parse_documents._launch... ). The tarball therefore contains the `lavandula`
# package tree. This bundles the Spec 0058 worker changes AND the already-committed
# NUL `_scrub` fix (chunking.py) which rides this tarball — no separate deploy.
#
# Usage (run from the repo root on a machine with AWS creds):
#   bash locard/operations/0058-build-worker-tarball.sh           # build only
#   bash locard/operations/0058-build-worker-tarball.sh --upload  # build + back up + upload
#
# ALWAYS back up the current tarball before overwriting (never delete the old one
# until the new run is confirmed writing — operator policy).
set -euo pipefail

BUCKET="lavandula-nonprofit-collaterals"
KEY="deploy/worker-code.tar.gz"
OUT="/tmp/worker-code.tar.gz"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

cd "$(git rev-parse --show-toplevel)"

echo "Packaging worker code -> ${OUT}"
# Package ONLY the packages the worker imports (parse, common, faithfulness) +
# the top-level package marker. The full lavandula/ tree is NOT safe to tar: it
# contains reports/logs (multi-GB crawler_decisions.jsonl), nonprofits/data
# (a ~200 MB SQLite db), and per-subpackage venvs — none of which belong on a
# worker. Explicit includes keep the tarball ~200 KB and junk-free.
tar -czf "${OUT}" \
    --exclude='*/__pycache__' \
    --exclude='*/tests' \
    --exclude='*.pyc' \
    lavandula/__init__.py \
    lavandula/parse \
    lavandula/common \
    lavandula/faithfulness

echo "Built ${OUT}:"
tar -tzf "${OUT}" | grep -E 'parse/(parse_runner|worker|chunking|db|config)\.py' || {
    echo "ERROR: expected Spec 0058 worker modules missing from tarball" >&2
    exit 1
}

if [[ "${1:-}" == "--upload" ]]; then
    echo "Backing up current s3://${BUCKET}/${KEY} -> deploy/backups/worker-code-${STAMP}.tar.gz"
    aws s3 cp "s3://${BUCKET}/${KEY}" "s3://${BUCKET}/deploy/backups/worker-code-${STAMP}.tar.gz" || \
        echo "(no existing tarball to back up — first deploy?)"
    echo "Uploading new tarball"
    aws s3 cp "${OUT}" "s3://${BUCKET}/${KEY}"
    echo "Done. New tarball live at s3://${BUCKET}/${KEY} (backup stamp ${STAMP})."
else
    echo "Build-only. Re-run with --upload to back up + publish."
fi

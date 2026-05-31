# Spec 0058 — Phase-0 Spike (operator-run, GPU only)

**Throwaway** code that gates the Phase-2 architecture. Docling will not run on
the CPU builder worktree — this **must** run on a g6 GPU instance in the venv
that has `docling==2.93.0` + `boto3` with S3 read access to the corpus bucket.

## What it answers (two-factor gate)
1. **HANG** — does native `PdfPipelineOptions.document_timeout` interrupt the
   `e038a9e75317ff86` poison-doc hang within `T + 30s` across ≥3 runs, leaving
   the worker able to parse a normal doc immediately after?
2. **MEMORY** — what is peak host RSS + GPU memory on the poison doc and the
   46–49 MB/page extremes? (A native timeout does not bound memory.)

The verdict decides whether the builder ships **§3.2 native timeout** or the
heavier **§3.3 subprocess + RLIMIT_AS** path. Do **not** build both.

## Run

```bash
# In the GPU venv. Pick any known-good text-native sha for --recovery
# (e.g. a doc that already parsed fine in run 32).
python spike_timeout.py \
    --poison e038a9e75317ff86 \
    --extreme de8fecc5 --extreme 658d9b89 \
    --recovery <known-good-sha> \
    --timeout 60 --runs 3 \
    --out spike_results.json
```

SHAs accept either a full 64-char sha256 or a unique prefix (resolved via S3
`list_objects_v2`). If a prefix is ambiguous, pass the full sha.

To resolve the §1.3 prefixes to full shas from the DB instead (optional):

```sql
SELECT content_sha256 FROM lava_corpus.corpus
WHERE content_sha256 LIKE 'e038a9e75317ff86%';
```

## Output
- `spike_results.json` — machine-readable measurements + derived PASS/FAIL hints.
- `RESULTS.md` — **you fill this in.** It is the human verdict that gates Phase 2.

## After running
Complete `RESULTS.md` (§4 final verdict + chosen `PARSE_TIMEOUT_SECONDS`) and
hand the verdict back to the builder. Then this directory can be deleted — it is
not part of the shipped package.

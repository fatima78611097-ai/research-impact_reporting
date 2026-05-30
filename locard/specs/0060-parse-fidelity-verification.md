# Spec 0060 — Parse Fidelity Verification & pdftotext Repair

- **Project:** 0060
- **Status:** specified (multi-agent review + red-team incorporated; human-approved 2026-05-30)
- **Depends on:** none (0057 consumes 0060's output via SourceTextProvider)
- **Author:** Architect, 2026-05-30

> Link 1 of the integrity chain: verify the parsed text is true to the source PDF. Together with 0057 (link 2: snippet grounded in parsed text), forms an unbroken PDF → text → snippet → published chain of custody.

---

## 1. Problem & Motivation

Docling produces structured text (sections + tables) from PDF documents. We trust this text as ground truth for all downstream extraction and verification. But Docling has two failure modes discovered in spikes:

1. **Layout fragmentation** — designed-layout hero stats (the highest-value content on impact reports) get split across sections. "5,330 Served at the Y in 2022" becomes "5,330" in one section and "Served at the Y in 2022" in another. This is the **primary cause of 0057 quarantine** (97% of the 12,387 quarantined metrics in the p20-v1 failure catalog).

2. **Font garble** — custom/subsetted display fonts render as mojibake (e.g., "o;u ƐƏķƕƏƏ wishes sin1e ƐƖѶƒ" = "over 10,700 wishes since 1983"). pdftotext reads these correctly by applying the font's ToUnicode map. Garbled hero stats are silently dropped by the LLM (incomplete, not wrong) — a recall loss on the most valuable content.

**Evidence:**
- n=1,500 sample: 99.3% text-native (verifiable), 0.3% scanned (Tier B), 0.3% pdftotext-suspect
- Word-set coverage (Docling vs pdftotext): mean 0.938, median 0.964, but a left tail — 15.6% of docs < 0.90, 2.3% < 0.70
- Reverse coverage: mean 0.988 — Docling rarely invents content; it drops
- 0057 failure catalog: 82% metrics verified, 18% quarantined. Of quarantined, ~97% are Docling fragmentation/garble. True LLM fabrication is ~0.4% of total metrics
- **Projected impact:** 0060 pdftotext repair rescues ~10,000 of 12,387 quarantined metrics, pushing the verified rate from 82% toward 96-98%

## 2. Scope

**0060 delivers:**
1. **pdftotext extraction** — run pdftotext against S3-archived PDFs, store the raw text in a new `lava_parse.pdftotext` table
2. **Fidelity scoring** — bidirectional word-set coverage (Docling vs pdftotext) per document, stored in `lava_parse.documents`
3. **PdftextSourceProvider** — a new `SourceTextProvider` implementation that returns pdftotext text instead of Docling sections, plugging into 0057's existing interface
4. **Text-native / scanned classification** — flag each document as text-native (has embedded text layer) or scanned (OCR-only), feeding 0057's Tier A vs Tier B decision
5. **Batch runner** — management command + dashboard button to run pdftotext extraction + fidelity scoring over the corpus

**Non-goals:**
- Replacing Docling entirely (Docling's section structure + tables are valuable; pdftotext provides flat text)
- OCR cross-checking for scanned documents (residual-risk minority, <1% of corpus)
- Modifying the 0057 verifier logic (0060 swaps the source text; 0057's grounding rules are unchanged)

## 3. The Integrity Chain (updated)

```
Crawl ─[0060 inline: pdftotext at archive time]→ certified text in DB
                                                         ↓
PDF ─[Docling parse]→ structured sections/tables          ↓
                                                         ↓
                              ─[0057: deterministic grounding against pdftotext text]→ metric/story ─[disclosure]→ published
```

pdftotext runs at crawl time (no Docling dependency). Fidelity scoring runs after both pdftotext and Docling exist. 0060 provides **two things to 0057:**
- The pdftotext text (a more faithful rendering of the PDF for grounding)
- A certification flag (text-native vs scanned) that determines Tier A vs Tier B

## 4. Requirements

### 4.1 pdftotext extraction

**Two paths — inline at crawl time (primary) + backfill for existing corpus:**

**Primary (inline at crawl):** During the crawl pipeline, the PDF bytes are already on disk before S3 upload. Run `pdftotext -layout {pdf_path} -` on the local file at that point — adds ~0.1s per doc, zero S3 download needed. Store the text in `lava_parse.pdftotext` alongside the S3 archive step. All future crawled docs get pdftotext text captured for free.

**Backfill (one-time batch):** For the ~16K+ documents already crawled and archived in S3, a batch runner downloads from S3 and runs pdftotext. This is a one-time catch-up; once complete, the inline path handles all new docs.

In both paths:
1. Run `pdftotext -layout {pdf_path} -` to extract the embedded text layer
2. Store the full text in `lava_parse.pdftotext` keyed by `content_sha256`
3. If pdftotext produces zero or near-zero output (<50 chars) on a PDF with pages, classify as **scanned** (no embedded text layer)

**pdftotext is deterministic:** same PDF + same pdftotext version → identical output. No AI, no model, no randomness.

**Dependency management:** pdftotext is provided by the `poppler-utils` system package (already installed on cloud2: `pdftotext version 24.02.0`). The extracted `pdftotext_version` column records the binary version per row for reproducibility. No additional installation is needed. If a future environment lacks it, `apt install poppler-utils` is the only prerequisite.

### 4.2 Fidelity scoring

For each text-native document, compute:
- **Forward coverage** (Docling → pdftotext): fraction of Docling word-set present in pdftotext output. Catches Docling **invention** (words Docling produced that aren't in the PDF).
- **Reverse coverage** (pdftotext → Docling): fraction of pdftotext word-set present in Docling output. Catches Docling **omission** (content in the PDF that Docling dropped).

**Word-set tokenization (precise definition):** lowercase → Unicode NFC → collapse all whitespace to single space → strip → split on whitespace → discard tokens ≤ 1 character. This matches the normalization in 0057's `grounding.normalize()` minus the quote/dash folding (not needed for coverage — we're comparing two machine-produced texts, not human vs machine). Punctuation attached to words is kept (e.g., "$5,000" is one token). Coverage = |A ∩ B| / |A| where A and B are word-sets.

Both metrics use **word-set membership**, NOT sequence alignment. Sequence alignment conflates benign reading-order differences with real content divergence (proven: docs with identical content scored align=0.20 from order alone).

Store per-document scores in `lava_parse.documents`:
- `pdftotext_coverage` — forward coverage (Docling fidelity to source)
- `pdftotext_reverse` — reverse coverage (source completeness in Docling)
- `text_source` enum — `text_native | scanned | pdftotext_failed`

### 4.3 PdftextSourceProvider

A new `SourceTextProvider` implementation for 0057's faithfulness gate.

**Fallback logic (per-document, not per-section):**
1. Look up `lava_parse.pdftotext` for the document's `content_sha256`
2. If a row exists AND `text_source = 'text_native'` in `lava_parse.documents` → return pdftotext text with `source = "pdftotext-repaired"`
3. If no pdftotext row exists OR `text_source` is not `text_native` → fall back to `DoclingSourceProvider` (returns Docling text with `source = "docling"`)

**In all cases:** tables come from `lava_parse.tables` (Docling's structured table data). pdftotext produces flat text with no table structure, so Docling tables are always used for R2 matching.

**No mixed-source documents:** a document is either fully pdftotext-sourced or fully Docling-sourced. The `grounding_source` field on each verified fact records which provider was used.

**Certification rule:** a document is **certified** (eligible for Tier A in 0057) when:
- `text_source = 'text_native'`
- pdftotext text exists and is non-empty
- Forward AND reverse coverage are both ≥ 0.50 (both parsers produced meaningful output — the threshold is deliberately low to avoid rejecting docs where Docling dropped significant content, which is the exact scenario we want pdftotext to rescue)

**Scanned documents route to Tier B in 0057** (published with OCR label), not `unverified_pending`. This matches the 0057 spec §4.3: Tier B = scanned/image source, published WITH a label. `unverified_pending` is reserved for docs where 0060 hasn't run yet (pdftotext not extracted).

**Provider decision table (precise, covers every combination):**

| pdftotext row | text_source | coverage ≥ 0.50 | Docling sections | Provider returns | 0057 tier eligibility |
|---|---|---|---|---|---|
| exists | text_native | yes | any | pdftotext text, `source="pdftotext-repaired"` | Tier A (certified) |
| exists | text_native | no | exist | Docling text, `source="docling"` | `unverified_pending` |
| exists | scanned | — | exist | Docling text, `source="docling"` | Tier B (OCR label) |
| exists | scanned | — | missing | None | quarantine |
| exists | pdftotext_failed | — | exist | Docling text, `source="docling"` | `unverified_pending` |
| missing | NULL | — | exist | Docling text, `source="docling"` | `unverified_pending` |
| missing | NULL | — | missing | None | quarantine |

**Borderline cases (narrative):**
- pdftotext exists but coverage < 0.50 in either direction → treat as `pdftotext_failed` (both parsers disagree so severely that neither is trustworthy alone); fall back to Docling; flag for manual review
- pdftotext exists but Docling sections are missing → use pdftotext text directly; this document was never fully parsed by Docling but has a text layer
- Docling exists but pdftotext row is missing → use Docling (0060 hasn't processed this doc yet); `unverified_pending` until 0060 runs

### 4.4 Data model additions

**New table: `lava_parse.pdftotext`**
```sql
content_sha256    TEXT PRIMARY KEY,
pdftotext_version TEXT NOT NULL,
full_text         TEXT NOT NULL,
char_count        INTEGER NOT NULL,
extracted_at      TIMESTAMPTZ DEFAULT now()
```

**New columns on `lava_parse.documents` (all nullable, backfilled by the batch runner):**
```sql
pdftotext_coverage  NUMERIC,     -- forward word-set coverage
pdftotext_reverse   NUMERIC,     -- reverse word-set coverage
text_source         TEXT,        -- 'text_native' | 'scanned' | 'pdftotext_failed'
```

Existing rows remain `NULL` until the batch runner processes them. The `PdftextSourceProvider` treats `NULL text_source` as "not yet processed" and falls back to Docling.

**Migration:** operator-run DDL (same pattern as 0057). Claude produces the SQL; operator applies it. Single-operator DB, no zero-downtime needed.

### 4.5 Batch runner

Management command: `python3 manage.py extract_pdftotext [--run-tag TAG] [--limit N] [--sha SHA]`

Located in `lavandula/dashboard/pipeline/management/commands/extract_pdftotext.py`.

1. Query `lava_parse.documents` for documents not yet in `lava_parse.pdftotext`
2. For each: download PDF from S3 → run pdftotext → store text → compute fidelity scores → update `documents`
3. Progress reporting (every 100 docs)
4. **Resumable:** skips documents already in `lava_parse.pdftotext` (idempotent at row level)
5. **Atomic per-document:** each document's pdftotext row + fidelity scores + text_source classification are written in a single transaction. No partial state.
6. **Partial-failure recovery:** S3 download failures or pdftotext crashes are logged and skipped; the batch continues. Failed documents can be retried with `--sha`.
7. Dashboard integration: button on the Faithfulness Gate page to trigger a batch run

**Temp file cleanup:** PDF downloads use `tempfile.NamedTemporaryFile(delete=True)` so cleanup is automatic, including on crash. The temp file is created, pdftotext reads it, and it's deleted — no accumulation.

**Resource limits:** per-document subprocess timeout (30s), output capture capped at 10MB, temp file in `/tmp` (not accumulating).

**Performance:** pdftotext is fast (~0.1s/doc). For the inline path, it adds negligible time to the crawl pipeline. For the backfill path, S3 download is the bottleneck: ~16K docs in ~1-2 hours, full corpus (~333K) in ~6-8 hours single-threaded.

## 5. Integration with 0057

After 0060 runs:
1. The Faithfulness Gate dashboard page shows a "Re-verify with pdftotext" option
2. Re-running `verify_faithfulness` uses `PdftextSourceProvider` instead of `DoclingSourceProvider`
3. Previously-quarantined metrics that were quarantined due to Docling fragmentation now verify as Tier A against the pdftotext text
4. The `grounding_source` field records `"pdftotext-repaired"` so the provenance is clear

**The swap is a config change**, not a code change — 0057's `SourceTextProvider` protocol was designed for this.

## 6. Acceptance Criteria

1. pdftotext text extracted and stored for **100%** of text-native documents in the target run
2. Fidelity scores computed for all extracted documents
3. Text-native / scanned classification assigned to all documents
4. `PdftextSourceProvider` returns correct `SourceText` objects (pdftotext text + Docling tables + correct `source` tag)
5. Re-running `verify_faithfulness` with `PdftextSourceProvider` on p20-v1 produces **≥ 95% Tier A** metrics (up from 82%)
6. **No regressions:** metrics that were Tier A with Docling source remain Tier A with pdftotext source
7. Per-document fidelity scores available in `lava_parse.documents` for downstream use
8. Scanned documents correctly classified and routed to Tier B (not `verified`)
9. Batch runner is resumable, atomic per-document, reports progress, and handles failures gracefully

**Required test cases:**
- scanned-vs-text-native classification (empty pdftotext output → scanned; non-empty → text-native)
- near-empty pdftotext output (<50 chars on multi-page PDF → scanned)
- pdftotext timeout handling (subprocess killed after 30s → `pdftotext_failed`)
- NUL-byte stripping (0x00 in pdftotext output stripped before INSERT)
- word-set coverage correctness on known fixture (pre-computed expected coverage)
- provider fallback: no pdftotext row → returns Docling text with `source = "docling"`
- provider certified: pdftotext row exists + text_native → returns pdftotext text with `source = "pdftotext-repaired"`
- end-to-end rescue: a metric quarantined under Docling source verifies as Tier A under pdftotext source
- end-to-end no-regression: a metric verified under Docling source remains verified under pdftotext source
- borderline coverage (<0.50) → falls back to Docling, not pdftotext
- malformed PDF handling (pdftotext timeout/crash → `pdftotext_failed`, batch continues)
- `--force` re-extraction overwrites existing pdftotext row
- `--force --version-mismatch` only re-extracts version-mismatched rows
- audit log entry created for dashboard-triggered batch runs

## 7. Security & Abuse Considerations

- **PDF input is untrusted**: pdftotext runs on externally-sourced PDFs. Use `subprocess.run()` with `timeout=30`, `shell=False`, bounded `stdout` capture (10MB cap via `subprocess.PIPE` + length check). No `shell=True`. Use the **absolute path** `/usr/bin/pdftotext` (not a bare `pdftotext` PATH lookup) to prevent path injection.
- **Subprocess isolation (red-team CRITICAL — Codex):** pdftotext runs as a subprocess on cloud2 processing untrusted PDFs. Full container isolation is disproportionate for this single-operator system with no multi-tenant exposure. Mitigations: (a) `subprocess.run()` with `timeout=30` kills runaway processes; (b) `shell=False` prevents injection; (c) absolute binary path prevents PATH hijacking; (d) temp files in a dedicated subdirectory (`/tmp/pdftotext-0060/`) with `NamedTemporaryFile(delete=True)`; (e) set `TMPDIR` env var for the subprocess to the same isolated directory, bounding internal temp file writes; (f) `ulimit` on the subprocess is a plan-phase detail if needed. The operator runs this system; there is no untrusted user access to the dashboard.
- **Malformed PDFs causing runaway CPU/disk:** the 30s timeout covers CPU; the 10MB stdout cap covers memory; the isolated `TMPDIR` bounds disk writes. If pdftotext writes excessive internal temp files, timeout kills it and `TMPDIR` is cleaned up.
- **S3 download**: validate SHA format (`^[a-f0-9]{64}$`) before constructing S3 key. Stream to `NamedTemporaryFile`, don't hold full PDF in memory. S3 bucket has versioning enabled and IAM least-privilege access.
- **Stored text**: strip NUL bytes (0x00) before INSERT — PostgreSQL rejects them in TEXT columns. Same pattern as the parse worker's known fix.
- **No new external access**: pdftotext is a local binary (`/usr/bin/pdftotext`), S3 access uses existing IAM role. No new network calls.
- **Temp file cleanup:** `NamedTemporaryFile(delete=True)` in the isolated `TMPDIR` ensures cleanup on normal exit, exception, or crash.
- **Dashboard authorization (red-team HIGH — Gemini):** the dashboard is behind Tailscale VPN + Django login. Only the operator (`ron`) has access. The batch runner button uses the same `LoginRequiredMixin` + `_log_audit()` pattern as all other pipeline controls. No additional RBAC is needed for this single-operator system.
- **stderr sanitization:** capture `stderr` from pdftotext but do NOT log raw stderr to user-facing surfaces. Log to the server log only, where it's available for debugging but not exposed.
- **Version exposure (red-team HIGH — Gemini):** `pdftotext_version` in the DB is internal (not exposed via API or public surfaces). It serves reproducibility. Keep `poppler-utils` patched per standard system maintenance.

## 8. Failure & Error Scenarios

- **pdftotext crashes/hangs on a PDF** → subprocess timeout after 30s, mark `text_source = 'pdftotext_failed'`, continue batch
- **S3 download fails** → retry once, then skip with error logged, continue batch
- **pdftotext produces empty output on a multi-page PDF** → classify as `scanned`, not `pdftotext_failed` (the PDF has pages but no embedded text layer). Page count determined from `lava_parse.documents.page_count` (set by Docling parse).
- **pdftotext produces empty output on a zero-page PDF** → classify as `pdftotext_failed` (corrupt PDF)
- **Whitespace-only output** → treated as empty (classify as `scanned` or `pdftotext_failed` per page count)
- **Extremely low fidelity score** (< 0.50 both directions) → classify as `pdftotext_failed`; flag for manual review but don't block the batch
- **NUL bytes in output** → strip before INSERT (known issue from parse worker)
- **Encrypted/password-protected PDFs** → pdftotext returns empty or error; classify as `pdftotext_failed`

### 8.1 Version invalidation & re-extraction (red-team HIGH — Codex)

The batch runner's `--force` flag re-extracts documents even if a `lava_parse.pdftotext` row exists. Use cases:
- **pdftotext upgrade:** `--force --version-mismatch` re-extracts only rows where `pdftotext_version` differs from the current binary version
- **Known-bad document:** `--sha SHA --force` re-extracts a specific document
- **Full re-extraction:** `--force` re-extracts all documents (operator decision, not automatic)

The batch runner **always** stores the current `pdftotext_version` on write, so stale versions are detectable. No automatic invalidation — the operator decides when to re-extract. This matches the single-operator model.
- **pdftotext binary missing** → fail fast at command startup with clear error message ("poppler-utils not installed")

## 9. Traps to Avoid

- **Do NOT use sequence alignment** for fidelity scoring — it conflates reading-order with content (proven in spike: identical content scored 0.20 alignment from order alone). Use word-set coverage.
- **Do NOT replace Docling tables** — pdftotext produces flat text with no table structure. Docling tables feed R2 matching in 0057 and must be preserved.
- **Do NOT treat pdftotext as infallible** — 0.3% of corpus has broken-font issues where pdftotext ALSO fails. The `pdftotext_suspect` category exists.
- **Do NOT run pdftotext on GPU instances** — it's CPU-only, runs on cloud2.
- **Do NOT mix sources within a document** — a document is either fully pdftotext-sourced or fully Docling-sourced for grounding purposes. Mixing would make the `grounding_source` field ambiguous.

## 10. Open Questions (plan phase)

- **Parallel S3 downloads** — how many concurrent downloads? (S3 rate limits, memory)
- **Dashboard UX** — fold into the Faithfulness Gate page (preferred) or separate page?
- **Coverage threshold tuning** — 0.50 proposed; tune after seeing corpus-wide distribution
- **Re-extraction trigger** — when pdftotext is upgraded, should we re-run? (Probably yes, unlike Docling upgrades — pdftotext is the ground truth reference)

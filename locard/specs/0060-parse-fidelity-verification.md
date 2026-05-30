# Spec 0060 — Parse Fidelity Verification & pdftotext Repair

- **Project:** 0060
- **Status:** conceived (initial draft)
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
5. **Batch runner** — management command to run pdftotext extraction + fidelity scoring over the corpus

**Non-goals:**
- Replacing Docling entirely (Docling's section structure + tables are valuable; pdftotext provides flat text)
- OCR cross-checking for scanned documents (residual-risk minority, <1% of corpus)
- Modifying the 0057 verifier logic (0060 swaps the source text; 0057's grounding rules are unchanged)

## 3. The Integrity Chain (updated)

```
PDF ─[0060: pdftotext fidelity check]→ certified text ─[0057: deterministic grounding]→ metric/story ─[disclosure]→ published
```

0060 certifies link 1 by providing **two things to 0057:**
- The pdftotext text (a more faithful rendering of the PDF for grounding)
- A certification flag (text-native vs scanned) that determines Tier A vs Tier B

## 4. Requirements

### 4.1 pdftotext extraction

For each document in the corpus (by `content_sha256`):
1. Download the PDF from S3 (`s3://lavandula-nonprofit-collaterals/pdfs/{sha}.pdf`)
2. Run `pdftotext -layout {pdf_path} -` to extract the embedded text layer
3. Store the full text in `lava_parse.pdftotext` keyed by `content_sha256`
4. If pdftotext produces zero or near-zero output (<50 chars) on a PDF with pages, classify as **scanned** (no embedded text layer)

**pdftotext is deterministic:** same PDF + same pdftotext version → identical output. No AI, no model, no randomness.

### 4.2 Fidelity scoring

For each text-native document, compute:
- **Forward coverage** (Docling → pdftotext): fraction of Docling word-set present in pdftotext output. Catches Docling **invention** (words Docling produced that aren't in the PDF).
- **Reverse coverage** (pdftotext → Docling): fraction of pdftotext word-set present in Docling output. Catches Docling **omission** (content in the PDF that Docling dropped).

Both metrics use **word-set membership** (lowercased, whitespace-collapsed), NOT sequence alignment. Sequence alignment conflates benign reading-order differences with real content divergence (proven: docs with identical content scored align=0.20 from order alone).

Store per-document scores in `lava_parse.documents`:
- `pdftotext_coverage` — forward coverage (Docling fidelity to source)
- `pdftotext_reverse` — reverse coverage (source completeness in Docling)
- `text_source` enum — `text_native | scanned | pdftotext_failed`

### 4.3 PdftextSourceProvider

A new `SourceTextProvider` implementation for 0057's faithfulness gate:
- Returns pdftotext text (from `lava_parse.pdftotext`) as the `section_text`
- Returns Docling tables (from `lava_parse.tables`) as-is — pdftotext doesn't produce structured tables
- Sets `source = "pdftotext-repaired"` so 0057 records which text it grounded against
- Falls back to `DoclingSourceProvider` if no pdftotext text exists

**Certification rule:** a document is **certified** (eligible for Tier A in 0057) when:
- `text_source = 'text_native'`
- pdftotext text exists and is non-empty
- Forward AND reverse coverage are both ≥ 0.50 (both parsers produced meaningful output)

Uncertified documents (scanned, failed, or extremely low coverage) get `unverified_pending` in 0057 until manually reviewed or OCR cross-checked.

### 4.4 Data model additions

**New table: `lava_parse.pdftotext`**
```sql
content_sha256  TEXT PRIMARY KEY,
pdftotext_version TEXT NOT NULL,
full_text       TEXT NOT NULL,
char_count      INTEGER NOT NULL,
extracted_at    TIMESTAMPTZ DEFAULT now()
```

**New columns on `lava_parse.documents`:**
```sql
pdftotext_coverage  NUMERIC,     -- forward word-set coverage
pdftotext_reverse   NUMERIC,     -- reverse word-set coverage
text_source         TEXT,        -- 'text_native' | 'scanned' | 'pdftotext_failed'
```

### 4.5 Batch runner

Management command: `python3 manage.py extract_pdftotext [--run-tag TAG] [--limit N] [--sha SHA]`

1. Query corpus for documents not yet in `lava_parse.pdftotext`
2. For each: download PDF from S3 → run pdftotext → store text → compute fidelity scores → update `documents`
3. Progress reporting (every 100 docs)
4. Resumable (skips already-extracted documents)
5. Dashboard integration: button on the Faithfulness Gate page to trigger a batch run

**Performance:** pdftotext is fast (~0.1s/doc). The bottleneck is S3 download. For 3,242 docs in p20-v1, expect ~10-15 minutes. For full corpus (~100K), a few hours with parallel downloads.

## 5. Integration with 0057

After 0060 runs:
1. The Faithfulness Gate page shows a "Re-verify with pdftotext" button
2. Re-running `verify_faithfulness` uses `PdftextSourceProvider` instead of `DoclingSourceProvider`
3. Previously-quarantined metrics that were quarantined due to Docling fragmentation now verify as Tier A against the pdftotext text
4. The `grounding_source` field records `"pdftotext-repaired"` so the provenance is clear

**The swap is a config change**, not a code change — 0057's `SourceTextProvider` protocol was designed for this.

## 6. Acceptance Criteria

1. pdftotext text extracted and stored for **100%** of text-native documents in the target run
2. Fidelity scores computed for all extracted documents
3. Text-native / scanned classification assigned to all documents
4. `PdftextSourceProvider` passes the same 102 verifier tests as `DoclingSourceProvider`
5. Re-running `verify_faithfulness` with `PdftextSourceProvider` on p20-v1 produces **≥ 95% Tier A** metrics (up from 82%)
6. No regressions: metrics that were Tier A with Docling source remain Tier A with pdftotext source
7. Per-document fidelity scores available in `lava_parse.documents` for downstream use
8. Scanned documents correctly classified and routed to `unverified_pending` (not `verified`)
9. Batch runner is resumable, reports progress, and handles S3 download failures gracefully

## 7. Security & Abuse Considerations

- **PDF input is untrusted**: pdftotext runs on externally-sourced PDFs. Use subprocess with timeout (30s per doc), bounded output size (10MB text cap), and no shell=True.
- **S3 download**: validate SHA format before constructing S3 key. Stream to temp file, don't hold in memory.
- **Stored text**: same NUL-byte stripping as the parse worker (PostgreSQL rejects 0x00 in TEXT columns).
- **No new external access**: pdftotext is a local binary, S3 access uses existing IAM role.

## 8. Failure & Error Scenarios

- **pdftotext crashes/hangs on a PDF** → timeout after 30s, mark `text_source = 'pdftotext_failed'`, continue batch
- **S3 download fails** → retry once, then skip with error logged, continue batch
- **pdftotext produces empty output on a multi-page PDF** → classify as `scanned`, not `pdftotext_failed`
- **Extremely low fidelity score** (< 0.30 both directions) → flag for manual review but don't block
- **NUL bytes in output** → strip before INSERT (known issue from parse worker)

## 9. Traps to Avoid

- **Do NOT use sequence alignment** for fidelity scoring — it conflates reading-order with content (proven in spike: identical content scored 0.20 alignment from order alone). Use word-set coverage.
- **Do NOT replace Docling tables** — pdftotext produces flat text with no table structure. Docling tables feed R2 matching in 0057 and must be preserved.
- **Do NOT treat pdftotext as infallible** — 0.3% of corpus has broken-font issues where pdftotext ALSO fails. The `pdftotext_suspect` category exists.
- **Do NOT run pdftotext on GPU instances** — it's CPU-only, runs on cloud2.

## 10. Open Questions (plan phase)

- **Parallel S3 downloads** — how many concurrent downloads? (S3 rate limits, memory)
- **Dashboard UX** — separate page or fold into the existing Faithfulness Gate page?
- **Coverage threshold for certification** — 0.50 proposed; tune after seeing corpus-wide distribution
- **Re-extraction trigger** — when Docling is upgraded, should pdftotext re-run? (Probably not — pdftotext is independent of Docling)

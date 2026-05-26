# Spec 0050: Metric Extraction with Context Snippets

**Status:** Draft
**Author:** Architect
**Created:** 2026-05-26
**Dependencies:** 0049 (Statistical NLP Extraction & Sub-Archetype Discovery)

## Problem Statement

Spec 0049 extracted vocabulary and discovered sub-archetypes — we now know *what terms* each type of org uses and *which orgs* cluster together. But terms alone don't tell us what these orgs actually measure or how they talk about their impact. "Meals served" is a term; "We provided 12,000 meals to families facing food insecurity last year" is a metric with context. The AI interviewer needs the second form — it needs to know not just *what* to ask about, but *how* these orgs phrase their accomplishments.

Impact reports and annual reports consistently pair performance labels with numbers: "families housed: 347", "92% of children met school readiness goals", "our 50 volunteers delivered 12,000 meals." These metric observations are already in the parsed sections (Spec 0046) and the terms are already extracted (Spec 0049). What's missing is the link between them — pairing the term with its number and preserving the surrounding sentence as a phrasing template.

## Goals

1. **Extract metric observations** — term + numeric value pairs found in parsed document sections
2. **Preserve context snippets** — the full sentence or surrounding passage where the metric appears, not just the number
3. **Record provenance** — source document SHA, section index, section heading, org EIN
4. **Link to archetype vocabulary** — prioritize extraction of metrics using terms from the archetype's signature vocabulary (Spec 0049 lift scores)
5. **Store in `lava_vocab` schema** — additive tables, same pattern as 0049
6. **Run on CPU** — same resource constraints as 0049. Must not OOM on a 7.6GB t3.large

## Non-Goals

- Normalizing metric values across orgs (e.g., converting "12K" to "12000") — useful later, not now
- Comparing metrics across years ("grew 15%") — requires multi-document linking per org
- Generating interview questions from metrics — downstream consumer, not this spec
- Real-time extraction — batch pipeline like 0049
- LLM-based extraction — same statistical-only principle from 0049

## Design Principles

1. **Context is the discovery, not the number.** The raw metric (meals=12000) could be guessed from training data. The exact phrasing, framing, and surrounding narrative is what only the corpus can tell us. Optimize for snippet quality.

2. **Sentence is the unit.** Extract the full sentence containing the metric. If the sentence is very short (table cell, bullet point), include the preceding heading as context. If it's part of a paragraph with narrative framing, include up to 500 characters of surrounding text.

3. **Archetype vocabulary guides search.** Don't scan for every possible number in every section. Use the signature terms per archetype (lift > 2.0 from Spec 0049) as the search terms, then look for numbers co-occurring with those terms within the same sentence or section.

4. **Section heading is a feature.** A metric under "Program Outcomes" is more valuable than one under "Auditor's Report." Use heading context to prioritize program/impact sections over financial/administrative ones.

5. **Memory-bounded processing.** Process one document at a time, stream results to the database. No bulk in-memory aggregation. This is a hard constraint given the OOM history on this host.

## Technical Implementation

### Schema Additions (`lava_vocab`)

```sql
CREATE TABLE IF NOT EXISTS lava_vocab.metric_observations (
    id BIGSERIAL PRIMARY KEY,
    run_id INT NOT NULL REFERENCES lava_vocab.extraction_runs(id),
    content_sha256 TEXT NOT NULL,
    source_org_ein TEXT NOT NULL,
    term TEXT NOT NULL,
    numeric_value TEXT NOT NULL,
    numeric_parsed REAL,
    unit_hint TEXT,
    snippet TEXT NOT NULL,
    snippet_heading TEXT,
    section_index INT,
    source_type TEXT NOT NULL DEFAULT 'narrative',
    archetype_id INT REFERENCES lava_vocab.archetypes(id),
    confidence TEXT NOT NULL DEFAULT 'medium',
    UNIQUE(run_id, content_sha256, term, numeric_value, section_index)
);

CREATE INDEX IF NOT EXISTS idx_metric_ein ON lava_vocab.metric_observations(source_org_ein);
CREATE INDEX IF NOT EXISTS idx_metric_term ON lava_vocab.metric_observations(term);
CREATE INDEX IF NOT EXISTS idx_metric_run ON lava_vocab.metric_observations(run_id);
CREATE INDEX IF NOT EXISTS idx_metric_archetype ON lava_vocab.metric_observations(archetype_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON lava_vocab.metric_observations TO research_app;
GRANT USAGE, SELECT ON lava_vocab.metric_observations_id_seq TO research_app;
```

Key columns:
- **`term`**: The vocabulary term associated with the metric. Matched via **substring containment**: the lemmatized term from 0049 must appear as a substring of the lemmatized sentence text. No stemming, synonym expansion, or fuzzy matching — exact substring only. This matches the same canonicalization policy from Spec 0049 (lowercase, lemmatize, single-space join).
- **`numeric_value`**: The raw text of the number exactly as it appears in the source ("12,000", "92%", "$1.2M"). Never normalized or reformatted.
- **`numeric_parsed`**: Best-effort float parse. Rules: strip commas, parse `%` as the number before it (92% → 92.0), parse `$` prefix and K/M/B suffix (e.g., "$1.2M" → 1200000.0). Set to NULL for: ranges ("10-15"), ordinals ("3rd"), dates ("2024"), ratios ("1:3"), or any ambiguous value. NULL is always preferred over a wrong parse.
- **`unit_hint`**: One of: `count`, `percent`, `currency`, `ratio`, or NULL. Detected from the numeric pattern, not from context.
- **`snippet`**: The metric's context passage, max 500 characters. Construction rules below.
- **`snippet_heading`**: The immediate section heading under which this metric appeared. Stored as-is from `lava_parse.sections.heading`.
- **`source_type`**: `narrative` (from section body text), `table` (from Docling-extracted table), or `bullet` (from bullet/list item)
- **`confidence`**: `high` (term + number in same sentence), `medium` (number in sentence, term in section heading), `low` (number in section whose heading matches a term but term not in body text)

### Snippet Construction Rules

The snippet captures the metric in its natural phrasing context. One rule set, no ambiguity:

1. **Start with the sentence** containing the number (detected via spaCy sentencizer or regex fallback on `. ` boundaries)
2. **If the sentence is < 80 characters** (bullet point, table cell, short fragment): prepend the section heading as `"[heading] — "` prefix, then append up to 150 characters of the following sentence for context
3. **If the sentence is >= 80 characters**: use the sentence as-is, no padding
4. **Hard cap at 500 characters** — truncate with `…` if exceeded
5. **Table cells**: use the format `"[heading] — label: value"` where label is the row/column header and value is the cell content

### Term Matching Rules

A term "matches" a sentence when the term's canonical form (from 0049: lowercase, lemmatized, space-joined) appears as a **contiguous substring** within the sentence's lowercased text. No fuzzy matching, no synonym expansion, no stemming beyond the existing 0049 lemmatization.

When multiple terms match a sentence containing a number, emit one metric observation per (term, number) pair. The UNIQUE constraint on (run_id, content_sha256, term, numeric_value, section_index) prevents exact duplicates.

When multiple numbers appear in a sentence with one term, emit one observation per number. Each gets the same snippet (the full sentence) but a different `numeric_value`.

### Table Metric Extraction

Docling stores tables in `lava_parse.tables` with structured row/column data. Tables are a high-confidence metric source because the label-value pairing is explicit.

Processing rules:
1. For each table in a program/impact section (heading filter applies):
   - Identify column headers and row labels
   - For each cell containing a numeric value:
     - The term is the row label (or column header if row label is empty)
     - Match the label against the archetype term list using the same substring rule
     - If matched: emit with `source_type='table'`, `confidence='high'`
     - Snippet format: `"[section heading] — {row_label}: {cell_value}"`
2. Skip tables where > 50% of cells are numeric (likely financial statements)
3. Skip tables with headers matching the financial heading filter

### Extraction Algorithm

```
For each document in the run:
  1. Look up the org's archetype (from archetype_members)
  2. Build term set:
     a. Signature terms for that archetype (lift > 2.0 from compute_lift_per_term)
     b. Top 500 TF-IDF terms for the vertical (fallback for orgs not in an archetype)
     c. Union of both, deduplicated
  3. For each section in the document:
     a. Classify heading: skip (financial/admin), priority (program/impact), neutral
     b. Skip sections classified as financial/admin
     c. Process narrative text:
        - Split body into sentences
        - For each sentence containing a number:
          - Find all matching terms (substring match)
          - For each (term, number) pair: build snippet, emit observation
     d. Process tables (if any in this section):
        - Apply table extraction rules above
  4. Write batch to lava_vocab.metric_observations (per document, not per section)
```

### Number Detection

Regex patterns applied in order (first match wins per position):
- Percentages: `\d+(\.\d+)?%` → "92%", "15.3%"
- Currency: `\$\s?\d[\d,]*(\.\d{1,2})?\s?[KMBkmb]?` → "$1.2M", "$50,000"
- Integers with commas: `\b\d{1,3}(,\d{3})+\b` → "12,000" (requires at least one comma to avoid matching years/IDs)
- Plain integers 3+ digits: `\b\d{3,}\b` → "500", "1234" (but NOT 1-2 digit numbers — too noisy)

Explicitly excluded:
- Years (4-digit numbers 1900-2099 not preceded by `$` or followed by `%`)
- Phone numbers (patterns like `555-1234`, `(555) 555-5555`)
- Dates (patterns like `05/25/2026`)
- Page numbers / section references ("page 12", "section 3")
- ZIP codes (5-digit numbers following state abbreviations)

### Heading Filter

**Block list** (case-insensitive substring match — skip these sections entirely):
- "auditor", "financial statement", "balance sheet", "form 990"
- "board of directors", "staff list", "acknowledgment"
- "table of contents", "notes to financial", "independent auditor"
- "statement of activities", "statement of position"

**Priority list** (case-insensitive — process these first and flag with priority):
- "program", "impact", "outcome", "achievement", "result"
- "service", "community", "client", "participant", "success"

**Neutral** (all other sections): process normally. This avoids false negatives from inconsistent headings — we extract from all non-blocked sections, not only priority ones.

### Management Command

```
python3 manage.py extract_metrics <run_tag> [--ntee P2%] [--batch-size 50] [--resume]
```

Follows the same pattern as `extract_terms`: advisory lock, cursor-based resume, batch writes, progress logging.

### Memory Budget

- Process one document at a time (sections loaded per-doc, released after)
- Term lookup set: ~2000 terms in a Python set — negligible
- Sentence splitting via regex (no spaCy model load required — avoid the 800MB model)
- No bulk aggregation — write each document's metrics directly
- Target: < 300MB peak RSS (conservative given OOM history)

### Security and Data Handling

- **Snippet truncation**: Hard cap at 500 characters prevents storage of unexpectedly large content
- **No PII redaction at extraction time**: Snippets contain org-published report text (already public documents). PII concerns are deferred to the interviewer/display layer where context determines what to show.
- **Input sanitization**: Snippets are parameterized via SQLAlchemy bind parameters — no SQL injection risk. No user-supplied input enters the extraction pipeline.

## Output Example

For a Head Start org's annual report containing:
> "This year, 94% of enrolled children met or exceeded school readiness goals in the area of physical development, up from 87% the previous year."

The extraction produces:
```
term:           "school readiness goal"
numeric_value:  "94%"
numeric_parsed: 94.0
unit_hint:      "percent"
snippet:        "This year, 94% of enrolled children met or exceeded school readiness goals in the area of physical development, up from 87% the previous year."
snippet_heading: "Program Outcomes"
confidence:     "high"
archetype_id:   <Head Start archetype>
```

And a second observation for the "87%" comparison point in the same sentence.

## Downstream Use

### AI Interviewer
- Knows which metrics to ask about per archetype
- Uses real phrasing patterns from snippets as prompt examples
- Can say "Last year you reported 94% school readiness — how did that change?" using actual org language

### Published Lexicon
- Metric frequency across an archetype shows sector benchmarks
- Snippet aggregation reveals phrasing conventions per sub-type

### Report Production
- Snippets serve as templates for how to present metrics
- Section heading patterns inform report structure recommendations

## Testing

### T1: Number Detection Unit Tests

Test the number regex patterns against a fixture of known inputs. Minimum fixture:

| Input | Expected match | `numeric_parsed` | `unit_hint` |
|-------|---------------|-----------------|-------------|
| `"92%"` | `"92%"` | 92.0 | `percent` |
| `"$1.2M"` | `"$1.2M"` | 1200000.0 | `currency` |
| `"$50,000"` | `"$50,000"` | 50000.0 | `currency` |
| `"12,000"` | `"12,000"` | 12000.0 | `count` |
| `"500"` | `"500"` | 500.0 | `count` |
| `"15.3%"` | `"15.3%"` | 15.3 | `percent` |

Non-matches (must NOT be detected as metrics):

| Input | Reason |
|-------|--------|
| `"2024"` | Year |
| `"(555) 555-5555"` | Phone number |
| `"05/25/2026"` | Date |
| `"page 12"` | Page reference |
| `"10-15"` | Range → `numeric_parsed` = NULL |

**Pass criteria**: All matches correct, all non-matches excluded, `numeric_parsed` = NULL for ambiguous values.

### T2: Snippet Construction Unit Tests

Test snippet building against controlled inputs:

1. **Long sentence (>= 80 chars)**: Snippet equals the sentence, no padding
2. **Short sentence (< 80 chars)**: Snippet prepended with `"[heading] — "` and followed by up to 150 chars of next sentence
3. **Table cell**: Snippet format is `"[heading] — label: value"`
4. **500-char truncation**: Input sentence of 600 chars produces snippet of exactly 500 chars ending with `"…"`

**Pass criteria**: All four cases produce snippets matching the construction rules in this spec.

### T3: Term Matching Unit Tests

1. Multi-word term `"school readiness goal"` matches sentence containing `"school readiness goals"` (after lemmatization)
2. Term `"food bank"` matches `"Our food bank served..."` but NOT `"food banking regulations"`
3. Multiple terms matching one sentence produce one observation per (term, number) pair
4. Multiple numbers in one sentence with one term produce one observation per number

**Pass criteria**: Correct match/non-match behavior for all cases.

### T4: Integration Test — Known Documents

Run `extract_metrics` on 5 specific Head Start documents (EINs to be selected from archetype_members where archetype label contains "school readiness"). Verify:

1. At least 3 of 5 documents produce metric observations
2. At least one observation has `term` containing "school readiness" or "enrollment"
3. All observations have non-empty `snippet` (length > 0, length <= 500)
4. All observations have valid `confidence` values (`high`, `medium`, or `low`)
5. All `snippet_heading` values are non-NULL and correspond to actual section headings in the source document
6. No observations from headings matching the block list (e.g., "auditor", "financial statement")

**Pass criteria**: All 6 checks pass.

### T5: Heading Filter Test

Process a document known to contain both program and financial sections. Verify:

1. Sections with headings matching the block list produce zero observations
2. Sections with headings matching the priority list are processed
3. Sections with neutral headings are also processed (not skipped)

**Pass criteria**: Block list sections excluded, all other sections included.

### T6: Memory Test

Run on full P20 vertical with `--ntee P2%`. Monitor peak RSS via `/proc/self/status` VmHWM.

**Pass criteria**: Peak RSS < 500MB. If exceeded, the run must be aborted, not allowed to OOM.

### T7: Resume Test

1. Start a run on P20
2. Kill the process after ~30 seconds
3. Restart with `--resume`
4. Verify: no duplicate observations (UNIQUE constraint), processing continues from the last completed document

**Pass criteria**: Second run completes without UNIQUE violations, final observation count equals what a full uninterrupted run would produce.

### T8: Table Extraction Test

Process a document containing Docling-extracted tables in a program section. Verify:

1. Table cells with numeric values paired with matching term labels produce observations with `source_type='table'`
2. Tables where > 50% of cells are numeric (financial statements) are skipped
3. Table observations have `confidence='high'`

**Pass criteria**: Program tables extracted, financial tables skipped.

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Numbers in financial sections pollute results | Heading filter excludes financial/admin sections |
| Same metric reported multiple times in one doc | UNIQUE constraint deduplicates per section |
| Term appears near unrelated number | Confidence scoring (same-sentence = high, same-section = medium) |
| Memory exhaustion | Per-document processing, no bulk aggregation, 500MB budget |
| Sentence boundary detection errors | Use spaCy sentencizer if loaded, fall back to regex split on `. ` |

## Traps to Avoid

1. **Don't load all observations into memory.** Query per-document, not bulk. We learned this the hard way in 0049.
2. **Don't run FP-Growth or any combinatorial algorithm.** This is linear extraction, not clustering.
3. **Don't parse numbers too aggressively.** Store the raw text as-is in `numeric_value`. The `numeric_parsed` float is best-effort — NULL is better than wrong.
4. **Don't filter too aggressively on archetype terms.** Some orgs may report metrics using common vocabulary, not just their archetype's signature terms. Include top TF-IDF terms as a fallback.
5. **Don't ignore table cells.** Docling extracts tables — metrics in tabular format (label | value) are high-confidence extractions. Handle them as a special case.

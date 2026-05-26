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
    heading_context TEXT[],
    section_index INT,
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
- **`term`**: The vocabulary term associated with the metric (from 0049 observations)
- **`numeric_value`**: The raw text of the number as it appears ("12,000", "92%", "$1.2M")
- **`numeric_parsed`**: Best-effort float parse (12000.0, 92.0, 1200000.0) — NULL if ambiguous
- **`unit_hint`**: Detected unit type: `count`, `percent`, `currency`, `ratio`, or NULL
- **`snippet`**: The full sentence or passage (up to 500 chars) containing the metric
- **`snippet_heading`**: The section heading under which this metric appeared
- **`confidence`**: `high` (term + number in same sentence), `medium` (same section), `low` (heading-inferred)

### Extraction Algorithm

```
For each document in the run:
  1. Look up the org's archetype (from archetype_members)
  2. Get signature terms for that archetype (lift > 2.0)
  3. Also include high-TF-IDF terms for the vertical (top 500)
  4. For each section in the document:
     a. Skip sections with financial/administrative headings
     b. Split section body into sentences
     c. For each sentence:
        - Find numbers (regex: integers, decimals, percentages, currency)
        - Find matching terms from the target term list
        - If both a number and a term appear in the same sentence:
          → Extract as a metric observation (confidence: high)
        - If a number appears in a sentence under a heading that matches a term:
          → Extract with heading as the term (confidence: medium)
     d. Build snippet: the sentence + up to 150 chars before/after for context
  5. Write batch to lava_vocab.metric_observations
```

### Number Detection

Regex patterns for numeric values:
- Integers with commas: `\b\d{1,3}(,\d{3})*\b` → "12,000"
- Percentages: `\d+(\.\d+)?%` → "92%", "15.3%"
- Currency: `\$\d[\d,]*(\.\d{1,2})?[KMB]?` → "$1.2M", "$50,000"
- Spelled fractions: "one-third", "half" (low priority)

### Heading Filter

Skip sections whose headings match financial/administrative patterns:
- "Auditor", "Financial Statement", "Balance Sheet", "Form 990"
- "Board of Directors", "Staff List", "Acknowledgments"
- "Table of Contents", "Notes to Financial"

Prioritize sections whose headings suggest program content:
- "Program", "Impact", "Outcome", "Achievement", "Result"
- "Service", "Community", "Client", "Participant"
- Any heading containing archetype signature terms

### Management Command

```
python3 manage.py extract_metrics <run_tag> [--ntee P2%] [--batch-size 50] [--resume]
```

Follows the same pattern as `extract_terms`: advisory lock, cursor-based resume, batch writes, progress logging.

### Memory Budget

- Process one document at a time (sections loaded per-doc)
- Term lookup set: ~2000 terms (same as archetype matrix) — negligible
- Sentence splitting via spaCy (already loaded for 0049) or simple regex
- No bulk aggregation — write each batch of metric observations directly
- Target: < 500MB peak RSS

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

1. **Unit tests**: Number regex against known patterns (currencies, percentages, plain integers)
2. **Integration test**: Run on 5 known Head Start docs, verify school readiness metrics are extracted with correct snippets
3. **Validation**: Spot-check 20 random metric observations against source documents
4. **Memory test**: Verify peak RSS stays under 500MB on full P20 run
5. **Resume test**: Kill mid-run, verify `--resume` picks up correctly

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

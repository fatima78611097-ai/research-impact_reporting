# Spike 001: Vocabulary Extraction POC

## Hypothesis

Domain-specific vocabulary can be programmatically extracted from nonprofit annual/impact reports and will differentiate archetypes (e.g., food banks vs. homeless shelters vs. family services) when subjected to market basket analysis.

## What We Already Know

- 97K documents in corpus, 25K classified as annual reports, 5K as impact reports
- NTEE P20 (Human Services) has 179 orgs with 883 annual/impact reports — broad enough to contain multiple sub-archetypes
- `first_page_text` (up to 4096 chars) exists on every report
- DeepSeek V4 available for cheap bulk extraction ($0.001/call)
- The vocabulary standardization has already happened organically via trade associations, shared consultants, and grant language — we're measuring it, not hoping for it

## Goal

Validate that extraction + aggregation produces archetype-differentiating vocabulary clusters. Specifically:

1. Can we extract structured vocabulary observations from `first_page_text`?
2. Do the extracted terms cluster into distinct sub-archetypes within P20?
3. Are the clusters meaningful (food bank vs. shelter vs. family services)?
4. Is the signal strong enough to differentiate from generic "nonprofit noise"?

## Approach

### Step 1: Sample Selection

Pull 50-75 reports from P20 orgs with populated `first_page_text`. Select orgs with multiple reports to test within-org consistency. Ensure geographic diversity (not all from one state).

```sql
SELECT c.content_sha256, c.source_org_ein, c.first_page_text,
       s.name, s.ntee_code, s.state
FROM corpus c
JOIN nonprofits_seed s ON c.source_org_ein = s.ein
WHERE c.classification IN ('annual', 'impact')
  AND s.ntee_code LIKE 'P2%'
  AND c.first_page_text IS NOT NULL
  AND LENGTH(c.first_page_text) > 200
ORDER BY RANDOM()
LIMIT 75;
```

### Step 2: Extraction Prompt Design (Critical)

The prompt must extract domain-specific vocabulary, NOT generic nonprofit language. This is the most important design decision in the spike.

**Approach:** Two-pass extraction.

**Pass 1 — Broad extraction:**

```
You are analyzing a section from a nonprofit annual/impact report.
Organization: {org_name}
NTEE Code: {ntee_code}

Identify domain-specific vocabulary in this text. For each term, provide:
- term: the exact phrase as used
- category: one of [stakeholder, metric, outcome, program, methodology]
- evidence_span: the sentence fragment containing the term

Focus on terms that are SPECIFIC to this type of organization's work.
Ignore generic language that any nonprofit would use (impact, community,
mission, stakeholders, donors, volunteers).

Text:
{first_page_text}

Return JSON array.
```

**Pass 2 — Differentiation filter (run after aggregation):**

After collecting all observations, identify terms that appear in >30% of one sub-cluster but <5% of others. These are the archetype-differentiating terms.

### Step 3: Storage

Simple table (can be temporary/SQLite for the spike):

```sql
CREATE TABLE spike_vocab_observations (
    content_sha256 TEXT,
    source_org_ein TEXT,
    term TEXT,
    category TEXT,
    evidence_span TEXT,
    confidence FLOAT
);
```

### Step 4: Market Basket Analysis

Using the observations table:

1. **Frequency by org:** Which terms appear across multiple orgs? (minimum support threshold — term must appear in 3+ orgs to be signal, not noise)
2. **Co-occurrence:** Which terms consistently appear together? (association rules — Apriori or FP-Growth)
3. **Clustering:** Do the orgs naturally group by vocabulary similarity? (simple cosine similarity on term vectors, then k-means or hierarchical clustering with k=3-5)
4. **Differentiation score:** For each resulting cluster, which terms are unique to it? (lift > 2.0 = strong archetype signal)

### Step 5: Validation

Manually inspect the clusters:
- Do they correspond to recognizable archetypes?
- Pick 5 orgs from each cluster — do their names/missions confirm the grouping?
- Are the differentiating terms ones a domain expert would recognize?

## Success Criteria

**PASS if:**
- Extraction produces 10+ distinct terms per report (not all generic)
- 3+ clusters emerge from P20 with meaningful separation
- Each cluster has 5+ terms with lift > 2.0 (strongly archetype-specific)
- Manual inspection confirms clusters map to recognizable org types

**FAIL if:**
- Extraction mostly returns generic nonprofit language despite prompt tuning
- No meaningful clusters emerge (all orgs have the same vocabulary)
- Clusters form by geography or size rather than domain/archetype

## Time Box

2-3 hours. If extraction prompt needs more than 2 iterations to produce useful output, pause and reassess prompt strategy.

## Output

- Extraction prompt (final version that worked)
- Raw observations table (CSV or SQLite)
- Cluster visualization or summary
- PASS/FAIL assessment with evidence
- Lessons learned for Phase 2 spec

## Risk Mitigation

- **first_page_text may be too short** — If 4096 chars doesn't contain enough domain vocabulary, note this as a finding. Full Docling parsing would give more signal, but first_page typically has the executive letter or intro which is vocabulary-rich.
- **DeepSeek may over-extract generic terms** — The prompt explicitly excludes them, but if results are noisy, add a second pass that filters to terms appearing in <50% of all reports (domain-specific by definition).
- **P20 may be too heterogeneous** — If it's so broad that 50 orgs produce 15 clusters of 3 each, that's actually a positive finding (fine-grained archetypes exist) but we'd need a larger sample to validate each cluster.

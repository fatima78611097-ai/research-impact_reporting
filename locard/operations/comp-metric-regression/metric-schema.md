# Canonical metric record schema

The shape every published metric is stored as. Built around our differentiator — **structured,
provable provenance** — plus the two classification tags that let domain experts read the corpus
in their own language: the **logic-model tier** and the **metric type**.

```jsonc
{
  // —— identity ——
  "metric_id":      "<sha8>:<idx>",
  "content_sha256": "<full doc hash>",   // our doc key (orgs file many reports; EIN alone collides)
  "ein":            "<org ein>",
  "report_year":    2025,
  "url":            "<report url>",

  // —— the metric ——
  "statement":   "<self-contained sentence>",
  "subject":     "<raw 'what' — 'crisis centers operated'>",
  "value":       16,
  "value_text":  "16",                   // as-printed ($2.3M / 16 / 14%) — survives unit-scale
  "unit":        "centers | people | USD | percent | hours ...",
  "direction":   "absolute | increase | decrease",
  "temporal":    "annual | cumulative | point-in-time | FY2025",
  "temporal_text":"<raw phrase>",
  "geo":         "<scope>",

  // —— CLASSIFICATION TAGS (populated post-extraction by the two-stage harness, NOT emitted by the model) ——
  "logic_tier":  "input | output | outcome | impact",   // ← the logic-model / theory-of-change tier
  "metric_type": "clients-served | volunteers | dollars | facilities ...",  // normalized category (taxonomy layer)
  "measurable":  true,                   // measurable-value check: false = not a real metric (event-1, multiplier)

  // —— PROVENANCE (the moat — structured, not a loose snippet) ——
  "provenance": {
    "page": 1,
    "value_ref": "<marker id>", "subject_ref": "<marker id>",
    "value_bbox": {…}, "subject_bbox": {…},     // also drives the public-facing highlight
    "charspan": [120, 168],
    "paired_by": "model | geometry",   // 'geometry' = re-paired on a designed page (ENH-003)
    "source_snippet": "<verbatim source text>"
  },

  // —— VERIFICATION ——
  "verification": {
    "gate": "published | quarantined",
    "grounded": true,
    "review": "gate-correct | false-quarantine | correct-unverifiable | ...",
    "cause_stage": null, "failure_mode": null
  },

  // —— EXTRACTION audit ——
  "extraction": { "model": "deepseek-chat", "prompt_sha": "b51dc96a", "extracted_at": "<ts>" },
  "date_added": "<ts>"
}
```

## `logic_tier` — the field that buys credibility (added 2026-06-08)
How nonprofit-evaluation experts (PhDs, M&E pros, funders) sort every number — the **logic model /
theory of change**. Tagged per metric by a classification step (a two-stage harness plug-in, same as
`metric_type` / `measurable`), **not** something the extraction model emits.

| tier | what it is | example | role |
|---|---|---|---|
| `input` | resources put in | **9 vans**, budget, staff | real metric, *lowest* tier (capacity) |
| `output` | what was done / reach | **6 counties served**, 16 centers, 500 clients | bona-fide metric, foundational tier |
| `outcome` | change in the people | 85% improved reading, 70% got jobs | the **KPI** tier — what experts care about |
| `impact` | long-term societal change | reduced regional homelessness | aspirational, hardest to claim |

**KPI ≈ outcome.** So "which metrics are KPIs" is answered by `logic_tier == "outcome"` — a filter, not a
gate. We keep every real metric (inputs included); the tier just shelves it where the field expects.

Pairs with [[project_corpus_inclusion_criteria]] and the metric-type taxonomy (the explorer view).

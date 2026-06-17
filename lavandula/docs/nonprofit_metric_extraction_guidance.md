# Nonprofit Impact Report Metric Extraction Guidance

*Product definition, classification rubric, schema guidance, and development acceptance criteria*

**Purpose.** This document defines what should count as a usable metric extracted from nonprofit impact reports. The goal is not merely to identify numbers. The goal is to produce complete metric statements that can stand on their own for a reader who already knows the nonprofit's mission and the report year, but who does not have the full report in front of them.

**Core development principle.** The system should classify numeric claims, not simply ask whether a number is a metric. The output should separate high-value impact evidence from operational or capacity data, and should reject decorative or misleading numbers such as dates, rankings, awards, labels, durations, forecasts, and event occurrences encoded as counts.

## 1. Executive Summary

- A metric is not just a number. It is a complete numeric claim.
- A usable metric must identify the quantity, the measured subject, the action or relationship, and enough context to be understood without the full report.
- The system should produce complete metric statements rather than bare labels such as "6 counties" or "85% improved."
- The system should classify each candidate into a metric tier and a standalone completeness status.
- The product should preserve lower-tier operational metrics when useful, but should not mix them equally with outcomes or impact metrics.
- Obvious non-metrics should be rejected even when they contain numbers.

## 2. Product Definition of a Usable Metric

**Recommended definition:** A usable metric is a self-contained numeric claim that measures something the nonprofit did, reached, produced, used, changed, or achieved during the reporting year, and that can be understood by a reader who knows only the nonprofit's mission and report year.

**Required structure:** Quantity + measured subject + action/relationship + enough context.

| Element | Meaning | Example |
|---|---|---|
| Quantity | The numeric value, percentage, dollar amount, ratio, or count. | 450, 85%, $2.5M, 4:1 |
| Measured subject | The thing being counted or measured. | clients served, meals distributed, counties reached |
| Action/relationship | What happened to or through that subject. | served, distributed, improved, operated, partnered with |
| Context | Enough detail to make the claim understandable without the report. | to families, across counties, through mobile clinics |

**Examples of complete metric statements:**

- Served residents across 6 counties.
- Provided legal assistance to 450 tenants.
- Distributed 12,000 meals to families.
- Operated 9 mobile service vans.
- Partnered with 4 local schools to deliver services.
- 85% of participating students improved their reading skills.
- Employment placement rates increased by 15%.

## 3. Standalone Completeness Requirement

A candidate may contain a real number and still fail as an output metric if it does not stand on its own. The extractor should either rewrite it into a complete statement using nearby context or reject it if the needed context is unavailable.

| Candidate | Problem | Better output |
|---|---|---|
| 6 counties | Bare number and noun; no action. | Served communities across 6 counties. |
| 85% improved | Does not say who improved or what improved. | 85% of participating students improved reading skills. |
| 9 vans | Capacity item without action. | Operated 9 mobile service vans. |
| 4 partner organizations | Could be a list fragment unless the relationship is stated. | Collaborated with 4 partner organizations to deliver services. |
| 3 new programs | Incomplete unless action and program context are present. | Launched 3 new community programs. |

## 4. Metric Tier Taxonomy

The system should not force all numeric claims into a binary metric/non-metric answer. It should classify metric candidates into tiers so the product can decide what to display, prioritize, or review.

| Tier | Class | Description | Examples | Recommended handling |
|---|---|---|---|---|
| 1 | Outcome / Impact | Measures change, benefit, or result for people, communities, systems, or conditions. | 85% improved reading skills; recidivism fell 12%; earnings rose $4,200 | Highest value. Prioritize. |
| 2 | Reach / Output | Measures scale of service delivery or direct output. | Served 6,000 families; distributed 12,500 meals; provided 800 sessions | Valid metric. Display prominently. |
| 3 | Capacity / Input / Infrastructure | Measures resources, infrastructure, staffing, budget, partners, locations, or operating capacity. | 9 vans; 16 centers; 42 staff; $2.5M budget; 4 partners | Valid lower-tier metric. Label appropriately. |
| 4 | Activity Count | Measures activities completed or launched, but does not directly measure reach or outcome. | Launched 3 programs; hosted 5 events; opened 2 sites | Borderline/useful. Keep separate or lower priority. |
| 0 | Rejected Non-Metric | Numeric expression that does not measure nonprofit output, reach, capacity, activity, outcome, or impact. | #1 ranking; one of seven honorees; founded in 2021; 9-month program | Reject or store outside metrics. |

## 5. Rejection Rules

The following should not be treated as metrics unless the product has an explicit special field for that type of claim.

| Reject type | Why it fails | Examples |
|---|---|---|
| Event occurrence encoded as 1 | The number means the event happened once, not that a meaningful quantity was measured. | Expanded Ready For Work initiative → value 1 |
| Ranking or ordinal position | A position is not the organization's output, reach, or outcome. | #1 city; 3rd largest provider |
| Awards and honors | Recognition is a badge, not an impact or output metric. | One of seven honorees; won 2 awards |
| Dates and years | Calendar references are context, not performance metrics. | Founded in 2021; since 1998 |
| Durations | A time span usually describes program structure, not accomplishment. | 9-month program; 12-week curriculum |
| Labels, ratings, or categories | Often decorative or classificatory unless ratings are the intended measured domain. | 4-star rating; Level 3 certification |
| Unsupported multipliers | Direction without baseline or final quantity is incomplete. | Doubled participation; tripled reach |
| Forecasts and projections | Future estimates are not achieved impact for the reporting year. | Will rise 14% by 2045 |

## 6. Recommended Extraction and Validation Workflow

1. Extract candidate numeric claims from the report, including surrounding context.
2. For each candidate, identify the quantity, unit, measured subject, action/relationship, and reporting-period relevance.
3. Rewrite the candidate into a complete metric statement when the missing context is available nearby.
4. Classify the metric tier: outcome/impact, reach/output, capacity/input, activity count, financial, or non-metric.
5. Classify standalone quality: complete, needs rewrite, insufficient context, or reject.
6. Reject decorative numbers and event occurrences encoded as counts.
7. Return confidence and a short reason for every accepted, borderline, or rejected candidate.
8. Prefer fewer, higher-quality metric statements over a fixed quota that forces weak or misleading metrics.

## 7. Recommended Data Schema

The schema should make the model's judgment auditable. The system should store both the original text and the normalized metric statement.

```json
{
  "original_text": "85% improved",
  "metric_statement": "85% of participating students improved their reading skills.",
  "value": 85,
  "unit": "%",
  "measured_subject": "participating students",
  "action_or_relationship": "improved their reading skills",
  "metric_tier": "outcome_impact",
  "standalone_quality": "needs_rewrite",
  "validity": "valid_metric",
  "reporting_period_relevance": "current_year",
  "confidence": 0.86,
  "reason": "The statement measures an outcome for participants, but the original fragment needed context to stand alone."
}
```

| Field | Purpose | Allowed / recommended values |
|---|---|---|
| metric_statement | The final standalone metric claim displayed to the user. | Complete sentence or sentence fragment with clear action. |
| validity | Whether the item is a metric. | valid_metric, borderline_metric, non_metric |
| metric_tier | The evidence class. | outcome_impact, reach_output, capacity_input, activity_count, financial, non_metric |
| standalone_quality | Whether the final statement can stand on its own. | complete, needs_rewrite, insufficient_context, reject |
| measured_subject | The concrete thing measured. | participants, families, counties, meals, staff, partners, dollars |
| reason | Short explanation for audit and debugging. | Plain English rationale. |
| confidence | Model/system confidence. | 0.0 to 1.0 |

## 8. Classification Examples

| Original claim | Recommended output | Tier | Decision |
|---|---|---|---|
| Fair Chance expanded its Ready For Work initiative. | None | Rejected non-metric | Reject. Event occurrence should not become value = 1. |
| Served 6 counties. | Served communities across 6 counties. | Reach / Output | Accept. Concrete geographic reach. |
| 16 centers. | Operated 16 service centers. | Capacity / Input | Accept as lower-tier capacity metric if context supports it. |
| 9 vans. | Operated 9 mobile service vans. | Capacity / Input | Accept as lower-tier capacity metric if mission-relevant. |
| Launched 3 new programs. | Launched 3 new community programs. | Activity Count | Borderline/accept as activity metric, not outcome. |
| 4 partner organizations. | Collaborated with 4 partner organizations to deliver services. | Capacity / Input | Accept if relationship is clear. |
| One of seven honorees. | None | Rejected non-metric | Reject as recognition/award. |
| Ranked #1 in the city. | None | Rejected non-metric | Reject as ranking unless stored in recognition field. |
| Will rise 14% by 2045. | None | Rejected non-metric | Reject as projection unless collecting forecasts. |
| Participation doubled. | None or flag for review. | Insufficient context | Reject or review unless baseline/final value can be extracted. |

## 9. Development Acceptance Criteria

The implementation should be considered successful when it meets these criteria:

- The extractor produces complete metric statements, not bare values or labels.
- Each candidate includes a metric tier, validity status, standalone quality status, confidence score, and explanation.
- The system rejects dates, rankings, awards, decorative labels, durations, forecasts, and event occurrences encoded as counts.
- The system does not force weak metrics to satisfy a fixed quota. It should return fewer metrics when the report does not support more.
- Gray-zone items are classified as lower-tier or borderline rather than silently mixed with impact metrics.
- A reviewer can audit why a candidate was accepted, rewritten, downgraded, or rejected.
- The product can filter output by tier depending on audience needs.

## 10. Prompt / Model Guidance

Recommended instruction for the metric extraction model:

> Extract only complete numeric metric statements. A metric must include a quantity, a concrete measured subject, and an action or relationship. It must be understandable to a reader who knows the nonprofit's mission and report year but does not have the full report. Classify each candidate by tier and standalone quality. Reject dates, rankings, awards, labels, durations, forecasts, and simple event occurrences encoded as a count of 1. Prefer fewer high-quality metrics over filling a quota with weak claims.

## 11. Product Recommendation

**Recommended product stance:** Use a broad enough definition to preserve useful operational evidence, but classify it transparently. Outcome and reach metrics should be prioritized. Capacity and activity metrics should be labeled as lower-tier supporting evidence. Non-metrics should be rejected or stored in separate fields such as awards, rankings, program descriptions, or historical context.

**Bottom line.** This is not technically impossible to classify. The important development move is to stop asking a binary question and instead classify each numeric claim by type, evidence tier, and standalone completeness. Once this taxonomy is implemented, the product can measure non-metric rates against a clear definition and tune extraction quality intentionally.

## Appendix A: Reviewer Checklist

- Does the statement include a numeric value?
- Does it say what is being measured?
- Does it describe an action, relationship, output, capacity, outcome, or impact?
- Can it stand alone with only mission and year as assumed context?
- Is it free of decorative numbers such as dates, rankings, awards, labels, durations, and projections?
- Is the tier appropriate and not overstated?
- Would a knowledgeable consultant accept this as a credible metric statement?

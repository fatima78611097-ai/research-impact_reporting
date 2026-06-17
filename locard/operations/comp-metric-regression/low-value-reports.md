# Low-value report instances (corpus-inclusion reject candidates)

Running list of **low-value reports** found by eye during the metric review — documents
whose metrics extract fine but that probably shouldn't be **in the corpus** (curation, not
an extraction defect). Distinct from the already-live filters: financial/compilation
(`material_type IN financial_report,not_relevant`) and garble ([[enhancements]] ENH-004).

**Gathering instances before building a detector** (per operator: collect data, find the
real distinguishing signal, *then* decide if it's gateable). "Low-value report" is turning
out to be a **broader bucket than "plain-text report"** — at least two flavors so far, and
the second is harder to detect (real metrics + some design, narrow *scope*/report-type).

How to add: point at a doc you judge low-value; it gets characterized against the source
and appended here with its flavor.

---

## Flavors seen
- **A — plain-text report:** typed, undesigned, no imagery/layout (the original "plain-text report" term).
- **B — single-program / administrative report:** a narrow grant/compliance "final report" for one program — has real metrics and some design, but it's not the broad organizational impact/annual report the corpus is built on.
- **C — narrative / event report:** counts EVENTS and existence claims, not quantities — legislative/advocacy reports (a value of 1 per bill passed), church-network reports (1 per revitalization), "first/only" claims. Yields almost no measurable metrics. **Cheaply detected:** the measurable-value check (ENH-006 plug-in #2) flags docs where a high fraction (>~80%) of "metrics" have no measurable value.

## Instances

### LVR-001 — plain-text report (flavor A)
- **Doc:** `4afba9fd` — Emergency Assistance Center Inc ("Annual Report 2022")
- **What it is:** 2 pages, bulleted plain text, **no design/imagery/tables** — reads like a typed memo.
- **Metrics are fine:** 131,520 meals, 6,264 clients, 2,546 households (real, correctly extracted).
- **Why low-value:** undesigned plain-text; not the produced material the product is built on.
- **Caught by a filter?** No — labeled `annual_report`; the plain-text detector was named but never built.

### LVR-002 — single-program / administrative report (flavor B)
- **Doc:** `ee33d0e3` — Community Action Marin ("FINAL REPORT — Emergency Rental Assistance Program (ERAP)")
- **What it is:** 5 pages, a single grant program's compliance final report — bullet stats + a couple of donut charts.
- **Metrics are fine:** 885 households, $6,242,591 disbursed, 2,149 individuals (real, correctly extracted).
- **Why low-value:** narrow single-program/administrative scope — not a broad organizational impact/annual report.
- **Caught by a filter?** No — labeled `impact_report`; and it isn't plain-text (it has charts), so even the (unbuilt) plain-text detector wouldn't catch it. **No cheap signal yet** — it's about report *type/scope*, not label or imagery.

---

### LVR-003 — narrative / event report (flavor C)
- **Doc:** `12a4557a` — Texas Network of Youth Services (legislative/advocacy report)
- **What it is:** a policy-agenda report — each "metric" is a passed bill or legislative win stamped with value=1. **12 of 13 metrics have no measurable value** (measurable-value check).
- **Why low-value:** it reports legislative *events*, not quantified impact. No counts, no outcomes — just "this happened."

### LVR-004 — narrative / event report (flavor C)
- **Doc:** `30259c4d` — GBCanada (church-planting network report)
- **What it is:** value=1 per church revitalization / pastoral installation. **5 of 6 metrics have no measurable value.**
- **Why low-value:** counts events (churches revitalized) as 1s, not quantities.

**Open question for when we have more:** is there a detectable signal for flavor B (title patterns like "Final Report"/"Program", single-program scope, low page count, funder-reporting language), or does it need an LLM/classifier judgment? Decide after more instances. Pairs with [[project_corpus_inclusion_criteria]].

### LVR-002 — plain-text report (flavor A), operator-flagged 2026-06-12
- **Doc:** `dca5a824` — Grapevine Family & Community Resource Center (Annual Report FY2014-15)
- **Signature:** figures=0, tables=1, 2,595 chars/page over 7 pages — typed Word-style document.
- **Metrics extract "fine"** (14/14 published) but skew to program micro-counts (multiple
  measurable-value flags came from this doc); not the produced material the product is built on.
- **Operator ruling:** should not have passed the plain-text gate → gate must be BUILT (REQ-PROD-018).

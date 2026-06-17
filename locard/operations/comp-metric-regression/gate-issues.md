# Gate Issues Log

Operator-flagged problems with the comp-metric grounding gate. **The operator flags from
the review pages by eye; each is then investigated against the source document and recorded
here.** No automated classification — human judgment decides what's a problem. This list is
the evidence we collect *before* deciding any gate change; we look for patterns once enough
are logged, then decide. (Per operator: "collect a bunch and look for patterns" first.)

How to flag: point at a metric (doc sha8 + the statement, or a review-page id). I verify it
against the full source document and append it below with the finding.

Columns per issue: source doc · the metric · gate verdict · operator's call · investigation
(verified against source) · candidate pattern · status.

---

## Open issues

### ISSUE-001 — quarantined a correct metric (derived/aggregate value)
- **Doc:** `56688514` — Council on Aging of Southwestern Ohio (batch1-clean)
- **Metric:** "Council on Aging participated in 64 meetings with elected officials, legislative aides and candidates, and coordinated 50 visits by elected officials and candidates in the homes of COA clients." — value=**114**, subject="Advocacy meetings and home visits"
- **Gate verdict:** QUARANTINE · `value_not_at_marker`
- **Operator's call:** model was correct
- **Investigation (verified against source):** the document says verbatim *"In FY 2013, we participated in 64 meetings with elected officials… and coordinated 50 visits by elected officials and candidates in the homes of COA clients."* So 64 and 50 are real. value **114 = 64 + 50** is a model-computed aggregate that appears **nowhere** in the document. The grounding step also cited a *neighboring* advocacy sentence (marker t99, "Most of the state legislators…"), not the 64/50 sentence.
- **Candidate pattern:** (a) model synthesizes an aggregate value not present on the page; (b) grounding cites the wrong marker.
- **Status:** logged

### ISSUE-002 — quarantined a correct metric (value real, grounding cited wrong marker)
- **Doc:** `db75f054` — River Valley Initiative Foundation (batch1-clean)
- **Metric:** "A $2.3-million fiber enhancement project by CenturyLink was completed, providing 68 miles of new fiber to protect telecommunications." — value=**2300000**, subject="Telecom infrastructure investment"
- **Gate verdict:** QUARANTINE · `value_not_at_marker`
- **Operator's call:** model was correct
- **Investigation (verified against source):** "$2.3-million fiber enhancement project … 68 miles of new fiber" is real in the document. The grounding step cited a *different* sentence ("Our team went on-site to monitor repairs to the damaged fiber…"), so the gate checked the wrong marker. Value real, mis-cited.
- **Candidate pattern:** grounding cites the wrong marker (value genuinely present elsewhere).
- **Status:** logged

### ISSUE-003 — quarantined a correct metric (right passage, WRONG SLOT)
- **Doc:** `81265722` — Family League of Baltimore City Inc (batch1-clean) · review id `81265722:0` (Q1)
- **Metric:** "The Thriving Youth Strategy served more than 1,300 youth across 79 schools in Baltimore." — value=**1300**, subject="Youth served"
- **Gate verdict:** QUARANTINE · `value_not_at_marker`
- **Operator's call:** model was correct
- **Investigation (verified against source):** the value is real and verbatim on **page 5** (marker `t20`): *"More than 1,300 youth served… across 79 schools."* But the model **cited the right passage in the wrong slot** — it pointed `subject_ref` at that page-5 sentence, and pointed `value_ref` at a *look-alike* sentence on **page 12** (`t110`): *"…served 1,324 participants… across 79 schools."* The gate checked the value against `value_ref` (page 12, which has **1,324**, not 1,300) → quarantine. So the value is genuinely present, at a marker the model itself cited — just assigned to subject instead of value.
- **Candidate pattern:** **"right passage, wrong slot"** — value/subject marker mis-assignment (distinct from ISSUE-002's plain wrong-marker, and ISSUE-001's derived value). Look-alike near-duplicate numbers (1,300 vs 1,324) make it easy for the grounding step to swap them.
- **Status:** logged

---

## Resolved / decided

(none yet — no gate changes until the pattern is clear)

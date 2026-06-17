# Impact Story — Definition (v3 — LOCKED 2026-06-11)

*v1 2026-06-11 from the metric-definition playbook + 30-story recon (`recon-30.json`).
v2 same day: folded in the Codex + Gemini reviews (convergent on the major calls).
v3 same day: external review folded in (7 refinements adopted) + prompt-architecture section. Rulings below are
marked **[RECOMMENDED]** (consult-convergent, pending operator confirm) or **[CONFIRMED]** (operator).*

## 1. Definition

An **impact story** is a narrated account, presented by the report as a true past/current event, of a
**concrete change** experienced by an **identifiable beneficiary**, **attributable to the
organization's intervention**.

**Required elements (all three; no minimum length — "Thanks to the legal clinic, John avoided
eviction" is one sentence and complete):**

| element | meaning | notes |
|---|---|---|
| Beneficiary | **identifiable in context, not necessarily named**: a person, family, group, community, or place the report presents as a specific beneficiary rather than a generic population ("a rural village in northern Kenya", "one family in our housing program") | **[CONFIRMED — operator + both consults]** place/community is IN when it is the clear beneficiary of a concrete change ("the watershed was cleared of toxins"); excluding non-human beneficiaries would erase environmental/conservation/infrastructure orgs |
| Intervention | what the org did for them | attribution may be explicit or clearly implied by the report |
| Change | an **observable state change, avoided harm, or sustained improvement** — operational test: **the span must answer "What is different now?"** | "felt empowered" alone fails; "learned to budget and avoided eviction" passes. Immediate relief counts ONLY when tied to a specific beneficiary/episode ("Maria received a hot meal after three days without food"), never routine delivery ("we served hot meals to 300 people") |

**Standalone is NOT an extraction requirement.** Capture the verbatim span as printed, pronouns and
all ("She first came to us in 2021…"); coreference/context completion lives in the **derived summary
field**, labeled as derived. (Both consults: requiring standalone of the raw span over-rejects good
prose.)

**The published unit is the VERBATIM SPAN(S)** with location provenance + structured fields (title,
beneficiary type, program, themes). Model-written summary = derived, labeled, never the story of
record.

**Multi-beneficiary stories (schema rule, not prompt prose):** `primary_beneficiary` = the most
specific beneficiary experiencing the central change; secondary beneficiaries recorded separately.

**Span-boundary rules (Codex: the main adjudication-drift risk at scale):** one canonical span per
story; include the quote and its attribution line; include a heading only if it carries story content;
EXCLUDE trailing moral/appeal sentences ("you can help more people like Vanesa…") and photo credits.

## 2. Rejection rules (not an impact story)

| reject type | rule | example |
|---|---|---|
| Program description | service described, no specific beneficiary change | "Ripple Support Group provides a safe space…" |
| Org-capability narrative | org is the protagonist (fund launched, building opened) | "Crisis Fund established during pandemic" |
| Mission / appeal language | aspirational, no account of change | "Together we can…" |
| Event recap | attendance/activities, no beneficiary arc | gala recaps |
| **Aggregate statistics** *(narrowed per both consults)* | reject only population-level reporting ("served 3,000 people"); **numbers attached to a specific beneficiary STAY** ("Vanesa saved $500, credit +40 points" is an elite story) | |
| **Historical / origin lore** *(refined)* | reject UNLESS the report states a current/report-year-relevant outcome, follow-up, or sustained change (longitudinal impact stays; founder mythology goes) | "back in 1995, our founder…" |
| Composite / hypothetical | **REJECT outright** **[CONFIRMED]** — LLMs cannot reliably catch the "names changed/composite" footnote; flagged-inclusion risks publishing fiction as fact | "imagine a mother who…" |
| Future-tense / forward-looking | not yet true ("will help families like…") — evidentiary requirement: presented as having happened | |

**Routed classes (captured, NOT impact stories — don't trash good data):**
- `testimonial` — pure-sentiment quotes with no change ("we're so grateful"). A quote **with** change
  + intervention context IS an impact story ("I got housing and can see my kids again") **[CONFIRMED]**
- `donor_story` — giver/volunteer as subject (Tricia Hall's drive); separate product value
- `service_episode` *(new, Codex)* — group-level delivery with weak narrative ("participants left
  with new skills") — the middle class between program description and story; prevents oscillation

**Quote-only rule:** a quote alone is an impact story only if the quote itself or its immediate
surrounding context supplies beneficiary + intervention + change (pull-quotes disconnected from body
text fail this).

## 2a. Prompt architecture (complexity/drift control — the metric pipeline's lesson)

- **Stage 1 (extraction prompt, ONE PAGE, sha-locked once validated):** find candidate narrative
  spans; classify `impact_story | testimonial | donor_story | service_episode | reject`; the
  three-element test + "what is different now?". Nothing else.
- **Stage 2 (cheap qualifier on candidates only):** the finer rules — immediate-relief guard,
  historical-lore exception, quote-only adjudication. Layered simple checks instead of one complex
  prompt (ENH-006 pattern, proven on metrics).
- **Output fields:** `classification`, `confidence` (high|medium|low; low routes to review),
  `borderline_reason` (optional) — audit instrumentation from day one.
- **Drift monitor:** per-class rate tracking per run; distribution shifts with no prompt change =
  drift alarm before pollution.
- Prompt changes ONLY via re-validation against the story fixture (the b51dc96a discipline).

## 3. Operator decision points — status after consults

1. Bare gratitude/testimonial → **[CONFIRMED]** change-bearing quote = story; pure sentiment = `testimonial`
2. Donor/volunteer → **[CONFIRMED]** separate `donor_story` class
3. Minimum substance → **[CONFIRMED]** 3 elements, no length rule
4. Composite/illustrative → **[CONFIRMED]** reject outright
5. Community/place beneficiary → **[CONFIRMED IN]** (operator's own #1 comment + both consults)

## 4. PII / consent (hardens into REQ-PROD on lock) — expanded per consults

- Flags captured at extraction, ALL stories kept verbatim internally; transformation happens at the
  publication boundary (pseudonymize, don't drop):
  - `named_person` (first-name-only vs full name)
  - `sensitive_context` (health/HIV, domestic violence, addiction, immigration, criminal justice,
    foster care) → **auto-pseudonymize at publication regardless of source** (Gemini [Critical]:
    consent to one org's PDF ≠ consent to a searchable national aggregation)
  - `minor` — explicit OR **context-implied** (school, foster, youth programs) per Codex
  - `contact_info` / `precise_location` — redact at publication
  - `reidentification_risk` — small community + role + program combination
- **Visible redaction (operator ruling 2026-06-11): substitutions are NEVER silent.** The internal
  record stays true-verbatim; the published rendering marks every replacement in square brackets
  ("amazing experience for **[their son]**") plus a per-story disclosure line ("name replaced for
  privacy; original on file"). A silently edited quote presented as verbatim would break the
  pipeline's core integrity claim. Machine fields: `pii_redacted`, `redaction_type`.
- **Precedence rule:** name granularity sets the floor, sensitive context sets the ceiling — the
  stricter wins (first name + disability/recovery/DV context still pseudonymizes).
- Public-launch legal/ethics review required before any named-person publication.

## 5. Held (collected, not gating — the tier pattern)

- Emotional-resonance / quality scoring — log, never gate, until calibrated.
- Theme taxonomy — freeform now, lock vocabulary from data later.

## 6. Validation plan (after lock) — updated for consult findings

New prompt → dev-corpus run (165 docs) → vision-audit sample of the NEW output → story fixture +
scorecard + suite checks. Two engineering requirements from Gemini [Critical] carried into the build:
- **Span anchoring uses NORMALIZED/fuzzy matching** (hyphenation, line breaks, pull-quote
  interruptions) — never raw exact-match (the metric pipeline's normalize() lesson applies directly).
- **Story-aware chunking**: stories cross page breaks (often interrupted by a full-page photo);
  extraction context must not sever them or the model sees half a story and rejects it.

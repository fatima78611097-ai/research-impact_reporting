# Lavandula Corpus & Strategic Communications Platform — Project Seed

> **Purpose of this document.** This is the seed reference for every Claude Code session that touches this project. Read it first to ground yourself in strategic context, architecture, and conventions. Do not implement from this document directly. Use it to draft per-phase specifications, plans, and red-team reviews via Ron's standard workflow: spec → 3-way model consult → plan → 3-way consult → build → 3-way consult → red-team. Each phase produces its own artifacts; this seed is the shared context that grounds them all.

---

## 1. Strategic Context

### What is being built

A nonprofit reporting intelligence infrastructure that produces multiple revenue-bearing product surfaces:

- **Production deliverables** — annual reports, impact reports, event collateral, donor stewardship pieces (Lavandula Design's existing revenue engine, augmented)
- **Pre-call briefings** — prospect-specific intelligence reports for sales conversations
- **AI Interviewer** — archetype-aware content gathering tool that runs discovery sessions for impact reports
- **Domain-specific lexicon** — published, citable, navigable wiki of how each nonprofit archetype communicates
- **Reporting index / report card** — evaluative artifact comparing an organization's reporting against archetype norms
- **Annual sector publications** — credibility-building thought leadership

All surfaces share a single underlying corpus and pipeline. The corpus is infrastructure; the surfaces are products.

### Why this is viable now

The cost structure of running this infrastructure has dropped roughly 25-50x in the last 12-18 months due to:
- Frontier model commoditization (DeepSeek V4 at fractional pricing for bulk inference)
- Local document parsing (Docling, MIT-licensed, GPU-accelerated)
- Pgvector eliminating need for separate vector databases
- Search API competition (Serper at ~10x cost reduction over Brave)
- Agent-leveraged engineering reducing build cost dramatically

Three years ago this was venture-funded scale infrastructure. Today it costs hundreds to low thousands of dollars in API spend plus engineering time leveraged through agents. The window exists because most operators haven't internalized the new economics.

### Strategic positioning

Lavandula is rebranding from "nonprofit design firm" to **"strategic communications services for nonprofits."** The corpus and intelligence layer make this rebrand substantive rather than cosmetic. Production work is the immediate revenue. Sector authority via published intelligence is the long-term moat.

The destination: become the citable measurement authority for nonprofit impact reporting practices (analogous to Morningstar in financial reporting, or Forrester in technology research, but for nonprofit communications). Production engagements remain core revenue. Authority-driven inbound and advisory engagements compound over time.

### What this is NOT

The project must not drift into adjacent businesses that share infrastructure but diverge in customer, value prop, or operating model:

- **Not a measurement platform** (UpMetrics, Sopact, Clear Impact category — different buyer, different operations)
- **Not a methodology consultancy** (Theory of Change practitioners — different sales motion)
- **Not a CRM or donor management system**
- **Not a real-time program data collection tool**
- **Not a data licensing business** as primary revenue (data licensing may emerge as tertiary revenue with strict customer screening)
- **Not a SaaS performance management tool**

When in doubt, apply the four-question filter: same buyer? same value prop? same competitive landscape? same operational model? If any fail, it's a different business and should be declined.

---

## 2. Customer & Market

### Primary customers

- **Nonprofits** ($250K+ revenue outside Houston, $100K+ in Houston) commissioning impact reports, annual reports, and event collateral
- **Foundations** purchasing benchmark studies, sector analyses, and grantee evaluation services
- **Sector media and researchers** as audiences for published thought leadership (low/no direct revenue but high credibility ROI)

### Buyer personas

- Development directors and chief development officers (production engagements)
- Communications directors (production + advisory)
- Executive directors (strategic engagements at sophisticated orgs)
- Foundation program officers (benchmark studies, advisory)

### Market segmentation by archetype

The corpus organizes nonprofits into archetypes based on NTEE codes and operational patterns. Each archetype has distinct vocabulary, reporting conventions, visual patterns, and stakeholder language. Archetype assignment is a first-class metadata field on every organization in the corpus.

Examples (non-exhaustive):
- Food banks
- Regional theatres
- Community foundations
- Hospital foundations
- Human services orgs
- Independent schools
- Jewish federations
- Health system foundations
- Environmental orgs
- Performing arts (non-theatre)
- Higher education advancement

Archetype rubrics — what "good reporting" looks like for each — are derived empirically from the corpus, not asserted from authority.

---

## 3. Architectural Principles

These apply across all phases and should constrain every per-phase specification.

### Storage

- **Postgres + pgvector** as the single primary storage layer. No separate vector database.
- **S3** for raw artifacts (PDFs, page images, original crawl outputs). Lifecycle policies tier old data to Glacier.
- **Git repository** for generated wiki, methodology documentation, and code.
- **Structured data lives in Postgres. Narrative artifacts live in markdown wiki. Files live in S3.** Each storage layer matches what it stores.

### Schema philosophy

- **Organization-first.** Every nonprofit in the corpus has a record regardless of customer relationship status. Customer status is metadata, not the basis of the record.
- **Full citation chain: org → document → section.** The section is the finest-grained citation atom, but a citation must resolve the complete chain — which organization published it, which document contains it, and which section within that document. A section_id is a database key; a citation is a human-verifiable reference (e.g., "Houston Food Bank, *2024 Annual Report*, Executive Letter").
- **Vocabulary observations as first-class records.** Linked to source sections, with provenance preserved.
- **Methodology versioning.** Every extraction tagged with `extractor_version` so re-extraction with improved rubrics is supported without losing prior data.

### Citation discipline

Non-negotiable across all generated outputs:

- Every factual claim cites a source via the full chain: organization → document → section
- Citations resolve to actual records in the database (org_id → document_id → section_id)
- A validator checks every citation before any generated artifact is committed
- Hallucinated citations are rejected, not warned about
- Smart-model confident hallucination is the primary threat to defend against

### Cost & infrastructure

- **CPU instances (t4.small/medium)** for orchestration, API-bound work (resolver, classifier, vocabulary extraction calling DeepSeek)
- **GPU spot (G6.2xlarge)** for Docling parsing in bursts, terminated when batch completes
- **Cluster architecture** for parallel processing of independent documents
- **DeepSeek V4** for bulk LLM work (classification, vocabulary extraction)
- **Claude Sonnet or DeepSeek V4 Pro** for high-stakes synthesis (methodology design, wiki generation, briefing synthesis, interview real-time)
- **BGE-M3** for embeddings (local, free)
- **Serper** for search APIs with abstraction layer for provider swap
- **Free fallbacks first** for URL/phone/metadata resolution (IRS BMF, ProPublica, Wikipedia) before paid APIs

### Anti-fragility

- Every external dependency abstracted behind an interface
- Search providers, LLM providers, and parsing engines are swappable
- Pipeline stages are independently retryable
- Schema changes are migrations, not rewrites
- Methodology versioning enables re-extraction without data loss

---

## 4. Domain Concepts

Vocabulary used consistently across all phase specifications.

### Documents and parsing

- **Document** — a single source artifact (PDF) ingested into the corpus. Has a document_type (annual_report, impact_report, newsletter, magazine, appeal_letter, gala_program, etc.) and is tied to one organization.
- **Section** — a heading-anchored chunk of a document. Has section_type (executive_letter, program_highlight, story, donor_list, financials, board_roster, etc.) and is the citation atom.
- **Citation slug** — human-readable identifier for sections (e.g., `exec-letter`, `pavilion-section`) used in citation formatting.
- **Page image** — rendered image of each PDF page, stored in S3 for visual analysis.

### Vocabulary and lexicon

The domain-specific vocabulary corpus is the project's core intellectual property and the foundation for every downstream product surface. It is not a byproduct of parsing — it is the *reason* the corpus exists. LLMs contain domain vocabulary diffusely in their weights, but they cannot produce a complete, trustworthy, citable model of an archetype's language. They answer pointed questions confidently but cannot inventory what they don't know, measure prevalence, track drift, or provide provenance. Smarter models just hallucinate more convincingly. The corpus provides what models cannot: measured ground truth with citation chains.

The vocabulary corpus enables two immediate capabilities:
1. **Human fluency** — team members internalize archetype-specific language before sales calls and during production engagements, signaling domain expertise from first contact.
2. **Programmatic fluency** — the AI Interviewer and formatting engine use the same vocabulary data to speak each archetype's language, ask the right questions, and recognize meaningful signals vs. generic responses.

The target data structure is the **concept record** — a canonical concept with its full observed surface area and provenance:

```
Canonical concept: Mobile pantry
Domain: Food bank / hunger relief
Parent concept: Food distribution

Observed phrases:
  mobile pantry, mobile market, mobile food pantry,
  pop-up pantry, mobile grocery distribution

Common metrics:
  households served, pounds distributed,
  distribution sites, visits, meals equivalent

Common document types:
  annual reports, impact reports, program pages, newsletters

Evidence:
  source_org_id, document_id, source_url,
  page/section, evidence_span, publication_year

Notes:
  Often associated with rural access, senior services,
  school partnerships, and transportation barriers.
```

Every concept record is empirically derived — observed in documents, linked to sources, measurable in frequency and distribution. The completeness of coverage is itself measurable: gaps are visible, confidence is quantifiable, and the map has known boundaries rather than unknown unknowns.

Key vocabulary domain concepts:

- **Concept record** — the canonical unit of the lexicon. A domain concept with its observed phrases, associated metrics, evidence chain, and archetype context.
- **Vocabulary term** — a canonical word, phrase, or concept used in nonprofit reporting (e.g., "neighbors", "food insecurity", "stewardship cultivation").
- **Vocabulary observation** — a single occurrence of a term in a specific section of a specific document, with full context preserved.
- **Register** — the communication context of a section: `formal_report`, `operational`, or `donor_facing`. Same org speaks differently across registers.
- **Archetype** — the categorical grouping of nonprofits sharing communication patterns (food bank, regional theatre, etc.).
- **Rubric** — the per-archetype criteria for what constitutes complete/strong/weak reporting. Derived empirically from corpus, not asserted.

### Outputs and artifacts

- **Reference corpus** — the queryable database used for retrieval. NOT training data; never used to fine-tune models. Used as RAG reference at inference time.
- **Lexicon** — generated wiki of how each archetype communicates. Markdown-based, citation-backed, navigable.
- **Index** — quantitative measurement layer (frequencies, adoption rates, drift over time).
- **Report card** — evaluative artifact comparing one org's reporting against archetype norms. Internal name; client-facing branding TBD ("Reporting Profile", "Reporting Maturity Assessment", or similar).
- **Pre-call briefing** — prospect-specific intelligence document for sales conversations.

### Pipeline stages

- **Seed** — list of orgs to ingest, derived from IRS BMF and other sources
- **Resolve** — find websites and phone numbers for each seed org
- **Crawl** — fetch documents from resolved websites
- **Classify** — broad document_type assignment (annual_report, impact_report, newsletter, not_a_report, etc.). Serves two purposes: (1) validates that the taxonomy-based crawl and fetch is effective and proportionally accurate, and (2) provides a loose categorization starting point that tees up documents for detailed ingest. Classification at this stage is triage, not final — deep parsing may reclassify or disqualify artifacts.
- **Parse** — Docling extraction of structured content (sections, tables, figures)
- **Embed** — generate vector embeddings for sections
- **Extract** — vocabulary observations, methodology tags, structural patterns
- **Aggregate** — produce archetype-level views
- **Generate** — produce wiki pages, briefings, reports

---

## 5. Existing Infrastructure (as of project seed authoring)

The following components exist and are operational. Subsequent phases build on these rather than replacing them.

### Operational pipeline

- **Seeder** — populates seed pool from IRS data. National scope, seeding all 50 states + territories by NTEE code and revenue thresholds.
- **Resolver** — multi-tier URL and phone resolution. DeepSeek V4 primary, with configurable LLM backend per job. Dual search engine support (Brave + Google) with per-job selection.
- **Crawler** — fetches documents from resolved URLs. Async architecture with configurable concurrency. Wayback Machine recovery for sites that block direct crawl. Inline first-page classification via configurable backend (DeepSeek API default).
- **Classifier** — categorizes documents (annual, impact, other, not_a_report, etc.) with high precision (~99.98% classification success on 9,022 documents). Serves as crawl quality gate and loose categorization for downstream ingest.
- **990 ingestion pipeline** — national-scale, ~1.77M filings across 440K nonprofits, refreshed nightly. Extracts Part VII people data (officers, directors, key employees, contractors) with compensation breakdowns.
- **Orchestrator** — job-based pipeline management via dashboard. Multi-host orchestration with job queuing, dependency chaining, heartbeat monitoring, and per-job configuration. Systemd-managed on each host.
- **Dashboard** — Django web UI for pipeline control, job monitoring, and status visibility across hosts.

### Tech stack

- AWS infrastructure (EC2, S3, RDS Postgres)
- Tailscale for network isolation
- Docling for document parsing (GPU acceleration on G6.2xlarge spot — not yet integrated into pipeline)
- pgvector for embeddings (extension installed, not yet populated)
- BGE-M3 for embedding generation (planned)
- DeepSeek V4 for bulk LLM work (resolver, classifier)
- Claude Sonnet for high-stakes synthesis
- Brave + Google for search APIs (configurable per job)

### Conventions established

- CLAUDE.md and CODING_STANDARDS.md exist for the broader Lavandula ERP project
- Frontend coding standards and UI design system in place
- Multi-tenant architecture using django-tenants in adjacent projects
- DRF API patterns established
- Tailscale-mediated authentication

---

## 6. Phased Build Plan

Each phase is its own design loop (spec → consult → plan → consult → build → consult → red-team). The seed names them and their dependencies but does not specify their internals. Per-phase specifications are produced fresh for each phase.

### Phase 1 — Foundation and Schema Discipline

**Goal:** Establish the canonical schema for documents, sections, embeddings, and the citation contract. Audit existing pipeline output for compliance with the schema. Add register tagging and document_type refinement.

**Dependencies:** None (current state). Some refactoring of existing classifier/parser output to fit canonical schema.

**Architectural notes for spec:**
- *Register detection method* — Register tagging (formal_report, operational, donor_facing) requires inference on section content. The Phase 1 spec must decide whether register is assigned by heuristic (section position, document_type, heading patterns) or by LLM inference. The former keeps Phase 1 a pure schema phase; the latter pulls extraction-quality prompting into foundation work.
- *Docling integration* — The existing pipeline crawls and classifies but does not yet parse documents into sections. Phase 1 must define the relationship between Docling-based parsing and the section schema — whether Phase 1 includes building the Docling parsing stage or assumes it as a prerequisite. This is the bridge between the current pipeline (document-level) and the corpus phases (section-level).
- *National crawl concurrency* — The pipeline is actively scaling to national coverage. Phase 1 schema work must coexist with ongoing crawl ingestion. The spec should address whether schema migration happens on a snapshot or must be compatible with live crawl writes.

**Success criteria:**
- Schema documented with foreign key relationships
- All existing parsed sections conform to schema
- Citation slugs assigned to every section
- Register field populated on every section
- Validator function exists that confirms a citation resolves to a real section
- Document_type taxonomy refined to distinguish annual_report, impact_report, newsletter, magazine, appeal_letter, gala_program, etc.

**Out of scope:** Anything that depends on extracted vocabulary or generated artifacts.

### Phase 2 — Vocabulary Extraction Layer

**Goal:** Build the extraction pipeline that produces vocabulary_observations linked to source sections. Includes prompt design, eval set construction, methodology versioning, and confidence calibration.

**Dependencies:** Phase 1 schema stable.

**Architectural notes for spec:**
- *Archetype assignment* — Vocabulary extraction and aggregation are organized by archetype, but no prior phase assigns archetype to organizations. Archetype is an org-level field derived from NTEE codes and operational patterns. Phase 2 spec must define the archetype assignment method (rule-based from NTEE, LLM-inferred, or hybrid) and establish the archetype as first-class metadata before extraction begins.

**Success criteria:**
- vocabulary_terms and vocabulary_observations tables populated
- Extraction prompt validated against hand-labeled eval set (~50-100 sections)
- Confidence scoring calibrated and consistent
- extractor_version tracked on every observation
- Re-runnable: full corpus re-extraction is a one-command operation
- Coverage across all five vocabulary categories (stakeholder, metric, outcome, methodology, temporal)

**Out of scope:** Wiki generation. Aggregation views beyond basic frequency counts.

### Phase 3 — Aggregation and Pattern Detection

**Goal:** Build the analytical layer that turns raw observations into archetype-level patterns. Frequency by archetype/year, adoption curves, drift analysis, register-comparison views.

**Dependencies:** Phase 2 producing observations at corpus scale.

**Success criteria:**
- Materialized views or query functions for common aggregations
- Drift analysis identifies emerging vs fading terms with statistical confidence
- Cross-archetype queries supported
- Register-comparison views work (formal_report vs operational vs donor_facing)
- Performance acceptable at full national corpus scale

**Out of scope:** Generation of human-readable artifacts. Pre-call briefings.

### Phase 4 — Wiki Generation Layer

**Goal:** Generate the human-readable lexicon wiki from corpus aggregations. Markdown files, citation-backed, navigable, archetype-organized. Includes citation validator and regeneration tooling.

**Dependencies:** Phase 3 aggregations stable.

**Success criteria:**
- Wiki structure established (per-archetype directories, page templates)
- Generation pipeline produces valid markdown with resolved citations
- Citation validator rejects pages with broken citations
- Cross-archetype pages supported
- Regeneration is a one-command operation
- Wiki readable in standard markdown viewers (Obsidian, GitHub, qmd)
- Methodology document version-controlled alongside wiki

**Out of scope:** Public-facing publication infrastructure. Custom rendering UIs.

### Phase 5 — Pre-Call Briefing Generator

**Goal:** Produce prospect-specific briefings combining corpus data, 990 pipeline data, archetype rubrics, and prospect-published reports. Used by Lora and sales team before discovery calls.

**Dependencies:** Phases 1-4 producing reliable archetype rubrics and lexicon. 990 pipeline integrated with corpus prospect records.

**Architectural notes for spec:**
- *990 integration ownership* — The 990 pipeline and the corpus pipeline currently share an EIN key but no explicit join layer. This phase requires person records (officers, directors, compensation) from the 990 pipeline to appear alongside corpus data in briefings. The spec must define whether 990 integration is a prerequisite delivered before Phase 5 begins (potentially as a standalone bridging spec) or built within Phase 5 itself. The join semantics (EIN matching, name resolution across years, handling orgs with 990 data but no corpus documents and vice versa) need explicit design.

**Success criteria:**
- Given an EIN, produces a 1-2 page briefing in <5 minutes
- Briefing includes: org snapshot, leadership context, reporting history, peer benchmarks, vocabulary observations, vendor signals, conversation hooks
- Every claim cites either corpus or 990 source
- Format adapts to archetype
- Generates as PDF for client-facing distribution

**Out of scope:** Real-time interviewing. Post-call follow-up generation (later phase).

### Phase 6 — AI Interviewer

**Goal:** Archetype-aware content gathering tool for impact report engagements. Uses corpus rubrics to guide questions, captures stories with permission, surfaces gaps relative to peer norms.

**Dependencies:** Phases 1-4 producing archetype rubrics and exemplar pools. Story collector data model designed.

**Success criteria:**
- Conducts a discovery interview against archetype-specific question bank
- Surfaces follow-up questions based on incompleteness relative to rubric
- Captures responses in structured form (story, metric, program update, vocabulary signal)
- References peer exemplars when useful
- Output feeds the formatting engine (Phase 7)

**Out of scope:** Voice/audio. Real-time multi-modal capture (story collector handles that separately).

### Phase 7 — Formatting Engine

**Goal:** Production-side output generation. Takes Interviewer output + corpus context + design templates and produces report drafts. Domain-specific styling and structure per archetype.

**Dependencies:** Phase 6 producing structured interview output. Design templates per archetype.

**Success criteria:**
- Generates draft impact report from Interviewer output
- Applies archetype-appropriate structure, vocabulary register, and visual conventions
- Output is editable starting point, not finished product
- Reduces production time materially compared to from-scratch design

**Out of scope:** Final design polish (human craft). Print production.

### Phase 8 — Report Card / Reporting Index

**Goal:** Evaluative artifact comparing an organization's reporting against archetype norms. Internal sales tool, eventually client-facing deliverable.

**Dependencies:** Phases 1-4 producing reliable archetype rubrics. Phase 5 briefing infrastructure.

**Success criteria:**
- Generates structured assessment per org
- Quantifies completeness against archetype rubric
- Identifies specific gaps with peer-comparison framing
- Branded as a citable artifact ("Reporting Profile" or similar)
- Format adapts to internal vs client-facing use

**Out of scope:** Public-facing index publication.

### Phase 9 — Story Collector & Client Portal

**Goal:** Web-first responsive PWA for ongoing client relationships. Story capture with consent management, asset library, project deliverables, eventually mobile-optimized capture.

**Dependencies:** Foundation phases stable. Tenant model designed. Production engagements running successfully with manual content gathering.

**Success criteria:**
- Authenticated portal per client
- Story capture flow (text, voice, photo) with consent management
- Asset library tied to client and projects
- Year-over-year content accumulation per org
- Mobile-friendly capture flows (PWA, no native app initially)

**Out of scope:** Native iOS or Android apps. Real-time collaboration features.

### Phase 10 — Publication Pipeline

**Goal:** Public-facing publications that establish sector authority. Annual lexicon edition, sector reports, methodology documentation, citation infrastructure.

**Dependencies:** Phases 1-4 producing publication-quality data. Naming and brand decisions complete.

**Success criteria:**
- Annual flagship publication generated from corpus on schedule
- Sector-specific reports producible on demand
- Methodology document published and citable
- Public website or distribution mechanism operational
- Citation tracking infrastructure (so external citations of our work are detectable)

**Out of scope:** Paid subscriptions, gated content, e-commerce.

---

## 7. Workflow Conventions

### Per-phase loop

Each phase follows Ron's standard workflow:

1. **Spec** — initial brief authored against this seed and the prior phase's outputs. Specifies what success looks like, what the architecture is, what's in and out of scope.
2. **3-way model consult on spec** — Claude Opus, GPT-5, Gemini Pro independently critique the spec and propose revisions. Disagreements are surfaced, not averaged.
3. **Synthesis spec** — Ron adjudicates disagreements. Final spec produced.
4. **Plan** — implementation plan against the spec. Files to create, schemas to migrate, prompts to engineer, evaluation criteria.
5. **3-way model consult on plan vs spec** — verify plan implements spec correctly. Catch missed requirements and over-engineering.
6. **Build** — Claude Code implements the plan. Ron directs and judges. Agents do detail work.
7. **3-way model consult on build vs plan** — verify build matches plan. Catch shortcuts and drift.
8. **Red-team consult** — adversarial review for failure modes, security issues, hallucination risk, edge cases, anti-pattern drift.
9. **Promotion** — phase is marked complete and becomes dependency for downstream phases.

### Cross-phase principles

- **No phase begins until prior phase is promoted.** Resist temptation to build downstream before foundation is validated.
- **Each spec session begins by reading this seed.** Ground in shared context before narrowing.
- **Each phase produces its own spec, plan, build artifacts, and red-team report.** All version-controlled.
- **Drift is the primary risk.** Adjacent expansions will look natural and should be declined unless they pass the four-question filter (same buyer, same value prop, same competitive landscape, same operational model).
- **Hardness is preferred over ease.** Easy things commoditize; hard things compound. The asset is the integration, and integration is annoying enough to deter casual competitors.

---

## 8. Open Questions

These are not blockers but should be resolved during specific phases.

### Brand and naming
- Final naming for the index ("Reporting Profile" / "Reporting Maturity Assessment" / "Reporting Index")
- Final naming for the lexicon ("Lavandula Lexicon" / "Sector Reporting Atlas" / other)
- Public-facing brand for the strategic communications repositioning
- Tagline that captures evidence-led positioning without overreaching

### Architecture
- When and how Docling parsing integrates into the pipeline (Phase 1 or prerequisite?)
- Register detection method: heuristic vs. LLM inference (determines Phase 1 scope)
- Archetype assignment method and ownership: rule-based from NTEE, LLM-derived, or hybrid (must be resolved before Phase 2)
- 990-to-corpus join layer: standalone bridging spec or built within Phase 5?
- Schema migration strategy while national crawl is actively writing to the database

### Methodology
- Section chunking strategy at the boundary cases (long sections, sections without headings, multi-column layouts)
- Confidence threshold for vocabulary observations entering the canonical record
- Identity resolution for people across years (fuzzy matching for "Tom Carman" vs "Thomas H. Carman")
- Contractor classification taxonomy (design firm, fundraising consultant, accounting, legal, etc.)

### Coverage
- Geographic and sector representativeness validation before scaling national crawl
- Whether to expand archetype coverage opportunistically as new patterns emerge or commit to a fixed initial set
- How much of the "other" classified bucket to investigate before scaling

### Publication strategy
- Cadence of public lexicon publication (annual, semi-annual)
- Distribution mechanism (website, PDF, partner publications, all)
- Whether to seek academic validation of methodology before public release
- Citation infrastructure: how external citations of our work get tracked

### Long-term
- When and how foundation advisory engagements get formalized as a service line
- Whether to build a dedicated public-facing surface or remain corpus-as-infrastructure
- How the rebrand to "strategic communications services" gets sequenced with the publication strategy

---

## 9. Anti-Patterns

These are paths that will be tempting and should be declined unless conditions change materially.

### Scope expansion
- Building a measurement platform (UpMetrics-shaped)
- Building a methodology consultancy (ToC competitor shape)
- Building a CRM or donor management system
- Building real-time program data collection tools
- Building case management infrastructure
- Selling raw data to working consultants (competitors)

### Architecture
- Introducing a separate vector database when pgvector serves
- Building from scratch what Docling provides
- Hand-rolling PDF parsing instead of using proven tools
- Storing structured data in markdown files
- Storing narrative artifacts in databases
- Moving high-volume bulk inference to expensive APIs when DeepSeek serves
- Optimizing for resilience prematurely on cost-trivial dependencies

### Process
- Building before the spec is consulted
- Skipping the red-team review because the build "looks good"
- Letting one model's authority dominate the consult (the disagreements are the value)
- Treating credentialed methodologies as substitutes for empirical measurement
- Publishing the lexicon or index before the methodology is defensible

### Strategic
- Pricing for the buyer who can't pay (subsidizing competition)
- Pursuing data licensing as primary revenue
- Selling intelligence to direct competitors at any price
- Diluting the production business by chasing platform economics
- Letting AI commoditization erode the moat by failing to update the corpus
- Substituting frontier model capability for corpus measurement (the model may know the words; only the corpus knows the field's measured patterns)

---

## 10. Ground Truth

When in doubt, return to these:

- **The corpus is the asset.** Everything else is a product surface or operational support.
- **The vocabulary corpus is the core IP.** The corpus isn't valuable because it contains 50K PDFs — it's valuable because it enables extraction and codification of language patterns those PDFs contain. Without that, it's a document archive.
- **The LLM is the extraction engine, not the knowledge base.** Models contain domain vocabulary diffusely in their weights but cannot produce a complete, trustworthy, citable model of any archetype's language. Smarter models just hallucinate more convincingly. The corpus provides measured ground truth; the model is the tool that helps extract it.
- **Production work is current revenue.** Authority is long-term moat. Both are real; neither substitutes for the other.
- **Hard things compound.** The integration of corpus + 990 + lexicon + briefings + interviewer is the moat. Each component alone is not.
- **Citations are non-negotiable.** Every claim, every output, every artifact. Smart-model confident hallucination is the enemy.
- **Schema discipline now, optionality forever.** Decisions made before code is written constrain decades of subsequent work. Get them right.
- **The destination is published sector authority.** Production work today is the path to it. Adjacent businesses are not.

---

## Document Maintenance

This seed is a living document. Updates happen between phases, not during them. Per-phase artifacts (specs, plans, builds, red-teams) live in their own files and reference this seed by version.

**Version:** 1.0 (initial seed)
**Last updated:** 2026-05-05
**Authored:** From extended strategic conversation between Ron Yates and Claude Sonnet 4.6
**Next review:** Before Phase 1 spec authoring

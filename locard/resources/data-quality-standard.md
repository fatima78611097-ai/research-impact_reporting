# Data-Quality & Attribution-Confidence Standard

**Status: PROVISIONAL** — the acceptance thresholds in §3 are the fail-closed
strawman defaults (Spec 0048 §Open Decision 2). They are binding *as written*
until the sponsor ratifies or overrides them. **While PROVISIONAL, no
production recovery/ingestion *write* may proceed against these thresholds** —
the milestone may close, but the production gate remains until ratification.
Ratification or override does not weaken the gate; it only fixes the number.

**Canonical home**: `locard/resources/data-quality-standard.md` (permanent,
discoverable — not a scratch location).
**Owner**: project owner (sponsor) ratifies thresholds; architect maintains.
**Authority**: Spec 0048 Part 3. Derived from
`locard/operations/macro-plan-corpus-integrity-rag-readiness.md`
(§Workstreams 2 & 3, §Quality Gates).
**Created**: 2026-05-18.

---

## 0. Scope and binding force

This standard is a **contract, not code**. It defines what "trustworthy" means
for the corpus *before* the first production recovery, so a run cannot complete
and only then discover the bar was never specified.

**Binding clause.** Every downstream spec or plan that performs recovery,
reconciliation, recrawl, or bulk ingestion **MUST** link this document and
**MUST**:

1. compute and persist (or compute-at-evaluation, until the attribution schema
   lands) an `attribution_confidence` tier per §1 for every row it writes;
2. apply the sampling design in §2 and meet the acceptance threshold in §3
   per stratum before the data is promoted to production/downstream use;
3. apply stratum-level quarantine (§4) on failure — never all-or-nothing
   promotion, never blanket promotion;
4. exclude `low`-tier and `reject`-tier rows from production/downstream use.

Spec and plan review **MUST** reject any downstream recovery/ingestion spec
that ingests below-threshold data, omits the confidence rubric, or does not
reference this standard. A reviewer citing this clause is sufficient grounds
for `REQUEST_CHANGES`.

---

## 1. Attribution-confidence rubric

`attribution_confidence` is the defensible answer to "what evidence ties this
document to this EIN?" It is a **new derived value whose computation this
standard defines**; its persistence is downstream attribution-schema work
(Spec 0048 non-goal). Until that schema lands, treat it as
**computed-at-evaluation**, not a column to read.

Tiers are assigned by the **strongest** evidence path that holds. If none
holds, the tier is `reject`.

| Tier | Required evidence (ALL listed conditions must hold) | Downstream eligibility |
|------|------------------------------------------------------|------------------------|
| **high** | Document was fetched from a URL whose host is on the org's own resolved eTLD+1 (`seed_etld1` match), AND the EIN was carried along the crawl path (`fetch_log.ein` / crawl-discovery record, not inferred post hoc), AND `content_sha256` is present and the archived object exists, AND no contradicting evidence (see §1a). | Eligible |
| **medium** | EIN is present from S3 object metadata and/or `fetch_log` only (not a same-eTLD+1 crawl-path match), AND a plausible `source_url` is present, AND `content_sha256` + archived object exist, AND no contradicting evidence. | Eligible (subject to §3 threshold) |
| **low** | Attribution rests on a multi-hop or cross-origin redirect chain, OR the EIN was inferred indirectly (e.g. filename/heuristic, not metadata or crawl path), OR `source_url` is absent but an EIN guess exists. | **Excluded from production/downstream use by default** (its presence alone bars production ingestion — not rate-gated) |
| **reject** | No defensible EIN→document evidence path: no `source_url` AND no metadata EIN AND no crawl-path EIN; OR the archived object is missing; OR `content_sha256` cannot be computed/validated. | Excluded; quarantined for investigation |

### 1a. Contradicting evidence (forces downgrade, never upgrade)

A row that would otherwise be `high`/`medium` is **downgraded to `low`** (or
`reject` if the contradiction is dispositive) when any of:

- the resolved host eTLD+1 disagrees with the seed org's resolved eTLD+1 and
  the path was not an explicit same-origin link;
- the document's own content asserts a different organization/EIN than the
  attributed EIN (when content is available at QA time);
- the EIN does not join to `nonprofits_seed`;
- the `source_url` is structurally implausible (non-document host, parking
  page, unrelated aggregator) for the attributed org.

`attribution_confidence` is a **floor-by-weakest-link**: the final tier is the
minimum of the tier implied by the evidence path and any downgrade from §1a.

---

## 2. Sampling design

**Strata.** Sampling is stratified by the Cartesian product:

```
state  ×  NTEE-major  ×  recovery-source
```

- `state` — the seed org's state.
- `NTEE-major` — the major NTEE category derived from `nonprofits_seed`.
- `recovery-source` — the recovery tool/provenance path that wrote the row,
  recorded at recovery time (e.g. `pass1`, `pass2`, `s3-orphan`), surfaced via
  `corpus.discovered_via` / the recovery run's provenance.

A stratum is one `(state, NTEE-major, recovery-source)` cell that contains ≥1
recovered row eligible for production (i.e. `high` or `medium`; `low`/`reject`
are excluded from sampling because they are excluded from production by §1).

**Minimum sample n per stratum.**

- Draw a uniform random sample of **n = max(30, ceil(0.05 × N_stratum))** rows
  per stratum, capped at **n = 200** per stratum, where `N_stratum` is the
  count of production-eligible recovered rows in that stratum.
- If `N_stratum < 30`, sample **100%** of the stratum (full enumeration) — small
  strata are not exempt; they are fully audited.
- Sampling is without replacement, from the frozen recovered set for that run.

**Measured statistic.** Per stratum, the **EIN-misattribution rate**:

```
EIN-misattribution rate (stratum)
  = (# sampled docs whose content or source contradicts the attributed EIN)
    / (# sampled docs in the stratum)
```

A sampled document "contradicts" when manual/assisted QA finds the document's
content or its true source belongs to a different organization than the
attributed EIN, or no organization can be confirmed (treated as a miss).

---

## 3. Acceptance threshold (PROVISIONAL strawman — fail-closed)

These are the **default** thresholds. The sponsor may **loosen with written
justification** or tighten; until then they are binding and the standard is
marked PROVISIONAL (see header).

| Tier / stratum | Maximum tolerable sampled EIN-misattribution rate |
|----------------|---------------------------------------------------|
| `high` strata | **≤ 1%** |
| `medium` strata | **≤ 1%** |
| `low` tier | **Not rate-gated — excluded from production by default.** Its presence alone bars production ingestion of that row. |
| `reject` tier | Excluded; quarantined. |

- A stratum **passes** iff its measured EIN-misattribution rate ≤ the threshold
  for its tier.
- The bound is deliberately fail-closed (1%, not a looser number) so an
  unratified standard errs toward *blocking* bad data, not admitting it.
- **No production write may proceed against this threshold while the standard
  is PROVISIONAL** (header gate). Ratification fixes the number; it does not
  remove the gate.

---

## 4. Failure action — stratum-level quarantine

Failure handling is **per stratum, not all-or-nothing**:

- A stratum whose measured rate **exceeds** its threshold is **quarantined**:
  held out of all downstream/production use, flagged with its measured rate,
  and routed back for rework (re-attribution, re-fetch, or manual triage).
- Strata that **pass** proceed to downstream use independently — a single
  failing stratum does not block passing strata, and a passing run does not
  drag a failing stratum through.
- `low` and `reject` rows are quarantined by definition (§1), independent of
  any stratum's rate.
- Quarantine is reversible only by rework that brings a fresh sample of the
  stratum to ≤ threshold; the rework and its new sample are recorded.

---

## 5. Extraction-QA gate (symmetry requirement — stub)

Attribution QA gates *which documents are trustworthy*. By symmetry, **text
extraction quality must be gated before vocabulary/taxonomy mining** — clean
attribution of an unreadable extraction is still unusable.

- **Requirement (owned here, detail deferred):** before any vocabulary or
  taxonomy mining (macro-plan §Workstream 7), extracted text must pass an
  extraction-quality bar (e.g. minimum text yield, non-garbled ratio,
  language/encoding sanity). The detailed bar is **deferred to the Docling
  project** (macro-plan §Workstream 6).
- **Ownership:** this symmetry requirement is explicitly owned by this standard
  so it is not forgotten. The Docling project MUST define the concrete
  extraction-QA bar and link it back here; downstream mining specs MUST
  reference both this standard and the extraction-QA bar once it exists.

---

## 6. Field provenance (source of truth)

| Field | Provenance | Notes |
|-------|------------|-------|
| `state` | **existing** | From `nonprofits_seed` (seed/resolution layer intact per macro-plan §Current Understanding). |
| `NTEE-major` | **existing (derived)** | Derived from the `nonprofits_seed` NTEE code; not newly collected. |
| `recovery-source` | **derived** | Determined at recovery time from the tool/provenance path (`pass1`, `pass2`, `s3-orphan`); recorded by the recovery run (surfaced via `corpus.discovered_via`). |
| `ein` | **existing** | From crawl path / S3 metadata / `fetch_log`; must join to `nonprofits_seed`. |
| `source_url` | **existing** | From `fetch_log` / S3 object metadata / crawl-discovery record. |
| `content_sha256` | **existing** | Content address of the archived object. |
| `attribution_confidence` | **new derived** | Computation defined by §1 of this standard. Persistence is downstream attribution-schema work (Spec 0048 non-goal) — treat as computed-at-evaluation until that schema lands. |

---

## 7. Checklist (must be checkable, not prose)

- [x] Confidence rubric: all four tiers (high/medium/low/reject) with the exact
      fields/evidence producing each (§1).
- [x] Sampling design: strata defined, minimum `n` per stratum stated, measured
      statistic named (§2).
- [x] Threshold: concrete number present (strawman 1%); PROVISIONAL marker
      present because unratified (header + §3).
- [x] Failure action: stratum-level quarantine explicitly stated, not
      all-or-nothing (§4).
- [x] Field provenance table present (existing / derived / downstream) (§6).
- [x] Extraction-QA symmetry stub present with recorded ownership (§5).
- [x] Binding clause present — downstream specs fail review if non-compliant
      (§0).

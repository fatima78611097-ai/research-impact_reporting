# Research Pipeline Manifest — the transfer bill-of-materials for production

*The curated component list that "makes up" research, init → end. Every item the production port
must carry (or consciously waive). Pairs with `production-requirements.md` (the obligations) and
`test_suite.py` (the acceptance contract). Updated 2026-06-12.*

## Stage 0 — Corpus inclusion (before anything runs)
| Component | Where | Status |
|---|---|---|
| Material-type filter (annual/impact in; financial/not_relevant out) | corpus `material_type` | live (old classifier; quality caveat) |
| **Low-value doc gate** — flavor A plain-text (figs=0 + ≥1500 chars/pg), flavor C narrative/event (>60% non-measurable metrics) | `lowvalue_gate.doc_included()`, wired as `run_doc` stage 0 | **built + applied; operator-validated 8/8** |
| Attribution gates (sha + archived PDF + EIN + NTEE present) | macro-plan / REQ-015 | pending |
| Garble detector (glyph-soup docs → re-extract or quarantine) | ENH-004; mostly mooted by config B | pending (small) |
| **NTEE cohort onboarding** — new cohorts audit through research before production | REQ-019 (operating model) | encoded |

## Stage 1 — Parse
| Component | Where | Status |
|---|---|---|
| Docling **config B** (pypdfium2 backend; defaults otherwise) | REQ-021 | **LOCKED** (garble 1→0, +30% bare-number capture) |
| Provenance capture (per-element text+bbox+page → `source_locations`) | parse workers | live on new parses |
| Per-page dimensions + spread/landscape flag (coordinate-space mapping for overlays) | REQ-017 | pending |

## Stage 2 — Render (tagged input for the model)
| Component | Where | Status |
|---|---|---|
| Marker tagging (`t##` text / `c##` cells) + idmap (text, row, bbox, page) | `render.render_tagged` | built |
| Single-namespace `m##` markers (prefix-swap prevention) | REQ-020 — apply at next extraction | drafted, pending |

## Stage 3 — Geometry re-grouping (designed pages)
| Component | Where | Status |
|---|---|---|
| Number↔caption pairing (right/below/short-header-above; mutual-nearest; abstain) | `regroup.py` | **built; dev 1% regression; P20 out-of-sample validated** |
| **Input-side placement (re-grouped text → extraction): BORN-CORRECT metrics demonstrated** — 20/20 previously-mispaired numbers correctly paired on first extraction pass (`exp_regroup_input.py`, Calm Waters + Capstone) | experiment 2026-06-12 | **validated end-to-end** |
| Caption filters (digits, qualifiers, finite-verb sentences, financial lines) | `regroup._bad_label` | built |
| **Flag-mode policy**: disagreement → quarantine `mispairing_geometry`, never silent relabel | applied to canonical | policy locked |

## Stage 4 — Extraction (LLM, sha-locked prompts)
| Component | Where | Status |
|---|---|---|
| Metric selection prompt (comprehensive/standalone) | `comp-metric-prompt.KNOWN-GOOD.txt`, sha `b51dc96a` | **LOCKED** (92.2% vision-validated) |
| Grounding pass (assigns value_ref/subject_ref markers) | `comp-metric-grounding.v1.txt` | locked |
| Story stage-1 prompt (3-element test, 5-class routing, marker spans) | `story-extract-prompt.v1.txt`, sha `15749087…` | v1 (sha-lock after next audit cycle) |
| Prompt-change discipline: re-validate against fixture or no change | process | locked |

## Stage 5 — Grounding gate (deterministic)
| Component | Where | Status |
|---|---|---|
| `candidate_numbers` (spelled-out, abbreviated, **hyphenated scale words**) | `gate.py` | built |
| Small-int **exact standalone-token** grounding (no tolerance floor, no substring) | `gate.value_grounded` | built (kills invented 1s/2s) |
| **Within-token** digit-substring fallback (no cross-number spans) | `gate.value_grounded` | built (operator catch, efd61fea:11) |
| Subject grounding (word-overlap + co-location) | `gate.subject_grounded` | built (known soft spot: bare co-location — geometry layer covers) |
| Verbatim-or-quarantine doctrine: values must be PRINTED, never inferred | policy | locked |

## Stage 6 — Validity (is-a-metric, two-stage: regex sliver → DeepSeek)
| Component | Where | Status |
|---|---|---|
| Small-int measurable-value check (≤20 → MEASURE/NOT) | `measurable_value_check.py`; `verdict(measured=)` hook | built + wired |
| Item-1 rule classes: tenure, year-as-value (bare-token vs comma), forecast, population factoid, **episode-duration**, **named-person anecdote** | `item1_nonmetric_rules.py` | built + applied |
| Paraphrase-drift (or→and distortion) | `paraphrase_drift_check.py` | built (0.06% rate) |
| Locked definition: metric = 4 value classes; 8 tier-0 rejects + activity | log §1 | **operator-locked** |

## Stage 7 — Repair (recall recovery, guarded)
| Component | Where | Status |
|---|---|---|
| Twin-prefix repair (c↔t swap, re-gate as guard) | `pipeline.decide()` | built + applied |
| Neighbor ±1 re-anchor (value+subject both ground; small-int + year excluded) | `pipeline.decide()` | built + applied |
| Rounded-value repair | — | **REJECTED** (50% precision — near-duplicate trap; keep rejected) |
| P20 recovery pool (~547 across old quarantines @ 92% precision) | `regroup-p20-recoveries.json` | identified, apply at convergence |

## Stage 8 — Verify stage (the at-scale residual answer)
| Component | Where | Status |
|---|---|---|
| Two-stage architecture: cheap filter → page-read verdict → act | validated end-to-end (P20 + splice demo) | REQ-022 wiring pending |
| Qwen2.5-VL-7B on g6 (one-command launcher, auto-terminate, weight cache) | `qwen_eval/launch_qwen.py` | validated ~90% on hard pages, ~$1/600 docs |
| Multi-voice/splice detector (stories) → AI page-read verdicts | built; demo on 41 flags | pattern proven |

## Stage 9 — Stories (parallel track, same architecture)
| Component | Where | Status |
|---|---|---|
| Locked definition (3 elements, routed classes, span rules, PII flags) | `impact-story-definition-DRAFT.md` v3 LOCKED | locked |
| Stage-2 qualifier (specific-beneficiary vs population; 6000-char input) | `story_stage2.py` | built (60→87% precision, 94% retention) |
| Deterministic span gate (markers exist, narrative, beneficiary fidelity) | `span_check.py` | built |
| PII posture: capture-all + flags; pseudonymize at publication; **visible bracketed redaction** (REQ-016); name=floor / sensitive-context=ceiling | definition §4 | locked |

## Held (logged, never published until proven)
Tier (value-class ranking, 88% agreement) · emotional-resonance scoring · theme taxonomy.

## Cross-cutting machinery (what makes it provable — port these too)
| Component | Where |
|---|---|
| Vision ground-truth fixture (1,605 metrics) + story audit fixture (100) | `fixtures/`, `story-audit-result.json` |
| Scoring harness (locked definition encoded) | `score_pipeline.py` |
| **Regression suite — 9 checks + PENDING-PROD printed every run** | `test_suite.py` (= production acceptance contract, REQ-023) |
| Per-record decision attribution (`decided_by`/`decided_detail`) | canonical data + both viewers |
| Review tools + durable verdict store (loud save failures) | `metric-review.html`, `story-review.html`, `review_server.py` |
| Snapshot-before-apply policy (`review-data.before-*.json`) | process |
| Drift monitors (per-class rates per run) | stories §2a; generalize at port |
| REQ registry (obligations; 17 open / 1 done) | `production-requirements.md` |

## Convergence sequence (the plan around this manifest)
1. Benchmark production's existing R1/R2 gate against the fixture (replace-vs-graft per component)
2. SPIDER spec adopting the REQ registry as acceptance criteria
3. Port stage-by-stage behind the suite (each stage must hold its manifest numbers)
4. End-to-end run on fresh docs → converts 97±2 estimate into the certified number
5. Cutover only when `test_suite.py` is GREEN against production output (REQ-023)

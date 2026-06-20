"""The metric-engine CONTRACT — the one normalized shape every adapter must produce
and the core consumes. Locked Phase 0 (2026-06-20).

Design rules this encodes:
  - The model's own sentence is the metric (`statement`). The core NEVER rewrites it.
  - Both input adapters (dev sample pool, prod corpus) must build this exact shape.
  - The gate attaches its result (`decision`/`reason`/`flags`) — it does not mutate the metric.
  - Provenance carries enough to verify grounding + draw the value/label boxes.

Derived from comp-metric-regression/metric-schema.md, narrowed to what the engine needs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# The five logic-model / theory-of-change tiers the prompt assigns.
TIERS = ("outcome_impact", "reach_output", "capacity_input", "activity_count", "financial")

# A gate decision.
PUBLISH = "publish"
QUARANTINE = "quarantine"


@dataclass
class Provenance:
    """Where the value and the label physically live in the source document."""
    value_page: int | None = None         # the value and label can sit on DIFFERENT pages on a
    subject_page: int | None = None       #   designed layout — keep both (the viewer boxes need it)
    value_ref: str | None = None          # ⟨marker⟩ on the value's line/cell
    subject_ref: str | None = None        # ⟨marker⟩ on the label's line/cell
    value_bbox: dict | None = None        # {l,t,r,b,coord_origin} — drives the red box + geometry checks
    subject_bbox: dict | None = None      # blue box
    value_text: str | None = None         # resolved text at value_ref
    subject_text: str | None = None       # resolved text at subject_ref
    source_snippet: str | None = None     # verbatim span the model cited
    same_marker: bool = False             # value & label share one marker (inline; no pairing risk)
    section_heading: str | None = None    # heading of the section the metric sits in (grounds `program`)

    @property
    def page(self) -> int | None:
        """Primary display page — the value's, falling back to the label's."""
        return self.value_page or self.subject_page


@dataclass
class Metric:
    """One extracted metric, normalized. Adapters emit this; the gate reads it."""
    # —— identity ——
    content_sha256: str                   # the doc key (orgs file many reports; EIN alone collides)
    idx: int                              # position within the doc's metric list
    org: str | None = None
    report_year: int | None = None
    url: str | None = None

    # —— the metric (the model's output — statement is the model's own words, never rewritten) ——
    statement: str = ""
    value: float | int | None = None
    value_text: str | None = None         # as printed ($2.3M / 16 / 14%) — survives unit-scale
    unit: str | None = None
    tier: str | None = None               # one of TIERS
    subject: str | None = None            # the raw "what" (resolved label text)
    program: str | None = None            # named program/service this metric belongs to (null if the report doesn't state one)

    # —— provenance ——
    prov: Provenance = field(default_factory=Provenance)

    # —— gate result (None/empty until the gate runs; the gate fills these, never the adapters) ——
    decision: str | None = None           # PUBLISH | QUARANTINE
    reason: str = ""                      # why, if quarantined (a fixed token, not reflected source text)
    flags: list[str] = field(default_factory=list)   # non-blocking flags, e.g. "incomplete", "mispair_suspect"
    flag_detail: dict = field(default_factory=dict)   # flag -> short reason

    @property
    def metric_id(self) -> str:
        return f"{self.content_sha256[:8]}:{self.idx}"

    @property
    def sha8(self) -> str:
        return self.content_sha256[:8]

    def add_flag(self, name: str, detail: str = "") -> None:
        if name not in self.flags:
            self.flags.append(name)
        if detail:
            self.flag_detail[name] = detail


@dataclass
class CheckResult:
    """What a single gate check returns. A check looks at ONE metric and reports one of:
      - verdict="ok"          -> nothing to say
      - verdict="quarantine"  -> this metric should not publish (with a reason)
      - verdict="flag"        -> publishable but worth surfacing (a named, non-blocking flag)
    """
    verdict: str = "ok"                   # "ok" | "quarantine" | "flag"
    reason: str = ""
    flag: str = ""                        # the flag name when verdict == "flag"


# A check is any callable Metric -> CheckResult. Checks are pure and individually testable;
# the gate composes them (see core/gate.py).

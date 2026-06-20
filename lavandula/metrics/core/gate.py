"""The gate — runs the checks over a document's metrics and records the verdict.

Two layers:
  - CROSS-metric checks run first over the whole document list (currently: dedup).
  - PER-metric checks run on each survivor, in REGISTRY order. First `quarantine` wins
    (the reason is the first failing check); `flag` verdicts accumulate, never block.

The gate NEVER rewrites `statement` — it only sets `decision` / `reason` / `flags`.
Adding a gate type = add one check here (+ a fixture + a measured number in tests/).
Written ONCE; both harnesses (dev + prod) call this.
"""
from __future__ import annotations

from collections.abc import Callable

from .types import Metric, CheckResult, PUBLISH, QUARANTINE
from .checks.quality import quality_text, quality_subject
from .checks.vague_quantity import vague_quantity
from .checks.measurable import measurable
from .checks.is_a_metric import is_a_metric
from .checks.incompleteness import incompleteness
from .checks.mispairing import mispairing
from .checks.program_grounding import program_grounding
from .checks.dedup import duplicate_indices

Check = Callable[[Metric], CheckResult]

# Per-metric checks, in order: quarantine checks first, flag checks last.
REGISTRY: list[Check] = [
    quality_text,        # tenure / financial-statement fragment      -> quarantine
    quality_subject,     # gratitude / contentless header / empty      -> quarantine
    vague_quantity,      # "thousands of" rendered as a precise number -> quarantine
    measurable,          # value 1/2 not a printed count (event)       -> quarantine
    is_a_metric,         # ranking / forecast / duration / award / date-> quarantine
    incompleteness,      # bare generic people-count, no action        -> flag
    mispairing,          # value & label far apart on the page         -> flag
    program_grounding,   # program disagrees with the section heading  -> flag
]


def gate_one(metric: Metric, registry: list[Check] | None = None) -> Metric:
    """Run the per-metric checks over one metric; set decision/reason/flags in place."""
    checks = REGISTRY if registry is None else registry
    if metric.decision == QUARANTINE:          # already quarantined (e.g. by dedup) — leave it
        return metric
    metric.decision = PUBLISH
    metric.reason = ""
    for check in checks:
        result = check(metric)
        if result.verdict == QUARANTINE:
            metric.decision = QUARANTINE
            metric.reason = result.reason
            return metric                      # first quarantine wins
        if result.verdict == "flag":
            metric.add_flag(result.flag, result.reason)
    return metric


def gate_all(metrics: list[Metric], registry: list[Check] | None = None) -> list[Metric]:
    """Gate a document's metrics: cross-metric checks (dedup) then per-metric checks."""
    dups = duplicate_indices(metrics)                       # CROSS-metric
    for m in metrics:
        if m.idx in dups:
            m.decision = QUARANTINE
            m.reason = "duplicate"
        gate_one(m, registry)                               # per-metric (skips already-quarantined)
    return metrics

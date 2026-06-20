"""The gate — runs a registry of small checks over one metric and records the verdict.

This is the WHOLE gate: a list of pure checks, run in order. First `quarantine` wins
(deterministic — the reason is the first failing check). `flag` verdicts accumulate and
do NOT block publish. Anything that survives is `publish`.

The gate NEVER rewrites `metric.statement` — it only sets `decision` / `reason` / `flags`.
Adding a new gate type = add one check to the registry (in core/checks/), with a fixture
and a measured number. Written ONCE here; both harnesses (dev + prod) call this.

Phase 0: the registry is intentionally empty. Checks are ported/built in Phase 2.
"""
from __future__ import annotations

from collections.abc import Callable

from .types import Metric, CheckResult, PUBLISH, QUARANTINE

Check = Callable[[Metric], CheckResult]

# The ordered check registry. Quarantine checks first (cheapest/most decisive earlier),
# flag checks after. Populated in Phase 2 — see core/checks/.
REGISTRY: list[Check] = []


def gate_one(metric: Metric, registry: list[Check] | None = None) -> Metric:
    """Run every check over one metric; set decision/reason/flags in place. Returns the metric."""
    checks = REGISTRY if registry is None else registry
    metric.decision = PUBLISH
    metric.reason = ""
    for check in checks:
        result = check(metric)
        if result.verdict == QUARANTINE:
            metric.decision = QUARANTINE
            metric.reason = result.reason
            return metric                      # first quarantine wins; stop
        if result.verdict == "flag":
            metric.add_flag(result.flag, result.reason)
    return metric


def gate_all(metrics: list[Metric], registry: list[Check] | None = None) -> list[Metric]:
    """Gate a document's metrics. (Cross-metric checks like dedup live in core/checks and
    are applied here over the list before per-metric checks — wired in Phase 2.)"""
    return [gate_one(m, registry) for m in metrics]

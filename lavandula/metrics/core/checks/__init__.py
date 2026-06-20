"""The gate's checks — one pure function per check: Metric -> CheckResult.

Each check is individually testable and ships with a fixture of known-bad / known-good
examples plus a measured recall + false-positive number (see ../../tests). Checks are
registered into core.gate.REGISTRY. Ported/built in Phase 2:
  dedup · quality_text · quality_subject · incompleteness · is_a_metric · vague_quantity
  · measurable · mispairing(flag).  Empty for now (Phase 0).
"""

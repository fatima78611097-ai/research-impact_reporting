"""Prod OUTPUT: gated Metrics -> RDS commit (advisory lock, atomic/idempotent upsert,
per-run report). Re-implements the Spec 0069 runner PATTERNS against this contract
(reference only, not imported). Phase 3a; orchestrator wiring Phase 3b."""
def commit(metrics, run_id):
    raise NotImplementedError("Phase 3a")

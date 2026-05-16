"""Central registry of Postgres advisory lock IDs.

Every caller of `pg_advisory_xact_lock()` (or its siblings) MUST use a
key from this module. Keys are arbitrary but must be unique
project-wide to avoid cross-feature deadlocks.

Adding a new lock? Add a named constant here and import it at the
call site. A CI grep gate fails the build if `pg_advisory_xact_lock`
is called with a hardcoded literal instead of a name from this module.
"""
from __future__ import annotations


# Budget ledger reserve/settle/release critical section.
# Used by lavandula.reports.budget.check_and_reserve / settle / release
# to serialize the read-SUM-then-INSERT pattern that enforces the
# classifier budget cap.
# Hex is a stable arbitrary 32-bit int; the value itself has no meaning
# beyond "unique across the project." Spec/plan used the pseudo-hex
# `0xB0DGE7`; real hex needs digits in [0-9a-f], hence `0xB0D6E7`.
BUDGET_LEDGER_RESERVE: int = 0xB0D6E7

# Docling parse orchestrator — prevents concurrent orchestrator launches.
# Used by lavandula.parse.db.acquire_advisory_lock / release_advisory_lock
# and the parse_documents management command.
DOCLING_PARSE_ORCHESTRATOR: int = 0xD0C114

# Docling parse worker — prevents concurrent worker processes.
# Used by lavandula.parse.worker at startup to verify single-worker invariant.
DOCLING_PARSE_WORKER: int = 0xD0C115


__all__ = [
    "BUDGET_LEDGER_RESERVE",
    "DOCLING_PARSE_ORCHESTRATOR",
    "DOCLING_PARSE_WORKER",
]

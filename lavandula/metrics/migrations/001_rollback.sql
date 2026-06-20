-- ROLLBACK migration 001 — removes the lava_impact schema and EVERYTHING in it.
-- Use only to undo 001. DESTRUCTIVE: drops lava_impact.metrics + lava_impact.runs and all rows.
-- Safe while the engine is pre-production (no live readers of lava_impact yet).

DROP SCHEMA IF EXISTS lava_impact CASCADE;

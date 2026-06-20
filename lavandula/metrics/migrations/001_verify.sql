-- VERIFY migration 001 — run AFTER 001_create_lava_impact.sql.
-- Each query should return the rows described in its comment. Read-only; safe to re-run.

-- 1) schema exists  -> expect 1 row: lava_impact
SELECT nspname AS schema FROM pg_namespace WHERE nspname = 'lava_impact';

-- 2) both tables exist -> expect 2 rows: metrics, runs
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'lava_impact' ORDER BY table_name;

-- 3) metrics columns present -> expect 26 rows (the full Metric contract)
SELECT count(*) AS metrics_columns FROM information_schema.columns
WHERE table_schema = 'lava_impact' AND table_name = 'metrics';

-- 4) research_app has DML on the tables -> expect DELETE/INSERT/SELECT/UPDATE for both tables
SELECT table_name, privilege_type FROM information_schema.role_table_grants
WHERE table_schema = 'lava_impact' AND grantee = 'research_app'
ORDER BY table_name, privilege_type;

-- 5) research_app can USE the schema -> expect t (true)
SELECT has_schema_privilege('research_app', 'lava_impact', 'USAGE') AS research_app_can_use;

-- 6) indexes present -> expect metrics_sha_idx, metrics_run_idx, metrics_decision_idx
SELECT indexname FROM pg_indexes
WHERE schemaname = 'lava_impact' AND tablename = 'metrics' ORDER BY indexname;

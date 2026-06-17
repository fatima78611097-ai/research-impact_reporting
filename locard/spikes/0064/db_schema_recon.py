"""0064 — read-only schema recon (v2): find parse/metrics/verification tables."""
import sys
sys.path.insert(0, "/home/ubuntu/research")
from sqlalchemy import text
from lavandula.common.db import make_ro_engine

eng = make_ro_engine()
with eng.connect() as c:
    print("=== ALL non-system schemas ===")
    for r in c.execute(text(
        "SELECT schema_name FROM information_schema.schemata "
        "WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast') "
        "ORDER BY 1")):
        print(" ", r[0])

    print("\n=== tables whose name hints parse/metric/section/chunk/llm/story/verif ===")
    rows = list(c.execute(text(
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_name ~* "
        "'(parse|metric|section|chunk|llm|story|stories|verif|extract|tier|ground)' "
        "ORDER BY 1,2")))
    for r in rows:
        print(f"  {r[0]}.{r[1]}")

    # For each candidate metrics/sections table, dump columns
    for sch, name in [(r[0], r[1]) for r in rows]:
        if not (name.lower().find("metric") >= 0 or name.lower().find("section") >= 0
                or name.lower().find("chunk") >= 0 or name.lower().find("stor") >= 0):
            continue
        print(f"\n--- columns: {sch}.{name} ---")
        for col in c.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema=:s AND table_name=:t ORDER BY ordinal_position"),
            {"s": sch, "t": name}):
            print(f"    {col[0]:30} {col[1]}")

"""0064 — distributions for sizing the stratified sample (app role = research_app)."""
import sys
sys.path.insert(0, "/home/ubuntu/research")
from sqlalchemy import text
from lavandula.common.db import make_app_engine

eng = make_app_engine()
with eng.connect() as c:
    print("=== lava_vocab.llm_metrics columns ===")
    for col in c.execute(text(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema='lava_vocab' AND table_name='llm_metrics' "
        "ORDER BY ordinal_position")):
        print(f"    {col[0]:30} {col[1]}")

    print("\n=== extraction_runs (id, tag, counts) ===")
    for col in c.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='lava_vocab' AND table_name='extraction_runs' "
        "ORDER BY ordinal_position")):
        print("   ", col[0])
    print("  --- rows ---")
    for r in c.execute(text("SELECT * FROM lava_vocab.extraction_runs ORDER BY 1")):
        print("   ", dict(r._mapping))

    print("\n=== run_id distribution in llm_metrics ===")
    for r in c.execute(text(
        "SELECT run_id, count(*) AS metrics, count(DISTINCT content_sha256) AS docs "
        "FROM lava_vocab.llm_metrics GROUP BY run_id ORDER BY run_id")):
        print(f"    run {r[0]}: {r[1]} metrics across {r[2]} docs")

    print("\n=== verification_tier distribution (all runs) ===")
    for r in c.execute(text(
        "SELECT run_id, verification_tier, count(*) "
        "FROM lava_vocab.llm_metrics GROUP BY run_id, verification_tier "
        "ORDER BY run_id, verification_tier")):
        print(f"    run {r[0]:>3}  {str(r[1]):20} {r[2]}")

    print("\n=== text_source distribution in lava_parse.documents ===")
    for r in c.execute(text(
        "SELECT text_source, parse_outcome, count(*) FROM lava_parse.documents "
        "GROUP BY text_source, parse_outcome ORDER BY 3 DESC LIMIT 25")):
        print(f"    {str(r[0]):16} {str(r[1]):14} {r[2]}")

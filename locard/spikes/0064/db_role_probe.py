"""0064 — probe which IAM role can read lava_parse / lava_vocab, dump columns."""
import sys
sys.path.insert(0, "/home/ubuntu/research")
from sqlalchemy import text
from lavandula.common.db import make_engine, make_app_engine
from lavandula.common.secrets import get_secret

HOST = get_secret("rds-endpoint"); PORT = int(get_secret("rds-port"))
DB = get_secret("rds-database")


def engine_for(user):
    return make_engine(host=HOST, port=PORT, database=DB, user=user,
                       region="us-east-1", schema=None)

CANDIDATES = ["app(rds-app-user)", "docling_writer"]
for label in CANDIDATES:
    try:
        eng = make_app_engine() if label.startswith("app") else engine_for(label)
        with eng.connect() as c:
            who = c.execute(text("SELECT current_user")).scalar()
            can_parse = c.execute(text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='lava_parse'")).scalar()
            can_vocab = c.execute(text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='lava_vocab'")).scalar()
            print(f"[OK] {label}: current_user={who} lava_parse_tables={can_parse} lava_vocab_tables={can_vocab}")
    except Exception as e:
        print(f"[FAIL] {label}: {type(e).__name__}: {str(e)[:140]}")

# Dump columns of lava_parse.documents + lava_vocab.llm_metrics via whichever worked
for user in ["docling_writer"]:
    try:
        eng = engine_for(user)
        with eng.connect() as c:
            for tbl in ("lava_parse.documents", "lava_parse.parse_runs",
                        "lava_vocab.llm_metrics", "lava_vocab.extraction_runs"):
                sch, name = tbl.split(".")
                cols = list(c.execute(text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema=:s AND table_name=:t ORDER BY ordinal_position"),
                    {"s": sch, "t": name}))
                print(f"\n--- {tbl} ({len(cols)} cols) ---")
                for col in cols:
                    print(f"    {col[0]:30} {col[1]}")
    except Exception as e:
        print(f"[FAIL dump as {user}] {type(e).__name__}: {str(e)[:140]}")

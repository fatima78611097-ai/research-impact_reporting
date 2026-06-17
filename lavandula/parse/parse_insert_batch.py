"""Batch prov re-parse for the gold-baseline set (metric-grounding step 2 → baseline).

Reads a seeded sha list from S3, re-parses each doc with the SHIPPING code
(chunking.extract_* with OCR-on = production's keep-OCR-when-uncertain default),
writes via db.insert_document (delete + reinsert → populates source_locations /
cell_locations / bbox), and reports aggregate population. One g6, loops all docs.
Per-doc try/except: one bad doc never aborts the batch. Run on a g6.

    python -m lavandula.parse.parse_insert_batch --shas-s3-key deploy/baseline-100.json \
        --host H --port P --database D [--bucket B] [--result-s3-key K]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from importlib.metadata import version as _pkg_version
from pathlib import Path


def _parse_version() -> str:
    from lavandula.parse import config
    try:
        base = f"docling-{_pkg_version('docling')}"
    except Exception:
        base = "docling-unknown"
    # Match worker._parse_version: append the output-schema version. See PARSE_SCHEMA_VERSION.
    return f"{base}+{config.PARSE_SCHEMA_VERSION}"


def _connect(host, port, database):
    import boto3
    from lavandula.parse import db

    rds = boto3.client("rds", region_name="us-east-1")
    return db.get_connection(
        host=host, port=int(port), database=database, user="docling_writer",
        iam_token_fn=lambda: rds.generate_db_auth_token(
            DBHostname=host, Port=int(port), DBUsername="docling_writer", Region="us-east-1"
        ),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shas-s3-key", required=True)
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", required=True)
    ap.add_argument("--database", required=True)
    ap.add_argument("--bucket", default="lavandula-nonprofit-collaterals")
    ap.add_argument("--result-s3-key", default="deploy/baseline-100-result.json")
    a = ap.parse_args()

    import boto3
    from lavandula.parse import chunking, config, db

    s3 = boto3.client("s3")
    spec = json.loads(s3.get_object(Bucket=a.bucket, Key=a.shas_s3_key)["Body"].read())
    docs = spec["docs"]
    print(f"loaded {len(docs)} shas from s3://{a.bucket}/{a.shas_s3_key} (seed {spec.get('seed')})", flush=True)

    conn = _connect(a.host, a.port, a.database)
    opts = chunking.build_parse_options(skip_ocr=False, downgrade=False, table_mode_fast=False)
    tmpdir = Path(tempfile.mkdtemp())

    ok, results = 0, []
    t_start = time.time()
    for i, d in enumerate(docs):
        sha, org_ein = d["sha"], d["org_ein"]
        rec = {"sha": sha, "ok": False}
        try:
            pdf = tmpdir / f"{sha}.pdf"
            s3.download_file(config.S3_BUCKET, f"{config.S3_PREFIX}{sha}.pdf", str(pdf))
            t0 = time.time()
            doc_obj = chunking.parse_pdf(pdf, opts)
            sections = chunking.extract_sections(doc_obj)
            tables = chunking.extract_tables(doc_obj, sections)
            meta = chunking.get_document_metadata(doc_obj)
            doc = {
                "sha": sha, "org_ein": org_ein, "parse_version": _parse_version(),
                "page_count": meta["page_count"], "section_count": len(sections),
                "table_count": len(tables), "figure_count": meta["figure_count"],
                "total_text_chars": sum(s["char_count"] for s in sections),
                "parse_duration_ms": int((time.time() - t0) * 1000),
                "docling_convert_ms": None, "parse_outcome": "ok", "error": None,
                "metadata_json": config.filter_metadata(meta.get("metadata")),
                "sections": sections, "tables": tables,
            }
            db.delete_document_data(conn, sha)
            db.insert_document(conn, doc)
            ok += 1
            rec.update(ok=True, sections=len(sections), tables=len(tables),
                       sec_with_loc=sum(1 for s in sections if s.get("source_locations")),
                       tab_with_cells=sum(1 for t in tables if t.get("cell_locations")))
            pdf.unlink(missing_ok=True)
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        results.append(rec)
        if (i + 1) % 5 == 0 or not rec["ok"]:
            el = int(time.time() - t_start)
            print(f"[{i+1}/{len(docs)}] ok={ok} fail={i+1-ok} elapsed={el}s last={sha[:8]}"
                  + (f" ERR {rec.get('error')}" if not rec["ok"] else ""), flush=True)

    # aggregate readback from RDS across the 100 shas
    shas = [d["sha"] for d in docs]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(DISTINCT content_sha256) FROM lava_parse.sections "
            "WHERE content_sha256 = ANY(%s) AND source_locations IS NOT NULL", (shas,))
        docs_with_secloc = cur.fetchone()[0]
        cur.execute(
            "SELECT count(DISTINCT content_sha256) FROM lava_parse.tables "
            "WHERE content_sha256 = ANY(%s) AND cell_locations IS NOT NULL", (shas,))
        docs_with_cellloc = cur.fetchone()[0]

    summary = {
        "total": len(docs), "ok": ok, "failed": len(docs) - ok,
        "docs_with_source_locations": docs_with_secloc,
        "docs_with_cell_locations": docs_with_cellloc,
        "elapsed_s": int(time.time() - t_start),
        "failures": [r for r in results if not r["ok"]],
    }
    print("\n==== BATCH SUMMARY ====")
    print(json.dumps(summary, indent=1))
    s3.put_object(Bucket=a.bucket, Key=a.result_s3_key,
                  Body=json.dumps({"summary": summary, "results": results}, indent=1).encode())
    print(f"result -> s3://{a.bucket}/{a.result_s3_key}")
    return 0 if ok == len(docs) else 1


if __name__ == "__main__":
    sys.exit(main())

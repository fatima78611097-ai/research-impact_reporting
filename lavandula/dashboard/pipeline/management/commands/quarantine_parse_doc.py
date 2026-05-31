"""Operator tool for the Spec 0058 §3.5 parse quarantine (parse_blocklist).

Governed + reversible: every entry carries reason / who / when / evidence, and
removal is one step. Quarantine EXCLUDES a doc from parse enqueue
(populate_work_queue anti-joins parse_blocklist); it never deletes the PDF.

Usage:
    python manage.py quarantine_parse_doc --list
    python manage.py quarantine_parse_doc --sha e038a9e75317ff86 \
        --reason "segfaults docling pdf_parsers.so (exit 139)" --by operator
    python manage.py quarantine_parse_doc --sha <full-sha> --remove
"""
from __future__ import annotations

import json
import re

from django.core.management.base import BaseCommand
from sqlalchemy import text as sa_text

from lavandula.common.db import make_app_engine

_SHA_RE = re.compile(r"^[a-f0-9]{64}$")
_PREFIX_RE = re.compile(r"^[a-f0-9]{6,64}$")


class Command(BaseCommand):
    help = "Manage the parse quarantine blocklist (Spec 0058 §3.5)"

    def add_arguments(self, parser):
        parser.add_argument("--sha", type=str, default=None,
                            help="content_sha256 (full) or a unique prefix")
        parser.add_argument("--reason", type=str, default=None)
        parser.add_argument("--by", type=str, default="operator")
        parser.add_argument("--evidence", type=str, default=None,
                            help="JSON string stored as evidence_json")
        parser.add_argument("--remove", action="store_true",
                            help="Un-quarantine (remove the blocklist row)")
        parser.add_argument("--list", action="store_true",
                            help="List all quarantined docs")

    def handle(self, *args, **opts):
        engine = make_app_engine()

        if opts["list"]:
            self._list(engine)
            return

        sha_arg = opts["sha"]
        if not sha_arg or not _PREFIX_RE.match(sha_arg):
            self.stderr.write("Provide --sha (full 64-hex or a >=6 hex prefix), or --list")
            raise SystemExit(1)

        sha = self._resolve(engine, sha_arg)

        if opts["remove"]:
            with engine.begin() as conn:
                res = conn.execute(
                    sa_text("DELETE FROM lava_parse.parse_blocklist WHERE content_sha256 = :s"),
                    {"s": sha},
                )
            self.stdout.write(f"un-quarantined {sha}" if res.rowcount else f"{sha} was not quarantined")
            return

        if not opts["reason"]:
            self.stderr.write("--reason is required to quarantine")
            raise SystemExit(1)

        evidence = opts["evidence"]
        if evidence is not None:
            try:
                json.loads(evidence)  # validate it is JSON
            except ValueError as exc:
                self.stderr.write(f"--evidence is not valid JSON: {exc}")
                raise SystemExit(1)

        with engine.begin() as conn:
            conn.execute(
                sa_text(
                    """
                    INSERT INTO lava_parse.parse_blocklist
                        (content_sha256, reason, quarantined_by, evidence_json)
                    VALUES (:s, :r, :b, CAST(:e AS JSONB))
                    ON CONFLICT (content_sha256) DO UPDATE SET
                        reason = EXCLUDED.reason,
                        quarantined_by = EXCLUDED.quarantined_by,
                        quarantined_at = now(),
                        evidence_json = EXCLUDED.evidence_json
                    """
                ),
                {"s": sha, "r": opts["reason"], "b": opts["by"], "e": evidence},
            )
        self.stdout.write(f"quarantined {sha} — {opts['reason']!r} by {opts['by']}")

    def _resolve(self, engine, sha_arg: str) -> str:
        if _SHA_RE.match(sha_arg):
            return sha_arg
        with engine.connect() as conn:
            rows = conn.execute(
                sa_text(
                    "SELECT content_sha256 FROM lava_corpus.corpus "
                    "WHERE content_sha256 LIKE :p LIMIT 5"
                ),
                {"p": f"{sha_arg}%"},
            ).fetchall()
        keys = [r[0] for r in rows]
        if len(keys) == 1:
            return keys[0]
        if not keys:
            self.stderr.write(f"no corpus doc matches prefix {sha_arg!r}")
        else:
            self.stderr.write(f"prefix {sha_arg!r} matched {len(keys)} docs — pass a full sha")
        raise SystemExit(1)

    def _list(self, engine) -> None:
        with engine.connect() as conn:
            rows = conn.execute(
                sa_text(
                    "SELECT content_sha256, reason, quarantined_at, quarantined_by "
                    "FROM lava_parse.parse_blocklist ORDER BY quarantined_at DESC"
                )
            ).fetchall()
        if not rows:
            self.stdout.write("parse_blocklist is empty")
            return
        for r in rows:
            self.stdout.write(f"{r[0][:16]}…  {r[2]}  by={r[3]}  {r[1]}")
        self.stdout.write(f"\n{len(rows)} quarantined doc(s)")

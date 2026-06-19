"""Durable review backend for the metric review viewer (stdlib only).

Mounted behind nginx at /p20/api/ -> 127.0.0.1:8770. Stores the operator's verdicts in
SQLite (durable on disk), keeps an append-only audit log (no silent overwrite), and
serves the aggregated CSV joining the static review-data.json with the saved verdicts.

Endpoints (paths as seen after nginx strips /p20/api/):
  GET  /health      -> ok
  GET  /state       -> {id: {operator_*..., status, updated_at}}   (restore on load)
  POST /save        -> upsert one record {id, operator_verdict, operator_stage,
                       operator_mode, operator_notes, actual_location, status}
  GET  /csv         -> aggregated CSV download (gate + claude + operator side by side)
"""
import csv
import io
import json
import sqlite3
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = "/home/ubuntu/research/locard/operations/metric-review"
DATA = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/review-data.json"
STORY_DATA = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/story-review-data.json"
SLOT_DATA = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/slot-review-data.json"
NEW_DATA = "/home/ubuntu/research/locard/spikes/0064/eval_set/vision/new-review-data.json"
DB = BASE + "/review.db"
PORT = 8770
FIELDS = ["operator_verdict", "operator_stage", "operator_mode", "operator_notes", "actual_location", "status"]
SLOT_FIELDS = FIELDS  # slot grade store mirrors the same grading schema (separate table)
_lock = threading.Lock()


def db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS reviews(
        id TEXT PRIMARY KEY, operator_verdict TEXT, operator_stage TEXT, operator_mode TEXT,
        operator_notes TEXT, actual_location TEXT, status TEXT, updated_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS audit(
        n INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT, payload TEXT, ts TEXT)""")
    # SEPARATE grade store for the slot pipeline — never touches `reviews`
    c.execute("""CREATE TABLE IF NOT EXISTS slot_reviews(
        id TEXT PRIMARY KEY, operator_verdict TEXT, operator_stage TEXT, operator_mode TEXT,
        operator_notes TEXT, actual_location TEXT, status TEXT, updated_at TEXT)""")
    # SEPARATE grade store for the NEW-prompt review set — never touches `reviews`
    c.execute("""CREATE TABLE IF NOT EXISTS new_reviews(
        id TEXT PRIMARY KEY, operator_verdict TEXT, operator_stage TEXT, operator_mode TEXT,
        operator_notes TEXT, actual_location TEXT, status TEXT, updated_at TEXT)""")
    return c


def load_static():
    static = {r["id"]: r for r in json.load(open(DATA))}
    try:  # story review shares this backend; its ids are valid too
        for r in json.load(open(STORY_DATA)):
            static[r["id"]] = r
    except FileNotFoundError:
        pass
    return static


STATIC = load_static()
try:
    SLOT_IDS = {r["id"] for r in json.load(open(SLOT_DATA))}
except FileNotFoundError:
    SLOT_IDS = set()
try:
    NEW_IDS = {r["id"] for r in json.load(open(NEW_DATA))}
except FileNotFoundError:
    NEW_IDS = set()


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        if ctype.startswith("text/csv"):
            self.send_header("Content-Disposition", 'attachment; filename="metric-review.csv"')
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path.split("?")[0].rstrip("/")
        if p in ("", "/health"):
            return self._send(200, json.dumps({"ok": True, "records": len(STATIC)}))
        if p == "/slot-state":
            with _lock, db() as c:
                rows = c.execute("SELECT id," + ",".join(SLOT_FIELDS) + ",updated_at FROM slot_reviews").fetchall()
            cols = SLOT_FIELDS + ["updated_at"]
            return self._send(200, json.dumps({r[0]: dict(zip(cols, r[1:])) for r in rows}))
        if p == "/state":
            with _lock, db() as c:
                rows = c.execute("SELECT id," + ",".join(FIELDS) + ",updated_at FROM reviews").fetchall()
            cols = ["id"] + FIELDS + ["updated_at"]
            return self._send(200, json.dumps({r[0]: dict(zip(cols[1:], r[1:])) for r in rows}))
        if p == "/new-state":
            with _lock, db() as c:
                rows = c.execute("SELECT id," + ",".join(FIELDS) + ",updated_at FROM new_reviews").fetchall()
            cols = ["id"] + FIELDS + ["updated_at"]
            return self._send(200, json.dumps({r[0]: dict(zip(cols[1:], r[1:])) for r in rows}))
        if p == "/csv":
            return self._csv()
        return self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        p = self.path.split("?")[0].rstrip("/")
        if p == "/slot-save":
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n) or b"{}")
            except Exception as e:
                return self._send(400, json.dumps({"error": str(e)}))
            rid = payload.get("id")
            if not rid or rid not in SLOT_IDS:
                return self._send(400, json.dumps({"error": "bad slot id"}))
            ts = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            vals = [payload.get(f, "") or "" for f in SLOT_FIELDS]
            with _lock, db() as c:
                c.execute("INSERT INTO slot_reviews(id," + ",".join(SLOT_FIELDS) + ",updated_at) VALUES("
                          + ",".join(["?"] * (len(SLOT_FIELDS) + 2)) + ") ON CONFLICT(id) DO UPDATE SET "
                          + ",".join(f"{f}=excluded.{f}" for f in SLOT_FIELDS) + ",updated_at=excluded.updated_at",
                          [rid] + vals + [ts])
                c.commit()
            return self._send(200, json.dumps({"ok": True, "id": rid, "updated_at": ts}))
        if p == "/new-save":
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n) or b"{}")
            except Exception as e:
                return self._send(400, json.dumps({"error": str(e)}))
            rid = payload.get("id")
            if not rid or rid not in NEW_IDS:
                return self._send(400, json.dumps({"error": "bad new id"}))
            ts = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            vals = [payload.get(f, "") or "" for f in FIELDS]
            with _lock, db() as c:
                c.execute("INSERT INTO new_reviews(id," + ",".join(FIELDS) + ",updated_at) VALUES("
                          + ",".join(["?"] * (len(FIELDS) + 2)) + ") ON CONFLICT(id) DO UPDATE SET "
                          + ",".join(f"{f}=excluded.{f}" for f in FIELDS) + ",updated_at=excluded.updated_at",
                          [rid] + vals + [ts])
                c.commit()
            return self._send(200, json.dumps({"ok": True, "id": rid, "updated_at": ts}))
        if p != "/save":
            return self._send(404, json.dumps({"error": "not found"}))
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._send(400, json.dumps({"error": str(e)}))
        rid = payload.get("id")
        if not rid or rid not in STATIC:
            return self._send(400, json.dumps({"error": "bad id"}))
        ts = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        vals = [payload.get(f, "") or "" for f in FIELDS]
        with _lock, db() as c:
            c.execute("INSERT INTO reviews(id," + ",".join(FIELDS) + ",updated_at) VALUES(" + ",".join(["?"] * (len(FIELDS) + 2)) + ")"
                      " ON CONFLICT(id) DO UPDATE SET " + ",".join(f"{f}=excluded.{f}" for f in FIELDS) + ",updated_at=excluded.updated_at",
                      [rid] + vals + [ts])
            c.execute("INSERT INTO audit(id,payload,ts) VALUES(?,?,?)", [rid, json.dumps(payload), ts])
            c.commit()
        return self._send(200, json.dumps({"ok": True, "id": rid, "updated_at": ts}))

    def _csv(self):
        with _lock, db() as c:
            saved = {r[0]: r for r in c.execute("SELECT id," + ",".join(FIELDS) + ",updated_at FROM reviews").fetchall()}
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["set", "sha8", "metric_id", "org", "statement", "value", "subject",
                    "gate_decision", "gate_reason", "cited_value_page", "cited_subject_page", "found_value_pages",
                    "claude_verdict", "claude_stage", "claude_mode", "claude_note",
                    "operator_verdict", "operator_stage", "operator_mode", "operator_notes", "actual_location",
                    "status", "updated_at"])
        for rid, r in STATIC.items():
            s = saved.get(rid)
            op = dict(zip(FIELDS + ["updated_at"], s[1:])) if s else {}
            cl = r.get("claude", {})
            w.writerow([r["set"], r["sha8"], rid, r["org"], r["statement"], r["value"], r["subject"],
                        r["gate_decision"], r["gate_reason"], r.get("v_page"), r.get("s_page"),
                        "|".join(str(f["page"]) for f in r.get("found", []) if f.get("page")),
                        cl.get("verdict"), cl.get("stage"), cl.get("mode"), cl.get("note"),
                        op.get("operator_verdict", ""), op.get("operator_stage", ""), op.get("operator_mode", ""),
                        op.get("operator_notes", ""), op.get("actual_location", ""),
                        op.get("status", ""), op.get("updated_at", "")])
        self._send(200, out.getvalue(), "text/csv; charset=utf-8")


if __name__ == "__main__":
    print(f"review backend on 127.0.0.1:{PORT}  ({len(STATIC)} records, db={DB})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()

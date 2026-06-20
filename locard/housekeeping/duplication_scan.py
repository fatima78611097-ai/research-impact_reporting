"""Duplication scan — flag files that may do the SAME JOB in DIFFERENT code.

Companion to import_graph.py. import_graph finds DEAD code (nothing imports a file);
this finds LIVE DUPLICATION (two reachable files implementing the same thing) — the blind
spot that hid the two parallel gates (slot_render vs gate_policy) from the dead-code sweep.

Two mechanical signals (NOT a verdict — narrows the search, a human confirms):
  1. file PAIRS sharing >=3 distinctive function/class names
  2. filename FAMILIES in one dir (x.py / x_policy.py / x_v2.py / async_x.py)

Dynamic dispatch, wrappers, and entry+library splits LOOK like duplication here but aren't —
every hit must be read before calling it a duplicate.  Usage:  python3 duplication_scan.py
"""
import ast
import os
import re
from collections import defaultdict
from itertools import combinations

ROOT = "/home/ubuntu/research"
SRC = "lavandula"
EXCLUDE = ("/tests/", "/migrations/", "/__pycache__/", "/templatetags/", "/.builders/")
COMMON = {"main", "run", "setup", "handle", "parse", "get", "post", "save", "load", "to_dict",
          "from_dict", "decide", "build", "process", "close", "validate", "execute"}
SUFFIX = re.compile(r"_(policy|runner|v\d+|check|render|resolve|resolver|batch|async|new|old|2|3|client|clients)$")


def iter_py(base):
    for dp, dn, fn in os.walk(os.path.join(ROOT, base)):
        if any(x in dp + "/" for x in EXCLUDE):
            dn[:] = []
            continue
        for f in fn:
            if f.endswith(".py") and not f.startswith("test_"):
                yield os.path.join(dp, f)


def names_of(path):
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception:
        return set()
    return {n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


files = {p: names_of(p) for p in iter_py(SRC)}

print("=== PAIRS sharing >=3 distinctive function/class names (possible parallel impls) ===")
pairs = []
for (a, na), (b, nb) in combinations(files.items(), 2):
    shared = {s for s in (na & nb) - COMMON if not s.startswith("_") and len(s) > 3}
    if len(shared) >= 3:
        pairs.append((len(shared), a, b, shared))
for n, a, b, sh in sorted(pairs, reverse=True):
    print(f"  [{n}] {os.path.relpath(a, ROOT)}")
    print(f"      {os.path.relpath(b, ROOT)}")
    print(f"      shared: {sorted(sh)[:8]}")
if not pairs:
    print("  (none)")

print("\n=== FILENAME FAMILIES (same dir, shared stem) ===")
bydir = defaultdict(list)
for p in files:
    bydir[os.path.dirname(p)].append(os.path.basename(p)[:-3])
found = False
for d, stems in sorted(bydir.items()):
    fam = defaultdict(list)
    for s in stems:
        root = SUFFIX.sub("", s)
        root = re.sub(r"^async_", "", root)
        fam[root].append(s)
    for root, members in fam.items():
        if len(members) >= 2 and root:
            print(f"  {os.path.relpath(d, ROOT)}/: {root}* -> {sorted(members)}")
            found = True
if not found:
    print("  (none)")

print("\nNOTE: hits are CANDIDATES. Read each — dispatcher+backend, wrapper, and entry+library"
      "\nsplits look identical to duplication here. See operations/duplication-audit.md for the"
      "\nverified verdicts from the 2026-06-20 pass.")

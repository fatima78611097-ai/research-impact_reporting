"""Import-graph / reachability analysis for the lavandula codebase (housekeeping).

Builds the internal import graph, computes in-degree for every lavandula module from
ALL repo sources (lavandula + locard + experiments), and flags modules unreachable
from the live entry points (stage targets, management commands, __main__ scripts,
Django-loaded modules) as DEAD CANDIDATES.

NOT a verdict: dynamic imports, `python -m` invocations, and Django implicit loading
mean candidates MUST be verified before deletion. This narrows the search; it doesn't
end it.  Usage:  python3 locard/housekeeping/import_graph.py [watch_module ...]
"""
import ast
import os
import sys

ROOT = "/home/ubuntu/research"
EXCLUDE = ("/venv/", "/__pycache__/", "/.builders/", "/node_modules/", "/site-packages/")
SRC_DIRS = ("lavandula", "locard", "experiments")
STAGE_TARGETS = {
    "lavandula.nonprofits.tools.seed_enumerate", "lavandula.nonprofits.tools.pipeline_resolve",
    "lavandula.nonprofits.tools.pipeline_classify", "lavandula.reports.crawler",
    "lavandula.nonprofits.tools.pipeline_enrich_phone",
}
DJANGO_LOADED = ("views.py", "urls.py", "models.py", "apps.py", "admin.py", "wsgi.py",
                 "asgi.py", "settings.py", "context_processors.py", "routers.py", "forms.py",
                 "param_validators.py", "scheduler_config.py")


def iter_py(base):
    for dp, dn, fn in os.walk(os.path.join(ROOT, base)):
        if any(x in dp + "/" for x in EXCLUDE):
            dn[:] = []
            continue
        for f in fn:
            if f.endswith(".py"):
                yield os.path.join(dp, f)


def mod_of(path):
    return os.path.relpath(path, ROOT)[:-3].replace("/", ".")


# universe of modules we ASSESS = lavandula only
lava_files = list(iter_py("lavandula"))
mods = {mod_of(p): p for p in lava_files}

# edges from ALL sources (so a lavandula module used only by a locard script isn't "dead")
all_files = [p for d in SRC_DIRS for p in iter_py(d)]


ALIASES = {"pipeline": "lavandula.dashboard.pipeline", "dashboard": "lavandula.dashboard.dashboard"}

def _expand(t):
    for a, full in ALIASES.items():
        if t == a or t.startswith(a + "."):
            return full + t[len(a):]
    return t


def resolve_rel(modname, level, sub):
    parts = modname.split(".")
    base = parts[:-level] if level <= len(parts) else []
    if sub:
        base = base + sub.split(".")
    return ".".join(base)


indeg = {m: 0 for m in mods}
importers = {m: set() for m in mods}
for p in all_files:
    m = mod_of(p)
    try:
        tree = ast.parse(open(p, encoding="utf-8").read())
    except Exception:
        continue
    targets = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            tgt = resolve_rel(m, node.level, node.module) if node.level else (node.module or "")
            targets.add(tgt)
            for n in node.names:
                targets.add((tgt + "." + n.name) if tgt else n.name)
        elif isinstance(node, ast.Import):
            for n in node.names:
                targets.add(n.name)
    for t0 in targets:
        for t in {t0, _expand(t0)}:
            if t in mods and t != m:
                indeg[t] += 1
                importers[t].add(m)



def _framework_or_test(p):
    return ("/tests/" in p or "/migrations/" in p or "/templatetags/" in p
            or os.path.basename(p).startswith("test_") or p.endswith("conftest.py")
            or p.endswith(("middleware.py", "gunicorn.conf.py", "asgi.py"))
            or "/pg_iam_backend/" in p)


def is_entry(m, p):
    if "/management/commands/" in p:
        return True
    if p.endswith(DJANGO_LOADED):
        return True
    if m.endswith("__init__"):
        return True
    if m in STAGE_TARGETS:
        return True
    try:
        if "__main__" in open(p, encoding="utf-8").read():
            return True
    except Exception:
        pass
    return False


dead = [(m, p) for m, p in sorted(mods.items()) if indeg[m] == 0 and not is_entry(m, p) and not _framework_or_test(p)]
print(f"lavandula modules assessed: {len(mods)}  |  import sources scanned: {len(all_files)} files")
print(f"DEAD CANDIDATES (0 in-degree, not entry/stage/django/__init__/__main__): {len(dead)}\n")
for m, p in dead:
    print(f"  {os.path.relpath(p, ROOT)}")

if len(sys.argv) > 1:
    print("\n=== watchlist: who imports these ===")
    for w in sys.argv[1:]:
        hits = [k for k in mods if k.endswith("." + w) or k == w]
        for k in hits:
            print(f"  {k}  (in-degree {indeg[k]})  <- {sorted(importers[k]) or 'NOBODY'}")

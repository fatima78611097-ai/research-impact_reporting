# Spec 0058 Phase-0 Spike — RESULTS (TEMPLATE — operator fills after the GPU run)

> **Status:** ⏳ NOT YET RUN — this is the template the operator completes on a g6
> instance. Its verdict **gates Phase 2**: it decides whether the builder ships
> the native `document_timeout` path (§3.2) or the subprocess + `RLIMIT_AS` path
> (§3.3). Until this file carries a verdict, Phase 2 stays unbuilt.

- **Instance / GPU:** _e.g. g6.2xlarge, L4 24 GB_ — fill in
- **Docling version:** _from `spike_results.json.docling_version`_ — expect `2.93.0`
- **Run command:** `python spike_timeout.py --recovery <good-sha> --timeout 60 --runs 3`
- **Raw measurements:** `spike_results.json` (machine-readable; this file is the human verdict)
- **Date / operator:** _fill in_

---

## 1. HANG verdict — does native `document_timeout` bound the poison doc?

Poison doc: `e038a9e75317ff86` (5.6 MB / 4 pg Illustrator, 19-char text layer).
`document_timeout` T = **___ s**, runs = **___** (≥ 3 required).

| Run | returned/raised | wall (s) | cpu (s) | exception type | within T+30s? | GPU peak (MiB) |
|-----|-----------------|----------|---------|----------------|---------------|----------------|
| 1   |                 |          |         |                |               |                |
| 2   |                 |          |         |                |               |                |
| 3   |                 |          |         |                |               |                |

**Recovery probe** (a normal text-native doc parsed immediately after each timeout —
proves GPU context survived; §3.0 criterion 2):

| After poison run | recovery returned | wall (s) | pages | tables |
|------------------|-------------------|----------|-------|--------|
| 1                |                   |          |       |        |
| 2                |                   |          |       |        |
| 3                |                   |          |       |        |

Thread count: start = ___, end = ___ (a leak of > 2 daemon threads = FAIL signal).

### §3.0 PASS criteria — check all:
- [ ] On the poison doc, `convert()` returns/raises within **T + 30 s** across **≥ 3** runs (deterministic, not one lucky run).
- [ ] After timeout, the worker is healthy: it parses a normal doc to completion (GPU context intact, no memory that breaks the next convert).
- [ ] No orphaned threads / processes left consuming GPU after the timeout.

A **late exception** (fires, but well past T) counts as **FAIL** — partial bounding is not bounding.

**HANG VERDICT:** ☐ PASS (native bounds the hang)  ☐ FAIL (→ subprocess §3.3)

---

## 2. MEMORY verdict — is peak host + GPU memory provably bounded?

`document_timeout` does **not** bound memory. A Flate-decompression bomb (small
compressed stream → huge decoded allocation) expands during PDF stream decode,
**before** the page/image caps apply — so the absolute caps do **not** prevent it.
Only an OS memory cap (`RLIMIT_AS`) in a child process does.

| Doc | role | file size (MiB) | wall (s) | peak RSS (MiB) | peak GPU (MiB) |
|-----|------|-----------------|----------|----------------|----------------|
| `e038a9e75317ff86` | poison |        |          |                |                |
| `de8fecc5…`        | 646s slow |     |          |                |                |
| `658d9b89…`        | 46.9 MB/page |  |          |                |                |

- Instance available RAM: ___ GiB. GPU total: ___ MiB.
- Max peak RSS observed: ___ MiB. Max peak GPU observed: ___ MiB.
- Headroom: peak RSS / available RAM = ___% ; peak GPU / GPU total = ___%.

### MEMORY criteria:
- [ ] Peak host + GPU memory on **all** pathological samples stays **comfortably**
      under the instance limit (with margin for the warm Docling model).
- [ ] No OOM / process death occurred during the run.

**MEMORY VERDICT:** ☐ BOUNDED (headroom proven)  ☐ AT RISK (→ subprocess §3.3 forced)

---

## 3. Nature of `document_timeout` (record for the plan — §3.0 / Codex red-team)

Fill from observation + a quick read of the docling source:

- **Units:** ☐ seconds ☐ ms — (confirms the spec open question; compare `wall_seconds`).
- **Wall-clock vs CPU-time:** ___ (compare `wall_seconds` vs `cpu_seconds` per run).
- **Raises vs partial result:** exact exception class = `__________`; or returns a partial `DoclingDocument`?
- **Interrupts native/CUDA code, or only checks between pipeline stages:** ___
- **Cleanup synchronous (GPU memory returned before the next doc):** ☐ yes ☐ no — evidence: ___

---

## 4. TWO-FACTOR DECISION (this is the gate)

Per the plan, Phase 2 branches on **both** factors — hang alone is not enough:

| HANG | MEMORY | → Phase 2 path |
|------|--------|----------------|
| PASS | BOUNDED | **§3.2 native `document_timeout`** — minimal code, set the option in the Phase-1 builder. |
| PASS | AT RISK | **§3.3 subprocess + RLIMIT_AS** — memory forces it even though the hang is bounded. |
| FAIL | (either) | **§3.3 subprocess + RLIMIT_AS** — the native timeout can't break the hang. |

> **Conservative default for the national-scale run:** unless this spike proves
> memory is tightly bounded AND the hang is reliably caught, choose **subprocess
> (§3.3)**. The native path is acceptable only for a bounded/known corpus where
> the spike proves headroom.

### ✅ FINAL VERDICT

**Phase 2 builds: ☐ §3.2 native  ☐ §3.3 subprocess**

**Rationale (2–3 sentences, cite the numbers above):**

_____________________________________________________________________

**Default timeout to ship (`PARSE_TIMEOUT_SECONDS`):** ___ s
(180 s proposed in the spec — tune vs the parse-duration tail p99≈69.5 s and the
0056 5-min heartbeat; T used in this spike was for the measurement, not the ship value.)

---

## 5. Operator → builder handoff

After completing the verdict above, notify the builder with the path:
the verdict + the chosen `PARSE_TIMEOUT_SECONDS` is all Phase 2 needs to proceed.
The spike code (`spike_timeout.py`) is throwaway and is **not** merged into the package.

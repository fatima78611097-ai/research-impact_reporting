# Spec 0058 — Phase-0 Spike RESULTS

**Status:** ✅ COMPLETE — verdict below gates Phase 2.
**Run:** 2026-05-31, on-demand g6.2xlarge (NVIDIA L4, 23034 MiB), Docling 2.93.0. Operator-driven via SSM, per-doc isolated (each convert in its own process so a crash on one doc doesn't block the others).

## Measurements

| Doc | sha | size / pages | Result | Wall | Peak RSS | Peak GPU |
|---|---|---|---|---|---|---|
| recovery (known-good) | 08815732… | 1.0 MB / 2pg | **OK** | 5.07s | 2158 MiB | 1128 MiB |
| **poison** | e038a9e7… | 5.9 MB / 4pg (Illustrator) | **SEGFAULT (exit 139)** | — | — | — |
| extreme (646s corpus doc) | de8fecc5… | 12.8 MB / 24pg | OK | **75.5s** | 2451 MiB | 1450 MiB |
| extreme (46.9 MB/page) | 658d9b89… | 49.2 MB / 1pg | OK | 25.9s | **5209 MiB** | 2048 MiB |
| recovery after each heavy doc | 08815732… | — | OK | ~2.0s | — | — |

dmesg (reproduced twice): `python segfault … in pdf_parsers.cpython-310-x86_64-linux-gnu.so`

## §3.0 two-factor gate

**FACTOR 1 — HANG / native `document_timeout`: FAIL.**
- The **poison doc segfaults Docling's native `pdf_parsers` C extension (exit 139), reproducibly.** A native segfault crashes the whole process at the C level — `document_timeout`, Python exceptions, and signals **cannot** catch it.
- `document_timeout` is **not a hard wall-clock interrupt**: the 24-page doc ran **75.5s wall under `document_timeout=60`** (overran T by 15s — checked between pipeline stages, not in real time). Even a genuine in-stage hang would not be broken by it.

**FACTOR 2 — MEMORY: bounded for these docs, but image-driven.**
- Peak RSS 5.2 GB (49 MB single-page doc), peak GPU 2.0 GB — well under the L4's 23 GB and the g6.2xlarge's 32 GB RAM. No OOM on this sample.
- Memory scales with embedded-image size (a 1-page 49 MB doc hit 5.2 GB), so a larger/crafted doc (Flate bomb) is not bounded in-process.

**Recovery:** the known-good doc parsed cleanly (~2s) after every *completed* heavy doc → a completed heavy doc does not corrupt GPU state. (Moot for the segfault case — that kills the process outright.)

## VERDICT → build §3.3 SUBPROCESS ISOLATION (native timeout path REJECTED)

The decisive fact is the **native segfault in `pdf_parsers.so`**, not memory or hang. No in-process mechanism (native `document_timeout`, signal, exception) can contain a C-level segfault — it takes the whole worker down. That is the run-32 wedge, and worse: in run-32 the 0056 heartbeat caught a *hang* after 5 min; a segfault kills the worker instantly and the doc re-poisons on relaunch.

**Only the subprocess design contains all three failure modes:**
- **Segfault** → child dies, parent sees non-zero exit (139) → record `parse_crash`, worker survives.
- **Hang** → parent `join(timeout)` → `terminate()`/`kill()`.
- **OOM / image bomb** → child `RLIMIT_AS` kills it → `parse_oom`.

Stronger conclusion than the spec anticipated (it weighed hang + OOM; the real worst case is a native crash) — and it removes all ambiguity: **subprocess is mandatory regardless of memory headroom.**

## Chosen config
- **`PARSE_TIMEOUT_SECONDS = 180`** — headroom over the observed 75.5s and the corpus p99 of 69.5s; Phase-1's `images_scale` cap + TableFormer FAST should pull slow docs down further. (This doc took 646s in the run-10 corpus parse vs 75s here — likely heavier default settings then; cap + FAST should keep it well under 180s. A genuinely slower doc becomes a clean `parse_timeout`, not a wedge.)
- **Child `RLIMIT_AS` ≈ 12–16 GB** — above the 5.2 GB observed peak + warm model, below the 32 GB host so a runaway is killed before host OOM. Builder tunes.
- **Quarantine `e038a9e75317ff86`** now — it segfaults Docling (`parse_blocklist`).

## Spike-script note (for the builder)
The spike's `GpuMemPoller` had a bug: an attribute named `_stop` shadowed `threading.Thread._stop`, breaking `join()` (`TypeError: 'Event' object is not callable`). I fixed it in-place (`_stop` → `_stop_evt`) to get clean measurements. Throwaway spike code — no further action. The segfault finding is independent of this bug (the good doc's convert succeeded; only the poison doc's native parse crashed).

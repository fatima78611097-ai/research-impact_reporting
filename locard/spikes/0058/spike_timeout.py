#!/usr/bin/env python3
"""Spec 0058 Phase-0 SPIKE — throwaway. Run on a g6 GPU instance ONLY.

Answers the two questions that gate the Phase-2 architecture:

  1. HANG  — does Docling 2.93's native ``PdfPipelineOptions.document_timeout``
             actually interrupt a hard hang (the e038a9e75317ff86 poison doc),
             returning control within ``T + 30s`` across >= 3 runs, with the
             worker process healthy enough to parse a normal doc immediately
             after (GPU context intact, no leaked threads / GPU memory)?

  2. MEMORY — what is the PEAK host (RSS) and GPU memory on the poison doc and
             the §1.3 extreme docs (46-49 MB/page Illustrator/Photoshop)? A
             native timeout does NOT bound memory; if peak approaches the
             instance limit, the subprocess + RLIMIT_AS path (§3.3) is forced
             regardless of the hang verdict.

This is THROWAWAY code (no tests, not shipped). Its only product is the
measurements written to ``spike_results.json`` and the human-authored verdict
in ``RESULTS.md``. It deliberately runs ``convert()`` IN-PROCESS so it tests the
real native mechanism the worker would use — do NOT wrap it in a subprocess here
(that would test the §3.3 path, not the native one).

Usage (on the GPU box, in the venv that has docling 2.93.0):

    python spike_timeout.py \
        --poison e038a9e75317ff86 \
        --extreme de8fecc5 --extreme 658d9b89 \
        --recovery <a-known-good-text-native-sha> \
        --timeout 60 --runs 3 \
        --out spike_results.json

SHAs may be given as full 64-char sha256 or as a prefix — the script resolves a
prefix to the full S3 key via ``list_objects_v2(Prefix=pdfs/<prefix>)``. The S3
bucket/prefix mirror the worker (lavandula.parse.config).
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

# S3 layout — keep in sync with lavandula.parse.config (duplicated here so the
# spike has zero import coupling to the package under test).
S3_BUCKET = "lavandula-nonprofit-collaterals"
S3_PREFIX = "pdfs/"

# Known §1.3 examples (prefixes from the spec). Operator may override via CLI.
DEFAULT_POISON = "e038a9e75317ff86"   # 5.6MB / 4pg Illustrator, 19-char text layer
DEFAULT_EXTREMES = ["de8fecc5", "658d9b89"]  # 646s slow-doc; 46.9 MB/page


# ---------------------------------------------------------------------------
# Memory sampling
# ---------------------------------------------------------------------------
class GpuMemPoller(threading.Thread):
    """Poll ``nvidia-smi`` for used GPU memory; track the peak (MiB).

    Best-effort: if nvidia-smi is missing or errors, peak stays None and the
    spike still produces host-memory + timing data.
    """

    def __init__(self, interval: float = 0.25):
        super().__init__(daemon=True, name="gpu-mem-poller")
        self._interval = interval
        self._stop = threading.Event()
        self.peak_used_mib: int | None = None
        self.total_mib: int | None = None

    def _sample(self) -> None:
        try:
            out = subprocess.run(
                ["nvidia-smi",
                 "--query-gpu=memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
        except Exception:
            return
        line = (out.stdout or "").strip().splitlines()
        if not line:
            return
        try:
            used, total = (int(x.strip()) for x in line[0].split(","))
        except Exception:
            return
        self.total_mib = total
        if self.peak_used_mib is None or used > self.peak_used_mib:
            self.peak_used_mib = used

    def run(self) -> None:
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(self._interval)

    def stop_and_read(self) -> dict:
        self._stop.set()
        self.join(timeout=2.0)
        self._sample()  # one final reading
        return {"peak_gpu_used_mib": self.peak_used_mib, "gpu_total_mib": self.total_mib}


def _vmhwm_kib() -> int | None:
    """Peak resident set size (VmHWM) of this process, in KiB, from /proc."""
    try:
        with open("/proc/self/status", "r") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1])
    except Exception:
        pass
    return None


def _maxrss_mib() -> float:
    """Peak RSS high-water mark via getrusage (KiB on Linux) -> MiB."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------
def resolve_key(s3, sha_or_prefix: str) -> str | None:
    """Return the full S3 key for a sha or sha-prefix, or None if not found."""
    if len(sha_or_prefix) == 64:
        return f"{S3_PREFIX}{sha_or_prefix}.pdf"
    resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=f"{S3_PREFIX}{sha_or_prefix}", MaxKeys=5)
    contents = resp.get("Contents", [])
    keys = [c["Key"] for c in contents if c["Key"].endswith(".pdf")]
    if len(keys) == 1:
        return keys[0]
    if len(keys) > 1:
        print(f"  !! prefix {sha_or_prefix!r} matched {len(keys)} keys; pass a full sha", file=sys.stderr)
    return None


def download(s3, key: str, dest: Path) -> bool:
    try:
        s3.download_file(S3_BUCKET, key, str(dest))
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  !! download failed for {key}: {exc}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Converter
# ---------------------------------------------------------------------------
def build_converter(document_timeout: float | None):
    """Build a DocumentConverter with the native document_timeout set.

    Mirrors the *minimal* configuration the native path (§3.2) would ship so the
    spike measures the real mechanism. Records nothing about FAST/images_scale —
    those are the A/B's job, not the timeout spike's.
    """
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    opts = PdfPipelineOptions()
    if document_timeout is not None:
        # NOTE: confirm units in the spike output (the spec open question).
        # As of docling 2.x this is seconds (float).
        opts.document_timeout = document_timeout
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


def run_convert_once(converter, pdf_path: Path, timeout_t: float, gpu_poll: bool) -> dict:
    """Run a single convert(), instrumented. Never raises — captures outcome."""
    poller = GpuMemPoller() if gpu_poll else None
    if poller:
        poller.start()
    hwm_before = _vmhwm_kib()
    t0 = time.monotonic()
    cpu0 = time.process_time()
    outcome = {
        "returned": None,        # "ok" | "raised"
        "exception_type": None,
        "exception_msg": None,
        "wall_seconds": None,
        "cpu_seconds": None,
        "within_T_plus_30": None,
        "n_pages": None,
        "n_tables": None,
    }
    try:
        result = converter.convert(str(pdf_path))
        doc = getattr(result, "document", None)
        outcome["returned"] = "ok"
        try:
            outcome["n_pages"] = len(getattr(doc, "pages", {}) or {})
            outcome["n_tables"] = len(getattr(doc, "tables", []) or [])
        except Exception:
            pass
    except BaseException as exc:  # noqa: BLE001 — capture EVERYTHING incl. timeout
        outcome["returned"] = "raised"
        outcome["exception_type"] = type(exc).__name__
        outcome["exception_msg"] = str(exc)[:300]
    finally:
        wall = time.monotonic() - t0
        outcome["wall_seconds"] = round(wall, 2)
        outcome["cpu_seconds"] = round(time.process_time() - cpu0, 2)
        outcome["within_T_plus_30"] = wall <= (timeout_t + 30.0)
    hwm_after = _vmhwm_kib()
    outcome["vmhwm_before_mib"] = round(hwm_before / 1024.0, 1) if hwm_before else None
    outcome["vmhwm_after_mib"] = round(hwm_after / 1024.0, 1) if hwm_after else None
    outcome["proc_maxrss_mib"] = round(_maxrss_mib(), 1)
    if poller:
        outcome.update(poller.stop_and_read())
    return outcome


def thread_count() -> int:
    try:
        return len(os.listdir("/proc/self/task"))
    except Exception:
        return -1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Spec 0058 Phase-0 timeout+memory spike")
    ap.add_argument("--poison", default=DEFAULT_POISON,
                    help="sha or prefix of the poison doc (default: e038a9e75317ff86)")
    ap.add_argument("--extreme", action="append", default=None,
                    help="sha/prefix of an extreme MB/page or slow doc (repeatable)")
    ap.add_argument("--recovery", default=None,
                    help="sha/prefix of a KNOWN-GOOD text-native doc, parsed after "
                         "each poison timeout to prove GPU state is intact")
    ap.add_argument("--timeout", type=float, default=60.0, help="document_timeout T seconds (default 60)")
    ap.add_argument("--runs", type=int, default=3, help="poison-doc repetitions (>=3 per §3.0)")
    ap.add_argument("--out", default="spike_results.json")
    ap.add_argument("--workdir", default="/tmp/spike-0058")
    args = ap.parse_args()

    import boto3
    s3 = boto3.client("s3")
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    extremes = args.extreme if args.extreme is not None else list(DEFAULT_EXTREMES)

    results: dict = {
        "config": {
            "timeout_T": args.timeout,
            "runs": args.runs,
            "poison": args.poison,
            "extremes": extremes,
            "recovery": args.recovery,
        },
        "docling_version": None,
        "thread_count_start": thread_count(),
        "poison_runs": [],
        "recovery_after_timeout": [],
        "extreme_docs": [],
        "notes": {
            # The operator/builder fills these qualitative facts from observation
            # + the docling source, per §3.0 "record the nature of document_timeout".
            "timeout_nature_wall_vs_cpu": "TODO: wall-clock or CPU-time? (compare wall vs cpu seconds below)",
            "exception_raised": "TODO: exact exception class (see poison_runs[].exception_type)",
            "interrupts_native_cuda_code": "TODO: did it break the C/CUDA hang, or only fire between pipeline stages?",
            "cleanup_synchronous": "TODO: did GPU memory return before next doc? (compare gpu peak across runs)",
        },
    }

    try:
        from importlib.metadata import version as _v
        results["docling_version"] = _v("docling")
    except Exception:
        results["docling_version"] = "unknown"

    # --- Resolve + download all docs up front ---
    def fetch(sha_or_prefix: str, label: str) -> Path | None:
        key = resolve_key(s3, sha_or_prefix)
        if key is None:
            print(f"[{label}] could not resolve S3 key for {sha_or_prefix!r}", file=sys.stderr)
            return None
        dest = workdir / Path(key).name
        if not dest.exists() and not download(s3, key, dest):
            return None
        print(f"[{label}] {sha_or_prefix} -> {key} ({dest.stat().st_size/1e6:.1f} MB)")
        return dest

    poison_path = fetch(args.poison, "poison")
    recovery_path = fetch(args.recovery, "recovery") if args.recovery else None
    extreme_paths = [(e, fetch(e, "extreme")) for e in extremes]

    converter = build_converter(document_timeout=args.timeout)

    # --- 1. HANG: poison doc, >=3 runs, with a recovery probe after each ---
    if poison_path is not None:
        for i in range(args.runs):
            print(f"\n=== poison run {i+1}/{args.runs} (T={args.timeout}s) ===")
            res = run_convert_once(converter, poison_path, args.timeout, gpu_poll=True)
            res["run_index"] = i
            res["thread_count_after"] = thread_count()
            results["poison_runs"].append(res)
            print(f"    -> {res['returned']} in {res['wall_seconds']}s "
                  f"(cpu {res['cpu_seconds']}s) exc={res['exception_type']} "
                  f"within_T+30={res['within_T_plus_30']} "
                  f"gpu_peak={res.get('peak_gpu_used_mib')}MiB")

            # criterion 2/3: a normal doc must parse immediately after the timeout
            if recovery_path is not None:
                print("    recovery probe (normal doc must parse clean) ...")
                rec = run_convert_once(converter, recovery_path, args.timeout, gpu_poll=True)
                rec["after_poison_run"] = i
                results["recovery_after_timeout"].append(rec)
                print(f"    -> recovery {rec['returned']} in {rec['wall_seconds']}s "
                      f"pages={rec['n_pages']} tables={rec['n_tables']}")
    else:
        print("!! poison doc unavailable — HANG verdict cannot be measured", file=sys.stderr)

    # --- 2. MEMORY: extreme docs, single run each, measure peak host+GPU ---
    for sha, path in extreme_paths:
        if path is None:
            results["extreme_docs"].append({"sha": sha, "error": "unavailable"})
            continue
        print(f"\n=== extreme doc {sha} ===")
        res = run_convert_once(converter, path, args.timeout, gpu_poll=True)
        res["sha"] = sha
        res["file_size_mib"] = round(path.stat().st_size / (1024 * 1024), 1)
        results["extreme_docs"].append(res)
        print(f"    -> {res['returned']} in {res['wall_seconds']}s "
              f"rss_peak={res['proc_maxrss_mib']}MiB gpu_peak={res.get('peak_gpu_used_mib')}MiB")

    results["thread_count_end"] = thread_count()

    # --- Derived PASS/FAIL hints (the human writes the final verdict in RESULTS.md) ---
    pr = results["poison_runs"]
    results["derived"] = {
        "all_poison_runs_returned": bool(pr) and all(r["returned"] is not None for r in pr),
        "all_within_T_plus_30": bool(pr) and all(r["within_T_plus_30"] for r in pr),
        "recovery_all_ok": bool(results["recovery_after_timeout"])
            and all(r["returned"] == "ok" for r in results["recovery_after_timeout"]),
        "thread_leak": (results["thread_count_end"] - results["thread_count_start"]) > 2,
        "max_gpu_peak_mib": max(
            [r.get("peak_gpu_used_mib") or 0 for r in (pr + results["extreme_docs"])] or [0]
        ),
        "max_rss_peak_mib": max(
            [r.get("proc_maxrss_mib") or 0 for r in (pr + results["extreme_docs"])] or [0]
        ),
    }

    out_path = Path(args.out)
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWROTE {out_path.resolve()}")
    print("\n--- DERIVED HINTS (fill the real verdict in RESULTS.md) ---")
    print(json.dumps(results["derived"], indent=2))
    print("\nReminder: HANG-pass alone is NOT enough — the §3.3 subprocess path is")
    print("forced if peak memory approaches the instance limit (two-factor gate).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(2)

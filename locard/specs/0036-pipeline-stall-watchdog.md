# Spec 0036: Pipeline Stall Watchdog

**Status**: Draft
**Priority**: High
**Dependencies**: None (applies to existing pipelines retroactively)

## Problem Statement

Multiple pipeline processes (crawler, extraction, reclassification) use producer-consumer architectures with bounded queues. When a downstream resource stalls (RDS, S3, LLM API), backpressure propagates through the queue chain until all workers block:

1. DB writer or S3 upload stalls
2. Download/processing workers block on `db_actor.enqueue()` or `archive.put()`
3. Bounded queue fills to capacity
4. Producer workers block on `queue.put()`
5. **All progress stops** — but the process stays alive, heartbeat keeps ticking, logs keep emitting

**Observed incidents (2026-05-08/09):**
- VA crawler: stalled 2+ hours at 38/2935 orgs (active=120, queue=1000, PDFs=56)
- WA crawler: stalled 5+ hours at 699/2137 orgs (active=120, queue=1000, PDFs=2321)
- Both required manual kill + restart to recover
- The ETA field in logs *appeared* to show degradation (2d→8d) but was actually frozen output divided by growing elapsed time

This will recur for every new pipeline (extract_classification_context, reclassify_corpus, future Docling extraction) unless we add generic stall detection.

## Goals

1. **Detect stalls** — identify when a pipeline has active workers but zero progress for a configurable duration
2. **React to stalls** — configurable response: log warning, attempt graceful drain, set shutdown event, or force restart
3. **Generic interface** — works with any pipeline that exposes a progress counter, not just the crawler
4. **Dashboard visibility** — stall events surface in the Job record (status, error_message, log_tail)
5. **No false positives** — legitimate slow periods (large PDFs, LLM retries) must not trigger the watchdog

## Non-Goals

- Root-cause fixing the backpressure chain (that's a deeper architectural change)
- Automatic restart without operator awareness (too risky for production data pipelines)
- Monitoring external service health (RDS, S3, DeepSeek) — the watchdog detects symptoms, not causes
- Prometheus/metrics integration — observability is via logs and dashboard Job records

## Design

### Core Abstraction: `StallWatchdog`

A lightweight monitor that periodically checks whether a progress counter has advanced.

```python
class StallWatchdog:
    def __init__(
        self,
        get_progress: Callable[[], int],   # returns current progress counter
        get_active: Callable[[], int],      # returns number of active workers (0 = idle)
        stall_threshold_sec: int = 300,     # 5 min with no progress = stall
        check_interval_sec: int = 60,       # check every 60s
        on_stall: Callable[[StallInfo], StallAction] | None = None,
        pre_abort: Callable[[], None] | None = None,  # cleanup hook before os._exit(2)
        notify_email: str | None = None,    # SES recipient for stall alerts
        progress_total: int | None = None,  # total expected, if known
        job_id: int | None = None,          # dashboard Job ID for status updates
        clock: Callable[[], float] | None = None,  # injectable clock (default: time.monotonic)
    ): ...
```

When `job_id` is provided, the constructor eagerly validates that Django models can be imported (catches `ImportError` at init rather than at first stall). The actual DB connection is deferred to the first dashboard update.

**Detection logic** (evaluated every `check_interval_sec`):
1. Read `progress = get_progress()` and `active = get_active()`
2. If `progress > last_progress` → reset stall timer and `stall_checks` counter, record `last_progress = progress`
3. If `progress == last_progress` and `active > 0` and `stall_timer >= stall_threshold_sec` → **stall detected**, increment `stall_checks`
4. If `active == 0` → not a stall, just idle (waiting for work or draining)
5. If `progress < last_progress` → **counter regression** (caller bug, stats reset, or retry decrement). Log `ERROR "watchdog: progress counter regressed from {last_progress} to {progress}, resetting baseline"`. Reset `last_progress = progress` and reset stall timer. Do NOT treat this as progress — the stall timer starts fresh from the new baseline.

**Exception safety**: The watchdog's main loop wraps each iteration in `try/except Exception`. Any uncaught `Exception` — from `get_progress()`, `get_active()`, `on_stall()`, dashboard updates, or the watchdog's own logic — is logged at CRITICAL with traceback, and the loop continues to the next check interval. The watchdog must never crash the pipeline it monitors.

**Unexpected termination**: The `try/except Exception` does not catch `BaseException` subclasses. The conditions that can terminate the loop unexpectedly are:
- `KeyboardInterrupt` (SIGINT) — caught by outer `try/except BaseException` around the while loop
- `asyncio.CancelledError` (async mode, task cancelled) — same outer handler
- Thread killed by `os._exit()` from another thread — no handler possible (process dies)

The outer `try/except BaseException` handler logs `CRITICAL "watchdog terminated unexpectedly: {exc}"` and makes a best-effort Job `error_message` update if `job_id` is set. This is tested by injecting a `KeyboardInterrupt` via the mock clock.

**StallInfo** passed to the callback:
```python
@dataclass
class StallInfo:
    stall_duration_sec: float       # how long progress has been frozen
    last_progress: int              # the stuck counter value
    active_workers: int             # current active worker count
    progress_total: int | None      # total expected, if known
    stall_checks: int               # number of check intervals during this uninterrupted stall
    escalation_level: int           # current level in the default ladder (1-4), 0 if custom callback
```

`stall_checks` is the number of consecutive check intervals where progress was stuck and `active > 0`. It resets to 0 when progress advances. The default escalation ladder uses `stall_duration_sec` (not `stall_checks`) to determine the level, so it works correctly regardless of `check_interval_sec`:
- Level 1: `stall_duration_sec >= 1 × stall_threshold_sec` → WARNING
- Level 2: `stall_duration_sec >= 2 × stall_threshold_sec` → ERROR
- Level 3: `stall_duration_sec >= 3 × stall_threshold_sec` → SHUTDOWN
- Level 4: `stall_duration_sec >= 4 × stall_threshold_sec` → ABORT

**StallAction** returned by the callback:
```python
class StallAction(Enum):
    CONTINUE = "continue"       # log warning, keep watching
    SHUTDOWN = "shutdown"       # caller-defined graceful drain
    ABORT = "abort"             # force exit (os._exit(2))
```

### Callback Contract

The `on_stall` callback is invoked on **every check interval** during an ongoing stall (not just first detection). This allows:
- Custom callbacks to implement their own escalation logic
- Repeated logging at each check
- Changing the action over time (e.g., CONTINUE for 5 minutes, then SHUTDOWN)

**Custom vs default ladder**: AC7 says a custom `on_stall` replaces the default *escalation logic* (level 1-4 thresholds, log messages, action decisions). However, **email and dashboard updates are independent watchdog behaviors** — they fire based on the *action returned*, not the ladder that produced it. So a custom callback that returns `ABORT` still triggers the level-4 dashboard update and SES email, because those are side effects of the ABORT action itself, not of the default ladder. Similarly, a custom callback returning `CONTINUE` after `stall_duration_sec >= 2× threshold` still triggers the level-2 dashboard/email update. The watchdog tracks `escalation_level` internally regardless of which callback produced the action.

**SHUTDOWN semantics**: The watchdog itself does not act on `SHUTDOWN` — the callback is responsible for setting whatever shutdown primitive the pipeline uses. This keeps the watchdog ignorant of `asyncio.Event` vs `threading.Event` vs any other mechanism. `SHUTDOWN` is **not terminal** — the watchdog continues monitoring after the callback returns `SHUTDOWN`. Repeated `SHUTDOWN` returns on subsequent intervals are expected; the callback should be idempotent (e.g., setting an event that is already set is a no-op). The watchdog logs the action on each interval but does not deduplicate. Monitoring continues so the escalation can reach `ABORT` if the graceful shutdown fails to produce progress.

For `ABORT`, the watchdog enters a **terminal state** (sets `_abort_armed = True`). Once armed, no further callbacks are invoked and no further dashboard updates are attempted. The abort sequence is:

1. Set `_abort_armed = True` (prevents re-entry from concurrent checks)
2. Log `CRITICAL "watchdog: ABORT armed, running pre-abort cleanup"`
3. Call `pre_abort()` if provided (with a 3-second timeout — if it hangs, proceed anyway)
4. Log `CRITICAL "watchdog: forcing exit in 5s"`
5. Sleep 5 seconds (allows log flush and any in-flight writes to land)
6. Call `os._exit(2)`

`os._exit()` is used instead of `sys.exit()` because:
- `sys.exit()` raises `SystemExit`, which may be caught by exception handlers in deadlocked threads
- `os._exit()` terminates immediately from any thread
- Exit code `2` distinguishes watchdog-forced exit from generic failures (code `1`)

The `pre_abort` hook is where pipelines clean up resources that `os._exit()` would leave dirty:
- **Crawler**: remove `.crawler.STATE.lock` file so the state can be restarted immediately
- **Extract/Reclassify**: release advisory locks (though these auto-release on connection close)
- The hook runs with a 3-second timeout; if it hangs (e.g., trying to talk to a stalled DB), the watchdog proceeds to `os._exit(2)` anyway

**`pre_abort` is always synchronous.** In threaded mode, timeout enforcement uses `threading.Timer` that calls `os._exit(2)` if the hook hasn't returned in 3 seconds. In async mode, the 3-second block on the event loop is acceptable because the process is about to terminate — there is no useful async work to preserve. Do not make `pre_abort` an async callable; the cleanup operations (file deletion, lock release) are fast synchronous I/O that don't benefit from async.

### Lifecycle

**Starting:**
- `run_async()` — returns a coroutine; caller creates the task via `asyncio.create_task()`. Must be called from the event loop thread.
- `start_thread()` — spawns a daemon thread and returns immediately. Safe to call from any thread.

**Stopping:**
- `stop()` — sets an internal `_stop` flag. The next check interval sees it and exits cleanly.
- For async: the caller should `await watchdog_task` after `stop()` or cancel the task.
- For threaded: the daemon thread exits on `stop()` or when the process exits (daemon=True).
- Pipelines must call `stop()` on normal completion to prevent false stall detection during teardown.

**Concurrency model rule**: async watchdog with async pipeline, threaded watchdog with threaded pipeline. Never cross models (e.g., threaded watchdog calling `asyncio.Event.set()` from the wrong thread). The `on_stall` callback must match the pipeline's concurrency model.

**Async-mode reliability constraint**: The async watchdog (`run_async()`) is only reliable when the event loop never blocks on synchronous I/O. If the loop blocks, the watchdog coroutine cannot fire. The crawler qualifies because all I/O (HTTP, S3, DB) goes through `aiohttp`/`aioboto3`/`asyncpg` — the `_progress_reporter` coroutine was still logging during both observed stalls, confirming the loop remained responsive. **If a future pipeline introduces synchronous blocking on the event loop, it must use `start_thread()` instead.** The threaded variant is the safe default; `run_async()` is an optimization for pipelines with a proven async-clean event loop.

### Integration Points

#### 1. Async Crawler (`async_crawler.py`)

The watchdog runs as an asyncio task alongside `_progress_reporter`:

```python
# In run_async():
def _handle_crawler_stall(info: StallInfo) -> StallAction:
    if info.stall_duration_sec >= 4 * 300:
        return StallAction.ABORT
    if info.stall_duration_sec >= 3 * 300:
        shutdown_event.set()  # same event loop, safe
        return StallAction.SHUTDOWN
    return StallAction.CONTINUE

watchdog = StallWatchdog(
    get_progress=lambda: stats.orgs_completed,
    get_active=lambda: stats.orgs_active,
    stall_threshold_sec=300,
    on_stall=_handle_crawler_stall,
    progress_total=stats.orgs_total,
)
watchdog_task = asyncio.create_task(watchdog.run_async())

# ... pipeline runs ...

watchdog.stop()
await watchdog_task
```

The crawler already has a `shutdown_event` — the callback sets it on `SHUTDOWN`, which drains workers gracefully (the same path as SIGTERM). The callback closure captures `shutdown_event` directly.

#### 2. Extract Classification Context (`extract_classification_context.py`)

Threading-based pipeline. The watchdog runs in a daemon thread. Uses an explicit worker counter instead of `threading.active_count()`:

```python
active_workers = [0]  # incremented/decremented by extraction consumers

watchdog = StallWatchdog(
    get_progress=lambda: stats["processed"],
    get_active=lambda: active_workers[0],
    stall_threshold_sec=300,
)
watchdog.start_thread()

# ... pipeline runs ...

watchdog.stop()
```

**Why not `threading.active_count()`**: It includes unrelated threads (Django, logging, GC, signal handlers). An explicit counter maintained by the workers themselves is reliable.

#### 3. Reclassify Corpus (`reclassify_corpus.py`)

Sequential pipeline. Uses a flag to distinguish "actively classifying" from "between docs / fetching batch":

```python
classifying = [0]  # set to 1 during LLM call, 0 between docs

watchdog = StallWatchdog(
    get_progress=lambda: stats["total"],
    get_active=lambda: classifying[0],
    stall_threshold_sec=600,
)
watchdog.start_thread()
```

The 600s threshold accounts for DeepSeek retries (4 attempts × 16s max backoff = ~64s worst case per doc, well under 600s). The `classifying` flag is 0 between docs, so idle periods between batches don't trigger the watchdog.

#### 4. Future Pipelines (Docling, etc.)

Any new pipeline that has a progress counter and active worker count can opt in. The contract is:
- `get_progress()` must return a monotonically increasing integer (regression is logged as an error but handled gracefully — see detection rule 5)
- `get_active()` must return 0 when no work is in flight
- **Active-worker counters must be maintained in `try/finally`** — a worker that increments on entry but fails to decrement on exception/cancellation will leave `active > 0` permanently, driving the watchdog to false ABORT. This is the most common integration bug.
- Both must be safe to call from the watchdog's thread/coroutine

### Default Stall Response

When no `on_stall` callback is provided, the watchdog uses a built-in escalation ladder. The default callback only returns `CONTINUE` or `ABORT` — it does not call `shutdown_event.set()` because it has no reference to one. Pipelines that want graceful shutdown must provide a custom callback.

Default escalation levels (based on `stall_duration_sec`):

1. **Level 1** (`>= 1× threshold`): Log `WARNING` with stall details. Return `CONTINUE`.
2. **Level 2** (`>= 2× threshold`): Log `ERROR` with stall details. If `job_id` provided, update Job `error_message` (one-time write at level transition). If `notify_email` set, send SES warning email (one-time). Return `CONTINUE`.
3. **Level 3** (`>= 3× threshold`): Log `ERROR "watchdog: no shutdown_event available via default ladder, will abort at 4×"`. Return `CONTINUE`. (This level is informational only — the default ladder cannot perform graceful shutdown because it has no reference to a shutdown primitive. Pipelines that want graceful shutdown before abort must provide a custom `on_stall` callback.)
4. **Level 4** (`>= 4× threshold`): If `job_id` provided, update Job `status = "failed"`, `exit_code = 2` (one-time write). If `notify_email` set, send SES abort email (one-time). Log `CRITICAL "watchdog: forcing exit"`. Return `ABORT`.

**Dashboard write rate-limiting**: Updates to Job records occur only at **escalation level transitions** (entering level 2, entering level 4), not on every check interval. This prevents write amplification against a possibly-stalled DB. Each update uses a fresh connection with `connect_timeout=5` and `statement_timeout=5000` (5s) to prevent the watchdog from joining the deadlock.

### Dashboard Integration

For dashboard-managed pipelines, the watchdog accepts an optional `job_id: int | None` parameter:

```python
watchdog = StallWatchdog(
    get_progress=...,
    get_active=...,
    job_id=job.id,   # enables dashboard updates
)
```

When `job_id` is set:
1. At escalation level 2 entry: update Job `error_message` with stall details (one-time)
2. At level 4 / `ABORT`: set Job `status = "failed"`, `exit_code = 2`, `error_message` includes "watchdog forced exit" (one-time)
3. All dashboard updates are **best-effort and non-blocking** — caught exceptions are logged and ignored. If the DB path is itself stalled (the very condition causing the pipeline stall), the update will time out and the watchdog continues to ABORT.
4. Updates occur only at **escalation level transitions** — not on every check interval — to prevent write amplification against a stalled DB

**Persistence path**: The `job_id` validation at `__init__` imports the Django `Job` model to confirm it exists (fail-fast on misconfiguration). However, **actual writes use raw SQL via a fresh `psycopg2` connection** (not Django ORM, not the pipeline's SQLAlchemy pool). This avoids two problems: (a) Django ORM uses the pipeline's DB connection, which may be the stalled resource; (b) SQLAlchemy's connection pool may also be exhausted. A single `psycopg2.connect()` call with `connect_timeout=5` and `options='-c statement_timeout=5000'` gives the watchdog an independent path to the database. The write is a simple `UPDATE pipeline_job SET ... WHERE id = %s` — no ORM overhead, no pool contention.

**Target table**: `pipeline_job` (Django model `pipeline.Job`). Required columns: `id` (int PK), `status` (text), `exit_code` (int, nullable), `error_message` (text, nullable). The DB connection string is read from `DJANGO_DATABASE_URL` or `DATABASE_URL` environment variable (same as the pipeline itself). **If the UPDATE affects 0 rows** (job_id does not exist or was already deleted), the watchdog logs `WARNING "watchdog: dashboard update affected 0 rows for job_id={job_id}"` and continues — it does not retry or raise.

### Email Notification (SES)

When `notify_email` is set, the watchdog sends email alerts via AWS SES at the same escalation-level transitions as dashboard writes:

- **Level 2 entry**: Subject `"⚠ Pipeline stall: {pipeline} on {hostname}"` — includes stall duration, progress counter, active workers, and host identity.
- **Level 4 / ABORT**: Subject `"🛑 Pipeline ABORT: {pipeline} on {hostname}"` — includes same details plus "watchdog forcing exit, manual restart required".

The pipeline name is derived from `sys.argv[0]` (e.g., `crawler.py`, `reclassify_corpus`). The hostname comes from `socket.gethostname()`.

**Implementation details:**
- Uses `boto3.client('ses')` with a **5-second timeout** (`Config(connect_timeout=5, read_timeout=5)`) — same defensive pattern as dashboard writes
- Sender address: `watchdog@lavandulagroup.com` (must be a verified SES identity or in a verified domain)
- All SES calls are **best-effort**: caught exceptions are logged at WARNING and ignored. A failed email must never prevent the watchdog from proceeding to ABORT.
- The boto3 client is created lazily on first send (not at `__init__`), so the import cost and credential lookup happen only when needed
- Rate-limited to level transitions only — at most 2 emails per stall incident (level 2 + level 4)
- When `_abort_armed` is set, no further emails are sent (same terminal-state rule as dashboard and callbacks)
- Custom `on_stall` callbacks that return `ABORT` also trigger the level 4 email (the email fires on the action, not the default ladder)

**No new dependencies**: boto3 is already required for S3 uploads in the crawler and extraction pipelines.

**Information disclosure**: Email bodies include pipeline name (`sys.argv[0]` basename only, not full path or arguments) and hostname. The `notify_email` parameter should come from trusted configuration (environment variable or constructor argument in the pipeline's own code), not from user input or external data. The email body does not include stack traces, environment variables, or database connection strings.

## File Location

```
lavandula/reports/stall_watchdog.py    # StallWatchdog class, StallInfo, StallAction
```

Single file. No new dependencies — boto3 is already required for S3 operations. Imported by each pipeline that opts in.

## Acceptance Criteria

### Detection
- [ ] AC1: Watchdog detects stall when `progress` unchanged and `active > 0` for `>= stall_threshold_sec`
- [ ] AC2: Watchdog does NOT trigger when `active == 0` (idle/draining)
- [ ] AC3: Watchdog does NOT trigger when progress advances (even by 1) within the threshold
- [ ] AC4: Watchdog resets stall timer and `stall_checks` when progress advances after a stall warning
- [ ] AC5: `stall_checks` increments each check interval during an uninterrupted stall, resets to 0 on any progress
- [ ] AC50: Counter regression (`progress < last_progress`) logs ERROR, resets baseline to new value, resets stall timer

### Response
- [ ] AC6: Default escalation uses `stall_duration_sec` for level determination: WARNING at 1×, ERROR at 2×, escalation warning at 3×, ABORT at 4× threshold
- [ ] AC7: Custom `on_stall` callback replaces the default escalation logic (level thresholds, log messages, action decisions). Dashboard/email side effects still fire based on the returned action and `stall_duration_sec`.
- [ ] AC8: `on_stall` callback is invoked on every check interval during a stall (not just first detection)
- [ ] AC9: `ABORT` enters terminal state (`_abort_armed`), runs `pre_abort()` with 3s timeout, sleeps 5s for log flush, calls `os._exit(2)`
- [ ] AC10: All stall events are logged with progress, active workers, stall duration, escalation level, action taken, and watchdog mode (async/thread)
- [ ] AC10a: Once `_abort_armed` is set, no further callbacks or dashboard updates are invoked (one-shot terminal state)

### Lifecycle
- [ ] AC11: `stop()` causes the watchdog to exit cleanly within one check interval
- [ ] AC12: Async variant: `run_async()` returns a coroutine suitable for `asyncio.create_task()`
- [ ] AC13: Thread variant: `start_thread()` spawns a daemon thread and returns immediately
- [ ] AC14: Repeated calls to `start_thread()` or `run_async()` raise `RuntimeError`

### Exception Safety
- [ ] AC15: Every iteration of the main loop is wrapped in try/except; any uncaught exception is logged at CRITICAL and the loop continues
- [ ] AC15a: If the loop terminates unexpectedly (not via `stop()`), log CRITICAL and update Job `error_message` if `job_id` is set
- [ ] AC16: Watchdog does not import Django, asyncio, or threading at module level (lazy imports). Exception: when `job_id` is provided, Django model import is validated eagerly at `__init__`

### Integration
- [ ] AC17: Async crawler uses `run_async()` with a custom callback that sets `shutdown_event`
- [ ] AC18: Extract command uses `start_thread()` with explicit `active_workers` counter (not `threading.active_count()`)
- [ ] AC19: Reclassify command uses `start_thread()` with `classifying` flag and 600s threshold
- [ ] AC20: All three integrations call `watchdog.stop()` on normal completion

### Dashboard
- [ ] AC21: Dashboard updates occur only at escalation level transitions (entering level 2, entering level 4), not on every check interval
- [ ] AC22: Dashboard updates use a fresh `psycopg2` connection (not ORM or pool) with `connect_timeout=5` and `statement_timeout=5000`
- [ ] AC23: Dashboard update failures are logged and ignored (never crash the watchdog)
- [ ] AC23a: When `job_id` is provided, Django model import is validated at `__init__` (fail fast)

### Email Notification
- [ ] AC39: When `notify_email` is set, SES warning email sent at level 2 entry (one-time)
- [ ] AC40: When `notify_email` is set, SES abort email sent at level 4 / ABORT (one-time, including custom callback ABORT)
- [ ] AC41: SES client uses 5-second connect and read timeouts
- [ ] AC42: SES send failures are logged at WARNING and ignored (never crash the watchdog)
- [ ] AC43: No emails sent after `_abort_armed` is set (terminal state rule)
- [ ] AC44: Email subject includes pipeline name (`sys.argv[0]`) and hostname
- [ ] AC45: At most 2 emails per stall incident (level 2 + level 4)
- [ ] AC46: Unit test: SES send is called at correct escalation transitions (mock boto3 client)

### Pre-Abort Cleanup
- [ ] AC36: `pre_abort` callback is called before `os._exit(2)` with a 3-second timeout
- [ ] AC37: If `pre_abort` raises or times out, the watchdog proceeds to `os._exit(2)` anyway
- [ ] AC38: Crawler integration's `pre_abort` removes the `.crawler.STATE.lock` file

### Testing
- [ ] AC24: Unit test: stall detected after threshold with active workers
- [ ] AC25: Unit test: no stall when progress advances
- [ ] AC26: Unit test: no stall when active == 0
- [ ] AC27: Unit test: escalation ladder fires at correct duration multiples
- [ ] AC28: Unit test: custom callback overrides default ladder
- [ ] AC29: Unit test: stall timer resets on progress after warning
- [ ] AC30: Unit test: callback exceptions are caught and watchdog continues
- [ ] AC31: Unit test: `stop()` causes clean exit
- [ ] AC32: Unit test: repeated `start_thread()` raises RuntimeError
- [ ] AC33: Tests inject a mock clock (`time.monotonic` replacement) — no real sleeps
- [ ] AC47: Unit test: dashboard write fires exactly once at level 2 entry and once at level 4 (not on every interval)
- [ ] AC48: Unit test: after `_abort_armed`, no further callbacks, dashboard writes, or emails fire
- [ ] AC49: Unit test: dashboard update times out gracefully when DB connection hangs (mock psycopg2 connect to raise after timeout)
- [ ] AC51: Unit test: counter regression resets baseline and stall timer
- [ ] AC52: Dashboard UPDATE affecting 0 rows logs WARNING and continues (does not retry or raise)
- [ ] AC53: `notify_email` email body includes only `sys.argv[0]` basename and `socket.gethostname()`, no full paths, arguments, or env vars

### Configuration
- [ ] AC34: `stall_threshold_sec` configurable per pipeline (default 300)
- [ ] AC35: `check_interval_sec` configurable (default 60)

## Traps to Avoid

1. **False positives on slow-but-progressing work**: A single large PDF taking 4 minutes to extract is not a stall. The threshold must be long enough to accommodate legitimate slow items. 300s default with `active > 0` check handles this.

2. **Race conditions on progress counter**: The progress counter is read from a shared mutable (dict, dataclass attribute). In async code this is safe (single-threaded event loop). In threaded code, integer reads are atomic in CPython (GIL). Do NOT add locks — they'd introduce the kind of contention the watchdog is trying to detect.

3. **Watchdog itself stalling**: If the event loop is blocked (e.g., synchronous S3 call blocking the thread), the async watchdog coroutine won't fire either. The threaded variant (`start_thread()`) is immune to event loop blocks. For the crawler, the progress reporter already runs on the event loop and was still logging during the stall — so the async variant will work there.

4. **ABORT during data write**: `os._exit(2)` during a DB transaction could leave partial writes. The `ON CONFLICT DO NOTHING` pattern used by all pipelines makes this safe — restarted runs skip already-written rows.

5. **Shutdown event ignored by deadlocked workers**: The `shutdown_event` only helps if workers periodically check it. Workers blocked on `queue.put()` won't check. The ABORT fallback (4× threshold) handles this case.

6. **`sys.exit()` vs `os._exit()`**: `sys.exit()` raises `SystemExit` which can be caught by exception handlers in blocked threads, preventing actual exit. `os._exit()` terminates immediately from any thread. Use `os._exit(2)`.

7. **Cross-model concurrency**: Never use a threaded watchdog with an async pipeline's Event, or vice versa. `asyncio.Event.set()` from a non-event-loop thread requires `loop.call_soon_threadsafe()`. The spec avoids this by requiring the callback to match the pipeline's model.

8. **Dashboard updates during DB stall**: The dashboard update itself might hang if RDS is the root cause of the stall. Using a fresh connection with a short timeout (5s) and best-effort semantics prevents the watchdog from joining the deadlock.

9. **SES send blocking the watchdog**: `boto3.client('ses').send_email()` is a synchronous HTTP call. With the 5-second timeout it won't hang forever, but it adds up to 10 seconds (connect + read) to the check interval. This is acceptable — the watchdog's job is to detect multi-minute stalls, not sub-second precision. Do NOT make the SES call async or threaded; the complexity isn't worth it for at most 2 emails per incident.

10. **Active-worker counter leak**: If a worker increments `active_workers` but crashes before decrementing (no `try/finally`), the counter stays elevated permanently. The watchdog sees `active > 0` with no progress and escalates to ABORT on a pipeline that is actually idle. All integrations must use `try/finally` for counter maintenance.

11. **False positives during reclassify between-batch idle**: The `classifying` flag returns 0 between docs, so the watchdog correctly identifies these as idle (not stalled). Without this flag, the 600s threshold would need to account for batch-fetch latency.

## Consultation Log

- **Codex (spec-review)**: REQUEST_CHANGES — ABORT exit code inconsistency, shutdown_event ownership unclear, dashboard integration vague, threading.active_count fragile, sequential pipeline false positives, callback invocation contract ambiguous, lifecycle management incomplete, timing semantics imprecise, sys.exit safety concern, testing gaps
- **Claude (spec-review)**: REQUEST_CHANGES — exit code mismatch, extract get_active fragile, reclassify get_active=1 false positives, shutdown_event not in constructor, async/thread Event API divergence, dashboard plumbing missing, consecutive_stalls semantics unclear, callback exception handling unspecified, watchdog cleanup on normal exit, testing time-mock strategy

All findings addressed in revision 2.

- **Gemini (red-team-spec)**: 6 findings — (1) CRITICAL: `os._exit()` leaves lock files preventing restart → added `pre_abort` cleanup hook with 3s timeout; (2) HIGH: callback exception crashes watchdog → full try/except per iteration already specified, clarified `_abort_armed` prevents re-entry; (3) HIGH: dashboard update amplifies DB pressure during stall → rate-limited to escalation level transitions only, fresh connection with 5s timeout; (4) MEDIUM: `_abort_armed` not terminal, concurrent checks could re-enter → made one-shot terminal state; (5) MEDIUM: Django import deferred to stall time → eager validation at `__init__` when `job_id` set; (6) LOW: no `clock` injection for tests → added `clock` parameter (default `time.monotonic`)

All findings addressed in revision 3.

- **Codex (spec-review, round 2)**: REQUEST_CHANGES — 6 findings: (1) custom callback vs email/dashboard contract inconsistency → clarified email/dashboard are independent watchdog behaviors that fire on the action, not the ladder; (2) Django/SQLAlchemy mixed persistence → switched to raw psycopg2 for writes, Django import is init-time validation only; (3) SHUTDOWN not terminal, repeated returns ambiguous → clarified SHUTDOWN is non-terminal, callback must be idempotent, watchdog continues toward ABORT; (4) pre_abort timeout undefined in async mode → specified pre_abort is always sync, 3s block acceptable in abort path; (5) unexpected loop termination conditions not enumerated → listed KeyboardInterrupt, CancelledError, external os._exit; outer BaseException handler; (6) missing test ACs for side effects → added AC47-49 for one-time writes, terminal-state suppression, DB timeout
- **Gemini (spec-review, round 2)**: Rate-limited, could not complete

All findings addressed in revision 4.

- **Codex (red-team-spec)**: REQUEST_CHANGES — 5 findings: (1) HIGH: async watchdog unreliable if event loop blocks → documented async-mode constraint, threaded is safe default, async only for proven async-clean loops; (2) MEDIUM: counter regression undefined → added detection rule 5, logs ERROR, resets baseline; (3) MEDIUM: active-worker bookkeeping underspecified → required try/finally, added trap 10; (4) MEDIUM: dashboard write for invalid/missing job_id → specified table, columns, 0-rows behavior; (5) LOW: notify_email info disclosure → constrained to trusted config, basename only, no env vars

All findings addressed in this revision.

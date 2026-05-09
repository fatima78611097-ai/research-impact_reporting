# Plan 0036: Pipeline Stall Watchdog

**Spec**: `locard/specs/0036-pipeline-stall-watchdog.md`
**Status**: Draft
**Phases**: 4 (sequenced by dependency)

## Context for the Builder

On 2026-05-08/09, two crawler jobs stalled silently for hours due to backpressure deadlocks. The process stayed alive but made zero progress. This plan implements a generic `StallWatchdog` that detects frozen progress counters and escalates through WARNING → ERROR → SHUTDOWN → ABORT, with SES email alerts and dashboard Job updates.

**Key codebase context**:
- Async crawler: `lavandula/reports/async_crawler.py` → `run_async()`, `CrawlStats` dataclass (line 57), `_progress_reporter` (line 680), `shutdown_event` (asyncio.Event)
- Extract command: `lavandula/dashboard/pipeline/management/commands/extract_classification_context.py` → `handle()`, threading-based, `stats["processed"]` counter
- Reclassify command: `lavandula/dashboard/pipeline/management/commands/reclassify_corpus.py` → `handle()`, sequential, `stats["total"]` counter
- DB access: `lavandula/common/db.py` → `make_app_engine()` (SQLAlchemy, RDS)
- Dashboard Job model: `lavandula/dashboard/pipeline/models.py` → `Job`
- S3/boto3: already in use throughout (`lavandula/reports/s3_archive.py`)
- Lock files: `.crawler.STATE.lock` pattern used by crawler

**Important constraints**:
- The watchdog file has zero new dependencies (psycopg2, boto3 already available)
- Dashboard writes use raw `psycopg2` (not ORM or pipeline's connection pool) — independent path to DB
- `pre_abort` is always synchronous, even in async pipelines
- All side effects (dashboard, email) are best-effort with 5s timeouts
- Tests must use injected `clock` — no real sleeps

---

## Phase 1: Core StallWatchdog Module

**Goal**: Implement `StallWatchdog`, `StallInfo`, `StallAction` in a single file with full detection logic, escalation ladder, lifecycle management, and exception safety.

**File**: `lavandula/reports/stall_watchdog.py`

### Implementation Steps

1. **Define enums and dataclasses**:
   - `StallAction` enum: `CONTINUE`, `SHUTDOWN`, `ABORT`
   - `StallInfo` dataclass: `stall_duration_sec`, `last_progress`, `active_workers`, `progress_total`, `stall_checks`, `escalation_level`

2. **Implement `StallWatchdog.__init__`**:
   - Store all constructor params from spec (10 params)
   - Set `_clock = clock or time.monotonic`
   - Initialize internal state: `_last_progress = 0`, `_stall_start = None`, `_stall_checks = 0`, `_stop = False`, `_started = False`, `_abort_armed = False`, `_last_escalation_level = 0`
   - If `job_id` is set: eagerly `from pipeline.models import Job` (full path: `lavandula/dashboard/pipeline/models.py`) to validate. Catch `ImportError` at init — surfaces misconfiguration immediately. The `Job` reference is stored but not used until dashboard update time.
   - Lazy-initialized: `_ses_client = None`, `_email_sent_levels = set()`

3. **Implement `_check_once()`** — the core detection method:
   - If `_abort_armed`: return immediately (terminal state)
   - Call `get_progress()` and `get_active()`
   - Apply detection rules 1-5 from spec (progress advance → reset; stall → increment; idle → skip; regression → log ERROR + reset)
   - Calculate `escalation_level` based on `stall_duration_sec` and threshold multiples (always computed, even with custom callback)
   - If custom `on_stall`: call it with `StallInfo`, get `StallAction`. **If action is `ABORT`, force `escalation_level = 4`** — this ensures level-4 dashboard/email side effects fire before `_do_abort()` regardless of callback origin (spec AC7, AC40)
   - If default ladder: apply level 1-4 logic from spec
   - Fire side effects: `_maybe_update_dashboard(escalation_level)`, `_maybe_send_email(escalation_level)` based on level transitions
   - If action is `ABORT`: call `_do_abort()`

4. **Implement `_do_abort()`** — terminal state handler (full implementation, not stubbed):
   - Set `_abort_armed = True`
   - Log CRITICAL "watchdog: ABORT armed, running pre-abort cleanup"
   - If `pre_abort`: start `threading.Timer(3, lambda: os._exit(2))` as safety net, call `pre_abort()`, cancel timer if it returns in time
   - Log CRITICAL "watchdog: forcing exit in 5s"
   - Sleep 5s (allows log flush)
   - Call `os._exit(2)`
   - Note: this is fully implemented in Phase 1, not deferred to Phase 3. Phase 3 only adds the *tests* for pre_abort timeout/exception behavior.

5. **Implement `_maybe_update_dashboard(level)`**:
   - Only fires at level transitions (entering level 2 or level 4)
   - If `_abort_armed` or `job_id is None`: return
   - Read DB connection string from `DJANGO_DATABASE_URL` or `DATABASE_URL` env var
   - `psycopg2.connect()` with `connect_timeout=5`, `options='-c statement_timeout=5000'`
   - At level 2: `UPDATE pipeline_job SET error_message = %s WHERE id = %s`
   - At level 4: `UPDATE pipeline_job SET status = 'failed', exit_code = 2, error_message = %s WHERE id = %s`
   - Check `cursor.rowcount` — if 0, log WARNING
   - Entire method wrapped in `try/except Exception` — log and continue

6. **Implement `_maybe_send_email(level)`**:
   - Only fires at level transitions (entering level 2 or level 4) and only once per level (`_email_sent_levels`)
   - If `_abort_armed` or `notify_email is None`: return
   - Lazily create `boto3.client('ses', config=Config(connect_timeout=5, read_timeout=5))`
   - Build subject with `os.path.basename(sys.argv[0])` and `socket.gethostname()`
   - Build body with stall details (duration, progress, active workers) — no env vars, no full paths
   - `ses_client.send_email()` with `Source='watchdog@lavandulagroup.com'`
   - Entire method wrapped in `try/except Exception` — log WARNING and continue

7. **Implement `_run_loop(sleep_fn)`** — shared loop logic for both modes:
   - Outer `try/except BaseException` for unexpected termination:
     - **Carve out `asyncio.CancelledError`**: if the exception is `CancelledError` and `self._stop` is set, treat it as normal shutdown (the caller used `task.cancel()` after `stop()`) — do NOT log CRITICAL or update Job. If `_stop` is NOT set, treat as unexpected and log.
     - For all other `BaseException`: log `CRITICAL "watchdog terminated unexpectedly: {exc}"`
     - If unexpected and `job_id`: call `_maybe_update_dashboard()` with error_message containing the exception (best-effort, in its own try/except)
   - Inner `while not self._stop and not self._abort_armed`:
     - `try/except Exception` wrapping `_check_once()` — log CRITICAL with traceback, continue
     - Call `sleep_fn(check_interval_sec)`

8. **Implement `run_async()`**:
   - If `_started`: raise `RuntimeError`
   - Set `_started = True`
   - Call `_run_loop(asyncio.sleep)` (asyncio.sleep imported lazily)

9. **Implement `start_thread()`**:
   - If `_started`: raise `RuntimeError`
   - Set `_started = True`
   - Store thread handle as `self._thread`
   - Spawn daemon thread targeting `_run_loop(time.sleep)` (threading imported lazily)
   - Return immediately — the thread handle is stored for tests to `join()` but the caller does not need it

10. **Implement `stop()`**:
    - Set `_stop = True`

### Testing for this phase

All in a new test file: `lavandula/reports/tests/test_stall_watchdog.py`

Use `pytest`. **Testing strategy for determinism (no real sleeps)**:

- **Mock clock**: Inject a `clock` callable that returns controlled values. Advances are explicit in the test.
- **Patch `time.sleep`**: Replace with a no-op or a function that advances the mock clock. Use `unittest.mock.patch('time.sleep')`.
- **Patch `asyncio.sleep`**: Replace with a no-op coroutine for async tests. Use `unittest.mock.patch('asyncio.sleep', new_callable=AsyncMock)`.
- **Patch `os._exit`**: Replace with `unittest.mock.patch('os._exit', side_effect=SystemExit(2))` — converts the hard exit into a catchable exception for test assertions.
- **Patch `threading.Timer`**: For pre_abort timeout tests, mock the Timer to fire immediately or not at all depending on the test case.
- **Thread tests**: Use `watchdog.start_thread()` then `watchdog.stop()` then `watchdog._thread.join(timeout=1)` to observe clean exit deterministically. The patched `time.sleep` ensures the thread runs through its loop iterations instantly.

Tests to write (map to ACs):
- `test_stall_detected_after_threshold` (AC1, AC24)
- `test_no_stall_when_idle` (AC2, AC26)
- `test_no_stall_when_progress_advances` (AC3, AC25)
- `test_stall_timer_resets_on_progress` (AC4, AC29)
- `test_stall_checks_increment_and_reset` (AC5)
- `test_counter_regression` (AC50, AC51)
- `test_escalation_ladder_levels` (AC6, AC27)
- `test_custom_callback_replaces_ladder` (AC7, AC28)
- `test_callback_invoked_every_interval` (AC8)
- `test_abort_terminal_state` (AC9, AC10a, AC48)
- `test_log_format` (AC10) — check log messages include required fields
- `test_stop_clean_exit` (AC11, AC31)
- `test_run_async_returns_coroutine` (AC12)
- `test_start_thread_daemon` (AC13)
- `test_repeated_start_raises` (AC14, AC32)
- `test_exception_in_callback_continues` (AC15, AC30)
- `test_unexpected_termination_logs_critical` (AC15a) — inject `KeyboardInterrupt` via mock clock side_effect
- `test_unexpected_termination_updates_job` (AC15a) — same, but with `job_id` set; verify best-effort dashboard update attempted
- `test_no_module_level_imports` (AC16) — inspect module attributes
- `test_clock_injection` (AC33)
- `test_threshold_configurable` (AC34)
- `test_interval_configurable` (AC35)

**Skip dashboard/email/pre_abort tests here — they go in Phases 2 and 3.**

### Acceptance criteria covered
AC1-AC16, AC24-AC35, AC50-AC51

---

## Phase 2: Dashboard Integration & Email Notification

**Goal**: Implement and test the dashboard write path (psycopg2) and SES email notification.

**File**: Same `lavandula/reports/stall_watchdog.py` (methods already stubbed in Phase 1)

### Implementation Steps

1. **Dashboard tests** (add to `test_stall_watchdog.py`):
   - `test_dashboard_write_at_level_transitions` (AC21, AC47) — mock `psycopg2.connect`, verify UPDATE called exactly once at level 2 entry and once at level 4
   - `test_dashboard_uses_fresh_psycopg2` (AC22) — verify `psycopg2.connect()` called with correct params
   - `test_dashboard_failure_ignored` (AC23) — mock connect to raise, verify watchdog continues
   - `test_dashboard_eager_import_validation` (AC23a) — verify `ImportError` at init when `job_id` set and Django unavailable
   - `test_dashboard_zero_rows_warning` (AC52) — mock cursor.rowcount=0, verify WARNING logged
   - `test_dashboard_timeout_graceful` (AC49) — mock `psycopg2.connect` to raise `psycopg2.OperationalError("connection timed out")`, verify watchdog logs WARNING and continues (not a real hang — tests the timeout error path)

2. **Email tests** (add to `test_stall_watchdog.py`):
   - `test_ses_warning_at_level2` (AC39, AC46) — mock boto3 client, verify `send_email` called once at level 2
   - `test_ses_abort_at_level4` (AC40, AC46) — verify `send_email` called at level 4, including from custom callback ABORT
   - `test_ses_timeout_config` (AC41) — verify `Config(connect_timeout=5, read_timeout=5)` passed to boto3
   - `test_ses_failure_ignored` (AC42) — mock send_email to raise, verify watchdog continues
   - `test_ses_no_email_after_abort_armed` (AC43) — verify no emails after `_abort_armed`
   - `test_ses_subject_format` (AC44) — verify subject includes basename and hostname
   - `test_ses_max_two_emails` (AC45) — run full escalation, verify exactly 2 send_email calls
   - `test_ses_body_no_sensitive_info` (AC53) — verify body does not contain full argv, env vars, connection strings

### Acceptance criteria covered
AC21-AC23a, AC39-AC46, AC47, AC49, AC52-AC53

---

## Phase 3: Pre-Abort Hook Tests

**Goal**: Test the `pre_abort` callback timeout enforcement and ABORT sequence edge cases. The implementation is already complete in Phase 1's `_do_abort()` — this phase adds the hard-to-test timeout and exception scenarios.

**File**: Tests only — `lavandula/reports/tests/test_stall_watchdog.py`

### Tests

1. `test_pre_abort_called_before_exit` (AC36) — mock `os._exit`, verify `pre_abort` called before it
2. `test_pre_abort_timeout_proceeds` (AC37) — `pre_abort` that blocks (use `threading.Event.wait()`), mock `threading.Timer` to fire immediately, verify `os._exit(2)` called
3. `test_pre_abort_exception_proceeds` (AC37) — `pre_abort` that raises, verify `os._exit(2)` still called

### Acceptance criteria covered
AC36-AC37

---

## Phase 4: Pipeline Integration

**Goal**: Wire the watchdog into all three existing pipelines.

### 4a: Async Crawler (`lavandula/reports/async_crawler.py`)

**Changes to `run_async()` function** (around line 789):

1. Import `StallWatchdog, StallInfo, StallAction` from `lavandula.reports.stall_watchdog`
2. Define `_handle_crawler_stall(info: StallInfo) -> StallAction` closure that:
   - At `>= 4× 300s`: return `ABORT`
   - At `>= 3× 300s`: `shutdown_event.set()`, return `SHUTDOWN`
   - Otherwise: return `CONTINUE`
3. Define `_crawler_pre_abort()` closure that removes the lock file (if it exists) using the existing lock path variable
4. Create `StallWatchdog(get_progress=lambda: stats.orgs_completed, get_active=lambda: stats.orgs_active, stall_threshold_sec=300, on_stall=_handle_crawler_stall, pre_abort=_crawler_pre_abort, notify_email=os.environ.get('WATCHDOG_NOTIFY_EMAIL'), progress_total=stats.orgs_total, job_id=job.id if job else None)`
5. `watchdog_task = asyncio.create_task(watchdog.run_async())`
6. In the `finally` block: `watchdog.stop()` then `await watchdog_task`

### 4b: Extract Classification Context (`lavandula/dashboard/pipeline/management/commands/extract_classification_context.py`)

**Changes to `handle()` method**:

1. Import `StallWatchdog` from `lavandula.reports.stall_watchdog`
2. Add `active_workers = [0]` before the ThreadPoolExecutor
3. Wrap worker logic in `try/finally` that increments/decrements `active_workers[0]`
4. Create `StallWatchdog(get_progress=lambda: stats["processed"], get_active=lambda: active_workers[0], stall_threshold_sec=300, notify_email=os.environ.get('WATCHDOG_NOTIFY_EMAIL'), job_id=job.id if hasattr(job, 'id') else None)`
5. `watchdog.start_thread()`
6. In the `finally` block: `watchdog.stop()` then `watchdog._thread.join(timeout=2)` — brief join prevents late side effects racing with pipeline teardown. The 2s timeout ensures the process still exits promptly if the watchdog thread is stuck.

### 4c: Reclassify Corpus (`lavandula/dashboard/pipeline/management/commands/reclassify_corpus.py`)

**Changes to `handle()` method**:

1. Import `StallWatchdog` from `lavandula.reports.stall_watchdog`
2. Add `classifying = [0]` before the main loop
3. Set `classifying[0] = 1` before the LLM call, `classifying[0] = 0` after (in `try/finally`)
4. Create `StallWatchdog(get_progress=lambda: stats["total"], get_active=lambda: classifying[0], stall_threshold_sec=600, notify_email=os.environ.get('WATCHDOG_NOTIFY_EMAIL'), job_id=job.id if hasattr(job, 'id') else None)`
5. `watchdog.start_thread()`
6. In the `finally` block: `watchdog.stop()` then `watchdog._thread.join(timeout=2)`

### 4d: Crawler lock file pre_abort test

- `test_crawler_pre_abort_removes_lock` (AC38) — create a temp lock file, call the pre_abort closure, verify file is gone.
- `test_crawler_callback_shutdown_then_abort` (AC17) — simulate the crawler's `_handle_crawler_stall` callback through a full stall sequence: verify CONTINUE at 1× threshold, CONTINUE at 2×, SHUTDOWN at 3× (sets mock event), ABORT at 4×. Verify `shutdown_event.set()` was called exactly once (idempotent on repeated SHUTDOWN).

### Acceptance criteria covered
AC17-AC20, AC38

---

## Phase Order & Dependencies

```
Phase 1 (Core)
    ↓
Phase 2 (Dashboard + Email)   ← depends on Phase 1 methods
    ↓
Phase 3 (Pre-Abort)           ← depends on Phase 1 _do_abort
    ↓
Phase 4 (Integration)         ← depends on all above
```

Phases 2 and 3 can be done in parallel if desired — they modify independent methods.

## Test Execution

```bash
# Run all watchdog tests
pytest lavandula/reports/tests/test_stall_watchdog.py -v

# Quick smoke test of core detection
pytest lavandula/reports/tests/test_stall_watchdog.py -k "test_stall_detected" -v
```

All tests use mock clock injection — no real sleeps, no external services, no DB connections needed. Dashboard and SES tests mock `psycopg2` and `boto3` respectively.

## Notes

**`log_tail` in Goal 4**: The spec's Goal 4 says stall events surface in "status, error_message, log_tail". The watchdog updates `status` and `error_message` directly. `log_tail` is populated by the pipeline's existing log-capture mechanism (the dashboard worker tails stdout/stderr into the Job record) — the watchdog's CRITICAL log messages naturally appear there. No explicit `log_tail` UPDATE is needed from the watchdog.

## Consultation Log

- **Codex (plan-review)**: REQUEST_CHANGES — 5 findings: (1) AC15a unexpected termination Job update not fully planned → added explicit path in _run_loop; (2) start_thread lifecycle underspecified → stored thread handle, documented join behavior; (3) no-real-sleeps testing strategy incomplete → added explicit patching strategy for time.sleep, asyncio.sleep, os._exit, threading.Timer; (4) _do_abort split across Phase 1 and 3 → clarified Phase 1 implements fully, Phase 3 is tests only; (5) log_tail not addressed → added note that log_tail is populated by pipeline's log-capture, not watchdog. All addressed.

- **Codex (red-team-plan)**: REQUEST_CHANGES — 6 findings: (1) HIGH: custom callback ABORT must normalize to level 4 for side effects → added explicit escalation_level=4 forcing step; (2) HIGH: threaded integrations don't join thread → added _thread.join(timeout=2) in finally blocks; (3) MEDIUM: CancelledError treated as unexpected → carved out CancelledError+_stop as normal shutdown; (4) MEDIUM: Django import path ambiguous → pinned exact `from pipeline.models import Job` path; (5) MEDIUM: no crawler-specific callback sequence test → added test_crawler_callback_shutdown_then_abort; (6) LOW: dashboard timeout test mock unclear → clarified OperationalError mock. All addressed.

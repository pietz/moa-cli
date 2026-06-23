# 016 - Direct test coverage for `execution.stream` and `_terminate`

**Status:** ready
**Touches:** new file `tests/test_execution.py`; optionally `tests/conftest.py` for
shared fakes.
**Related:** 001 (provider roster / doctor), the module-split that isolated
`execution.py`.

## Context

`src/moa_cli/execution.py` has no dedicated test file. The two paths most likely to
misbehave in production are the two with zero direct coverage:

- `stream()` (`execution.py:145-170`) - the async generator that fans providers out in
  parallel via `asyncio.as_completed` and yields each result as it finishes. This is
  moa's core feature, yet every consumer test replaces it: `tests/test_cli.py:32-37`
  and `tests/test_config.py:13-18` each define a `_fake_stream` and monkeypatch it in.
- `_terminate()` (`execution.py:35-56`) - the SIGTERM -> 2s -> SIGKILL escalation
  ladder with three exception-class branches (`ProcessLookupError`, generic `Exception`,
  success-within-2s). `tests/test_providers.py:388` replaces `_terminate` with a fake;
  the only adjacent test (`test_run_provider_times_out`, `:364`) uses a 5s sleep with a
  0.1s timeout and only ever exercises the fast-SIGTERM path.

So: a regression in stream-ordering, abandoned-task cleanup, or the kill ladder would
not be caught by the suite.

## Goal

A `tests/test_execution.py` that exercises the real `stream` and the real `_terminate`
under controlled fakes, pinning the behavior the rest of the suite assumes.

## Decisions

- **`stream` ordering test:** monkeypatch `asyncio.create_subprocess_exec` (or
  `run_provider`) so the spawned tasks resolve in a deterministic but out-of-creation
  order (e.g. provider C resolves first, then A, then B). Assert `stream` yields in
  **completion** order, not creation order, and that all providers are launched
  concurrently (the tasks are all created before any `await completed` blocks - assert
  task-creation count or timing).
- **`_terminate` branch tests:** construct a fake process object covering each branch:
  1. `returncode is not None` -> early return, no signal sent.
  2. SIGTERM succeeds within the 2s wait -> returns, no SIGKILL.
  3. SIGTERM ignored (wait times out) -> SIGKILL sent, then `wait()`.
  4. `os.killpg` raises `ProcessLookupError` -> clean return.
  5. (Generic `Exception` from `killpg` -> falls back to `process.terminate()` /
     `process.kill()`.)
  Use `asyncio.sleep` shenanigans or a fake clock to avoid real 2s waits where
  practical; a single real 2s-timeout test is acceptable if faking the clock is ugly.
- Follow the existing test style in `tests/test_providers.py` (plain functions, no
  classes, `monkeypatch` fixtures).
- Do not change `execution.py` source in this ticket - it is characterization coverage.
  If a test reveals a real bug, stop and record it as a follow-up rather than fixing
  inline.

## Acceptance criteria

- [ ] `tests/test_execution.py` exists and asserts `stream` yields results in
      completion order when tasks resolve out of creation order.
- [ ] `stream` test asserts all provider tasks are created (concurrent launch), not
      serialized.
- [ ] `_terminate` is exercised against a fake process for each of: already-dead,
      fast-SIGTERM exit, SIGTERM-ignored (forces SIGKILL), and `ProcessLookupError`.
- [ ] `uv run pytest` passes; `uv run ruff check src tests` clean.
- [ ] No change to `src/moa_cli/execution.py` in this ticket.

## Notes

If the shared `_fake_stream` / fake-process helpers would also clean up
`tests/test_cli.py` / `tests/test_providers.py`, leave that consolidation for a
separate cleanup ticket - this one is about coverage, not dedup.

# Plan 001: Terminate provider subprocesses when runs are cancelled

> Follow this plan step by step. Do not commit or push. The reviewer maintains
> `plans/README.md`.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `3df293e`, 2026-06-23

## Why this matters

`run_provider()` only calls `_terminate()` after its own timeout. Cancellation
from Ctrl-C or a cancelled parent task exits through `finally`, removes the
temporary output file, and can leave the spawned process group running. This is
especially unsafe for expensive or `--yolo` agent runs.

## Current state

- `src/moa_cli/execution.py:73-138` owns process creation and cleanup.
- `_terminate()` already performs process-group SIGTERM/SIGKILL cleanup and
  should be reused.
- `tests/test_providers.py` contains subprocess execution tests.

## Commands

- `uv run pytest -q` → 125 or more tests pass.
- `uvx ruff check src tests` → exit 0.
- `uvx ruff format --check src tests` → exit 0.

## Scope

In scope:

- `src/moa_cli/execution.py`
- `tests/test_providers.py`

Out of scope:

- Provider command arguments and timeout semantics.
- CLI command behavior.

## Steps

1. Retain the spawned process in a variable initialized before creation.
2. Catch `asyncio.CancelledError` around communication, terminate the process
   group with `_terminate()`, then re-raise cancellation.
3. Add a regression test with a fake process proving cancellation invokes
   termination and does not become a normal `RunResult`.
4. Verify the commands above.

## Done criteria

- Cancellation terminates a still-running process group and re-raises
  `CancelledError`.
- Timeout behavior remains unchanged.
- Full test and Ruff checks pass.

## STOP conditions

- Stop if correct cleanup requires changing provider CLI flags.
- Stop if cancellation cannot be tested without real external agent CLIs.

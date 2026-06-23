# Plan 002: Make distill and debate exit statuses reflect final-output success

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: Plan 001
- **Category**: bug
- **Planned at**: commit `3df293e`, 2026-06-23

## Why this matters

`distill` is documented to return a merged answer, but exits zero when only one
proposer succeeds and no synthesis is emitted. It also exits zero when the
synthesizer fails. `debate` exits zero when debater turns succeed even if its
promised moderator verdict fails.

## Current state

- `src/moa_cli/cli.py:404-445` runs synthesis but `_run_synthesis()` returns
  `None`, preventing the command from deciding success accurately.
- `src/moa_cli/cli.py:518-520` checks any successful transcript entry, including
  debater turns, rather than the verdict.
- `tests/test_cli.py` contains distill and debate command tests.

## Scope

- `src/moa_cli/cli.py`
- `tests/test_cli.py`

Do not change output record shapes or prompt contents.

## Steps

1. Make `_run_synthesis()` return a success indicator or `RunResult`.
2. Exit non-zero when synthesis is skipped, invalid, or returns non-`ok`.
3. Make debate exit non-zero when no successful verdict is produced, while
   preserving emitted failed verdict diagnostics.
4. Update and add focused CLI tests for one-success distill, failed synthesizer,
   and failed verdict.
5. Run full tests and Ruff checks.

## Done criteria

- `distill` exits zero only when it emits a successful synthesis.
- `debate` exits zero only when it emits a successful verdict.
- Existing `ask` behavior is unchanged.

## STOP conditions

- Stop if this requires changing JSON or Markdown output schemas.

# Plan 003: Restrict explicit synthesizers to providers selected for the run

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: Plan 002
- **Category**: correctness
- **Planned at**: commit `3df293e`, 2026-06-23

## Why this matters

`choose_synthesizer()` accepts any globally known provider name. A user can pin
two providers for proposer work but accidentally invoke a third unselected or
uninstalled provider as synthesizer.

## Current state

- `src/moa_cli/workflows.py:63-76` receives the selected candidate names but
  validates explicit choices against global `PROVIDERS`.
- `src/moa_cli/cli.py:420-438` passes selected provider names and then indexes
  global `PROVIDERS`.
- Tests for synthesizer selection are in `tests/test_output.py` and
  `tests/test_cli.py`.

## Scope

- `src/moa_cli/workflows.py`
- `src/moa_cli/cli.py`
- `tests/test_output.py`
- `tests/test_cli.py`

## Steps

1. Require explicit synthesizer names to be present in `candidates`.
2. Return a clear error naming the unselected synthesizer.
3. Add unit and CLI regression tests.
4. Run full tests and Ruff checks.

## Done criteria

- `auto`, `first`, and `random` remain unchanged.
- Selected explicit providers work.
- Unselected explicit providers cause a non-zero distill exit without spawning.

## STOP conditions

- Stop if preserving a documented ability to use an external unselected
  synthesizer is discovered.

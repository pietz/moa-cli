# Plan 004: Align imports and tests with the extracted module boundaries

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: Plan 003
- **Category**: tech-debt
- **Planned at**: commit `3df293e`, 2026-06-23

## Why this matters

The production code is split, but tests still import most symbols through
`moa_cli.cli` and patch `cli.asyncio`/`cli.shutil`. This preserves accidental
coupling to the old monolith and makes the compatibility facade larger than the
actual supported CLI surface.

## Current state

- `src/moa_cli/cli.py:16-98` re-exports implementation symbols and even imported
  modules solely for old tests.
- `tests/test_providers.py`, `tests/test_output.py`, and `tests/test_config.py`
  import extracted functionality from `moa_cli.cli`.
- Command-level tests correctly import `moa_cli.cli`.

## Scope

- `src/moa_cli/cli.py`
- `tests/test_providers.py`
- `tests/test_output.py`
- `tests/test_config.py`
- `tests/test_cli.py`
- `tests/conftest.py`

Keep the current five-file test split. Do not recombine tests.

## Steps

1. Import implementation symbols in tests from their owner modules:
   `providers`, `execution`, `workflows`, `output`, and `config`.
2. Patch owner-module dependencies directly, such as
   `execution.asyncio.create_subprocess_exec` and `providers.shutil.which`.
3. Keep only deliberate public compatibility exports in `cli.py`; remove
   `random`, `shutil`, and test-only aliases from `__all__`.
4. Preserve command-level monkeypatch seams where orchestration genuinely owns
   the imported callable.
5. Run full tests and Ruff checks.

## Done criteria

- Tests reflect the production module ownership.
- `cli.py` no longer imports modules merely to support test monkeypatching.
- 125 or more tests pass.

## STOP conditions

- Stop if removing a compatibility export breaks documented public API usage in
  README or package metadata.

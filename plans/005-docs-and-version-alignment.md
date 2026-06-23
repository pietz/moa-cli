# Plan 005: Refresh architecture references and synchronize package versions

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: Plan 004
- **Category**: docs
- **Planned at**: commit `3df293e`, 2026-06-23

## Why this matters

Backlog guidance tells future executors to merge features into `cli.py` and edit
the deleted `tests/test_moa.py`; ticket 007 also uses the obsolete `fuse` name.
Separately, `pyproject.toml` and `__init__.py` are version 0.3.3 while `uv.lock`
records 0.3.2.

## Current state

- `backlog/!README.md:60` says completed work is merged into `cli.py`.
- Active tickets 010, 012, and 013 list stale file paths.
- `backlog/007-agent-skill.md:29-40` says `moa fuse`.
- `pyproject.toml:3` is 0.3.3; `src/moa_cli/__init__.py` is 0.3.3;
  `uv.lock` package entry is 0.3.2.

## Scope

- `backlog/!README.md`
- `backlog/007-agent-skill.md`
- `backlog/010-live-connection-check.md`
- `backlog/012-run-telemetry.md`
- `backlog/013-read-only-mode.md`
- `uv.lock`

Do not change product decisions in the tickets.

## Steps

1. Replace monolith-specific instructions with responsibility-based module
   guidance and current split test paths.
2. Replace obsolete `fuse` references with `distill`.
3. Update the local package version in `uv.lock` to 0.3.3 without changing
   dependency resolutions.
4. Run full tests, Ruff checks, and `git diff --check`.

## Done criteria

- No active backlog file references `tests/test_moa.py` or `moa fuse`.
- Backlog workflow no longer requires merging everything into `cli.py`.
- Package version is 0.3.3 in `pyproject.toml`, `__init__.py`, and `uv.lock`.

## STOP conditions

- Stop if updating the lockfile changes third-party dependency versions.

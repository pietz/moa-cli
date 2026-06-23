# 018 - Load config once per run in `distill`/`debate`

**Status:** ready
**Touches:** `src/moa_cli/cli.py`, `tests/test_cli.py`.
**Related:** 008 (persistent config), 003 (synthesizer restriction - settled the
selected-provider check this builds on).

## Context

`resolve_run` (`cli.py:256`) calls `load_config()` to read and validate
`~/.moa/config.toml`. Then `distill` (`cli.py:385`) and `debate` (`cli.py:496`) each
call `_read_config_or_empty()` again to resolve the synthesizer/moderator option - and
`_read_config_or_empty()` (`config.py:230-234`) calls `load_config()` a second time but
**swallows** `ValueError` to `{}`, whereas `resolve_run` raises.

So the two lookups observe the config under different failure semantics: a malformed
file raises on the first read (good) but, if somehow survived or for the
synthesizer/moderator resolution, the second read silently defaults. At minimum it is
duplicated I/O + validation; at worst it is a subtle inconsistency on the error path.

## Goal

One config load per run, with one consistent error path.

## Decisions

- Have `resolve_run` keep the already-loaded, already-validated config dict and expose
  it on `RunConfig` (e.g. add a `config: dict` field), instead of each verb re-loading.
- Resolve `synthesizer` (distill) and `moderator` (debate) from that same dict via
  `resolve_option(..., cfg.config, "auto")`, dropping the `_read_config_or_empty()`
  calls at `cli.py:385` and `cli.py:496`.
- `_read_config_or_empty()` can stay in `config.py` (it may have other uses) - this
  ticket just stops calling it from the verb paths.
- Preserve current behavior: the synthesizer/moderator still default to `"auto"` when
  absent, and a malformed config still raises via `resolve_run`'s existing
  `load_config()` call (in fact it now raises consistently rather than
  sometimes-swallowing).

## Acceptance criteria

- [ ] `load_config()` is called exactly once per `distill`/`debate`/`ask` run
      (verifiable by monkeypatching `load_config` with a counter in a test).
- [ ] `synthesizer`/`moderator` are resolved from the same config dict as
      `num`/`timeout`/`exclude`/`models`/`efforts`.
- [ ] A malformed config raises the same `BadParameter` for synthesizer/moderator
      resolution as it does for the rest of the run (no silent-default path).
- [ ] Existing `distill`/`debate` config tests in `tests/test_cli.py` and
      `tests/test_config.py` still pass.
- [ ] `uv run pytest` and `uv run ruff check src tests` pass.

## Notes

This is low-risk but touches the verb entry points, so run the full
`tests/test_cli.py` suite. If `RunConfig` gaining a `config` field complicates the
`@dataclass(frozen=True)` shape, prefer stashing the dict on the field over threading
it through every helper signature.

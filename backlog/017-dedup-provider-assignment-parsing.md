# 017 - Deduplicate `PROVIDER=VALUE` parse-and-validate

**Status:** ready
**Touches:** `src/moa_cli/cli.py`, `tests/test_cli.py`, `tests/test_config.py`.
**Related:** 008 (persistent config), 011 (effort config, which added the third copy).

## Context

The "split on `=`, strip, validate the provider name against `PROVIDERS`, raise
`typer.BadParameter`" pattern is implemented three times in `cli.py`, and the copies
have already drifted:

1. `parse_model_overrides` (`cli.py:118-133`) - accepts an **empty** model value.
2. `config_set` "model" branch (`cli.py:687-699`) - same shape, different error wording
   ("Unknown provider:" vs "Unknown provider in --model:").
3. `config_set` "effort" branch (`cli.py:700-714`) - same shape again, but **rejects**
   an empty value (`cli.py:712-713`) and additionally checks `effort_flag is None`.

Any new `PROVIDER=VALUE` config option (e.g. a future per-provider setting) adds a
fourth copy.

## Goal

One helper that owns the split/strip/validate/raise logic, called from all three sites,
so they cannot drift further.

## Decisions

- Add a helper, e.g.
  `parse_provider_assignment(value: str, *, what: str, allow_empty: bool) -> tuple[str, str]`
  that:
  - raises `typer.BadParameter` if `=` is absent (message templated on `what`, e.g.
    "effort expects PROVIDER=VALUE, ..."),
  - splits on the first `=`, strips both sides,
  - validates the provider against `PROVIDERS` (one consistent error message,
    parameterized by `what`),
  - raises `typer.BadParameter` if the value is empty and `allow_empty=False`.
- Route all three existing sites through it:
  - `parse_model_overrides`: `allow_empty=True` (preserve current behavior).
  - `config_set` "model": `allow_empty=True`.
  - `config_set` "effort": `allow_empty=False`; the `effort_flag is None` note stays
    at the call site (it is about the resolved `Provider`, not the parse).
- Do not change observable error wording for cases currently covered by tests without
  updating those tests in the same change.

## Acceptance criteria

- [ ] Exactly one code path performs the split/strip/`PROVIDERS` check; all three call
      sites use it.
- [ ] Existing tests in `tests/test_cli.py` and `tests/test_config.py` for
      `--model`, `config set model`, and `config set effort` (valid, unknown provider,
      missing `=`) still pass.
- [ ] Empty-value handling is consistent within each option's documented contract
      (model allows empty, effort rejects empty) and pinned by a test.
- [ ] `uv run pytest` and `uv run ruff check src tests` pass.

## Notes

This is a pure refactor. Behavior is preserved; only the error wording may converge to
the helper's single template. If the convergence breaks a test that asserted on exact
wording, update the assertion to the new shared message.

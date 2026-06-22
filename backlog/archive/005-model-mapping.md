# 005 - Tool -> model mapping (defaults + override)

**Status:** done
**Touches:** `src/moa_cli/cli.py` (PROVIDERS defaults, `-m` parsing), `tests/test_moa.py`, `README.md`

## Goal

Each tool can run more than one model (e.g. `agy` hosts Gemini *and* Claude/GPT-OSS;
`opencode` runs any `provider/model` the user has authed). Ship a reasonable
default model per tool, but let the user override which model a tool uses.

## Decisions (from the user)

- Reasonable **defaults** per tool (already in `PROVIDERS.default_model`).
- **Customizable** per tool.

## Current defaults

| Provider | Default model                | Notes |
|----------|------------------------------|-------|
| claude   | `opus`                       | `--model` |
| codex    | `gpt-5.5`                    | `-m` |
| agy      | `Gemini 3.1 Pro (High)`      | exact display string incl. spaces/parens |
| opencode | (none - user's authed default) | `-m provider/model`; omit to use config |

## Design (proposal)

- Add `--model / -m PROVIDER=MODEL`, repeatable. E.g.
  `moa ask -m claude=sonnet -m agy="Claude Opus 4.6 (Thinking)"`.
- Overrides merge over `PROVIDERS.default_model`; unset providers keep defaults.
- Each builder already takes a `model` arg, so plumbing is mostly CLI parsing +
  passing the resolved model into `run_provider` (which currently hardcodes
  `provider.default_model`).
- Model-string formats differ per tool (agy = display name; opencode = slug;
  claude/codex = short id). Pass through verbatim; the underlying CLI validates.
- Unknown provider key in a mapping -> `BadParameter`.

## Open questions

- **Config file** (`~/.config/moa/config.toml` or similar) for persistent
  per-tool model defaults, vs CLI-flag-only for v1? Recommend: **CLI flag first**,
  add a config file later if users want persistence. (Confirm with user.)
- Interaction with debate/synthesis modes: which model does the
  synthesizer/aggregator use - its default, or overridable too? (Likely yes,
  same mechanism.)

## Acceptance criteria

- [x] `-m provider=model` repeatable; overrides the default for that provider only.
- [x] `run_provider` uses the resolved model (not always `default_model`).
- [x] Heading/JSON report the actual model used.
- [x] Unknown provider in a mapping raises a clear error.
- [x] Tests: override applied, default preserved for others, bad key errors.
- [x] README documents `-m` and the per-tool model-string formats.

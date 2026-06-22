# 008 - Persistent config / default settings

**Status:** building (on a side branch; integrate into 0.2.0 after 004)
**Touches:** `src/moa_cli/cli.py` (config load + merge into option defaults, new
`moa config` command), `tests/test_moa.py`, `README.md`
**Supersedes:** the deferred "config file" question in item 005 (model overrides).

## Goal

Let users persist their own defaults (always ask 2 agents, always exclude claude,
pin certain models) so they don't repeat flags on every call.

## Precedence (decided design)

```
built-in default   <   config file   <   CLI flag
```

A CLI flag always wins; the config file only changes the default when the flag is
omitted. Absent config == today's built-in behavior.

## Location & format

- `~/.moa/config.toml` (create dir/file on first `set`). A `~/.moa/` home dir matches
  the sibling AI CLIs (`~/.claude`, `~/.codex`) and leaves room for future files.
  Resolve the dir via a helper that honors `$MOA_CONFIG_DIR` if set, so tests use a temp dir.
- TOML. Read with stdlib `tomllib` (3.11+, no dependency). Writes via a small
  purpose-built serializer for our flat schema (zero new deps) - or `tomli-w` if preferred.

## Persistable keys (shared across verbs)

`num`, `timeout`, `exclude` (list), `synthesizer`, and a `[models]` table
(provider -> model). Mode-specific keys (debate `rounds`/`judge`) optional later via
`[debate]` sections.

```toml
num = 2
timeout = 120
exclude = ["claude"]
synthesizer = "auto"

[models]
claude = "sonnet"
agy = "Gemini 3.1 Pro (Low)"
```

## Command surface

- `moa config show`  - effective config (defaults + file merged) + file path.
- `moa config path`  - print the file path.
- `moa config set <key> <value>`  - e.g. `moa config set num 2`; writes the file.
  Lists: `moa config set exclude claude,codex`. Models: `moa config set model claude=sonnet`.
- `moa config unset <key>`.

## Open sub-decisions (leans)

- Writer: **hand-rolled TOML** (zero dep) vs `tomli-w`. Lean hand-rolled.
- **Env-var layer** (`MOA_NUM`, ... between config and flags)? Lean: skip v1, easy to add.
- **Project-local `.moa.toml`** overriding user config? Lean: defer to a later item.

## Acceptance criteria

- [ ] Config file loaded and merged under CLI flags for all verbs.
- [ ] `moa config show/path/set/unset` work; `set` creates dir/file if missing.
- [ ] Flags always override config; absent config = current built-in defaults.
- [ ] Tests: precedence (flag > config > default), set/unset round-trip, list + `[models]` parsing.
- [ ] README: a "Configuration" section with file location, keys, and examples.

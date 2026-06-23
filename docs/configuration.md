# Configuration reference

MOA reads persisted defaults from `~/.moa/config.toml` for every verb and merges
them under your CLI flags. This page is the full reference; the README keeps the
short version.

## Location

`~/.moa/config.toml` (the dir is created on first write). Set `$MOA_CONFIG_DIR`
to point the whole config layer somewhere else (useful in tests/CI).

## Precedence

`built-in default  <  config file  <  CLI flag`. A flag always wins; the config
file only changes a default when that flag is omitted; an absent file means
today's built-in behaviour.

## Keys

All keys are shared across `ask` / `distill` / `debate`.

| Key                  | Type                     | Example                       |
| -------------------- | ------------------------ | ----------------------------- |
| `num`                | int (>= 1)               | `num = 2`                     |
| `timeout`            | seconds (> 0)            | `timeout = 120`               |
| `exclude`            | list of provider names   | `exclude = ["claude"]`        |
| `synthesizer`        | `auto` / `random` / provider | `synthesizer = "codex"`   |
| `moderator`          | `auto` / provider        | `moderator = "agy"`           |
| `[providers.<name>]` | per-provider `model` + `effort` | see below              |
| `[models]`           | DEPRECATED provider -> model table | `claude = "sonnet"` |

```toml
# ~/.moa/config.toml
num = 2
timeout = 120
exclude = ["claude"]
synthesizer = "auto"

[providers.codex]
model = "gpt-5.5"
effort = "high"

[providers.opencode]
model = "zai-coding-plan/glm-5.2"
effort = "high"
```

Model and effort are grouped per provider under `[providers.<name>]`. The flat
`[models]` table still works as a **deprecated alias** for
`[providers.<name>].model`; when both set a model for the same provider, the
`[providers.<name>]` block wins (MOA prints a one-line note, not an error).

The role defaults are persistable too: the distill `synthesizer` and the debate
`moderator` (e.g. `moa config set synthesizer codex`, `moa config set moderator
agy`). `debate`'s `-r/--rounds` is not persisted. CLI `-m` overrides win
per-provider over the config model.

## `moa config`

Inspect and edit the file (creates the dir/file as needed, validates provider
names).

```bash
moa config show                       # effective config (defaults + file) + path
moa config path                       # print the config file path
moa config set num 2                  # set a scalar
moa config set exclude claude,codex   # set the exclude list (comma-separated)
moa config set model codex=gpt-5.5    # set a provider's model
moa config set effort codex=high      # set a provider's reasoning effort
moa config unset num                  # remove a key
moa config unset model codex          # remove one provider's model
moa config unset effort codex         # remove one provider's effort
```

## Reasoning / effort

Pin a per-provider **reasoning/effort** level in config so the council runs
each tool at the depth you want without repeating flags. This is **config-only**:
there is intentionally no `-e/--effort` CLI flag.

MOA uses **raw pass-through with zero value mapping.** It does not normalize
effort across providers or invent a canonical low/med/high scale. You write the
**exact value the target tool expects**, and MOA pastes it verbatim into that
provider's native flag. The only thing MOA maps is *where* the value lands in
each provider's argv, never the value itself:

| Provider   | `effort` lands in                    | Notes                                                       |
| ---------- | ------------------------------------ | ----------------------------------------------------------- |
| `codex`    | `-c model_reasoning_effort=<value>`  | generic config override                                     |
| `opencode` | `--variant <value>`                  | opencode's "model variant (provider-specific reasoning effort)" |
| `agy`      | (none)                               | reasoning is part of the model name, e.g. `Gemini 3.1 Pro (High)` |
| `claude`   | (none)                               | no per-call effort flag                                     |

Values are **tool-specific and not validated** by MOA (only "non-empty if
present"): a value the target tool rejects fails at that tool, not in MOA. When
no effort is configured for a provider, MOA passes **no effort flag at all**, so
the tool's own default stands. Setting `effort` for `agy`/`claude` is stored but
inert (they have no effort flag); MOA notes this when you set it.

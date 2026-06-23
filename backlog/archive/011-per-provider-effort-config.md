# 011 - Per-provider reasoning/effort in config (raw pass-through)

**Status:** done (shipped in 0.3.2; fresh-eyes review passed)
**Touches:** `src/moa_cli/cli.py` (Provider build path + config load/serialize),
`tests/test_moa.py`, `README.md`
**Related:** 005 (model mapping), 008 (persistent config), 010 (doctor check can
display the resolved effort).

## Goal

Let a user pin a **reasoning/effort** level per provider in config, alongside the
model, so the council can run e.g. opencode/GLM at high effort and codex at medium
without repeating anything. The model's own default stands when nothing is set.

## Decided design (do not re-litigate)

- **Config-only. No CLI flag.** There is intentionally no `-e/--effort` on-the-fly
  override. Effort is a persisted preference, not a per-call knob.
- **Raw pass-through, zero value mapping.** moa does **not** normalize effort across
  providers and does **not** invent a canonical low/med/high scale. The user writes
  the **exact wording/value the target tool expects**, and moa pastes it verbatim
  into that provider's native flag.
- **The only thing moa maps is the variable -> flag location** (where our `effort`
  value goes in each provider's argv). It never interprets the value.
- **Omit when unset.** No effort configured for a provider => moa passes no effort
  flag at all => the model/tool default is used. Same omit-when-empty rule the
  `[models]` table already uses.

## Per-provider flag mapping (the whole table moa must track)

| Provider   | `effort` lands in                                  | Notes |
| ---------- | -------------------------------------------------- | ----- |
| `codex`    | `-c model_reasoning_effort=<value>`                | generic config override; **verify the exact key against the installed codex** during build |
| `opencode` | `--variant <value>`                                | help: "model variant (provider-specific reasoning effort, e.g., high)" |
| `agy`      | (none) - reasoning is part of the **model name**   | already settable via the existing `model` field, e.g. `Gemini 3.1 Pro (High)`. Do **not** add a separate effort knob for agy. |
| `claude`   | (none) - no clean per-call effort flag             | omit for v1; verify and revisit if one exists. |

Implement this like `perm_args`: data, not branching. Give `Provider` an
`effort_args(value) -> tuple[str, ...]` (empty tuple when the provider has no
mapping or value is unset) and thread the resolved effort into `build()` /
`run_provider` next to `model`. Keep agy/claude returning `()`.

## Config shape (decided)

Per-provider blocks, model and effort grouped together:

```toml
[providers.codex]
model = "gpt-5.5"
effort = "high"        # -> -c model_reasoning_effort=high

[providers.opencode]
model = "zai-coding-plan/glm-5.2"
effort = "high"        # -> --variant high
```

- Keep the existing flat `[models]` table working as a **deprecated alias** for
  `[providers.<name>].model` (back-compat with 008/0.3.x configs). When both set a
  model for the same provider, `[providers.<name>].model` wins; surface a one-line
  note, not an error.
- `effort` is a string, validated only as "non-empty if present" (no enum check -
  the value space is provider-defined and we refuse to police it).
- `moa config set effort opencode=high` and `moa config unset effort opencode`
  mirror the existing `model` set/unset surface.

## Acceptance criteria

- [ ] `[providers.<name>]` blocks parse: `model` + `effort` per provider; `[models]`
      still works as a deprecated alias (provider-block model wins on conflict).
- [ ] Resolved effort is pasted verbatim into the right flag: codex
      `-c model_reasoning_effort=<v>`, opencode `--variant <v>`; agy/claude emit
      nothing. Unset => no effort flag at all.
- [ ] No CLI effort flag exists (config-only, by design).
- [ ] `config set/unset effort PROVIDER[=VALUE]` round-trips; `config show` prints
      effort under each provider.
- [ ] Tests: argv assertions per provider (set vs unset), `[models]` alias +
      conflict precedence, set/unset round-trip, omit-when-unset. Stub subprocess
      as existing build/run tests do.
- [ ] README: "Reasoning / effort" subsection under Configuration stating the
      raw-pass-through rule and the per-provider flag table; call out that values
      are tool-specific and not normalized.

## Open sub-decisions (leans)

- Variable name: `effort` (lean) vs `reasoning`. Matches the user's wording and the
  tools (`model_reasoning_effort`, opencode "reasoning effort"). Lean `effort`.
- Should `config show` / `doctor --check` (010) display the resolved effort? Lean
  yes for `doctor --check`, so a misfit value surfaces before a real run.

## Notes

The drift risk is real and accepted: provider flag names (not values) can change.
Contain it by (a) keeping the mapping as a tiny per-provider data table in one
place, and (b) verifying each flag against the installed CLI's `--help` during the
build, not from memory. Do not add value validation or scale translation - that is
explicitly out of scope and was rejected in design.

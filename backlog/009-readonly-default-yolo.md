# 009 - Read-only by default + `--yolo` override (0.1.1 patch)

**Status:** ready (flags verified; agy policy decided)
**Target:** 0.1.1 patch - fixes a safety gap shipped in 0.1.0.
**Touches:** `src/moa_cli/cli.py` (Provider permission map, runner, `--yolo` flag, selection, doctor), `tests/test_moa.py`, `README.md`

## Context

moa asks coding CLIs for an opinion and is meant to be called autonomously by other
agents. A reviewer confirmed `opencode` ran with full auto-approved file-edit + shell access.

## Decision (from the user)

- **Default = no write access.** Tools may read files and research online, but must
  not edit files or run mutating commands.
- **`--yolo`** opt-in flag grants all tools full write access.
- Keep it **uncluttered**: a structured per-provider permission map, not per-tool
  special-casing in the builders.
- **Tools with no read-only mode are excluded from the default (safe) panel** - do
  NOT complicate moa with config-file hacks to force them. They return only under `--yolo`.

## Design: structured permission map

Each `Provider` declares permission flags as data:
- `readonly`: argv to splice in for the safe default (`None` if the tool has no read-only mode).
- `yolo`: argv to splice in under `--yolo` (full access).
The runner selects `readonly` (default) or `yolo` (`--yolo`) and inserts it into the
command before the prompt. `readonly is None` => the tool can't be sandboxed.

## Per-tool flags (VERIFIED against installed binaries)

| Tool | readonly (default) | yolo (`--yolo`) |
|------|--------------------|------------------|
| claude   | `--permission-mode plan` | `--permission-mode bypassPermissions` |
| codex    | `-s read-only`  (NOT `-a` - rejected by `exec`) | `-s danger-full-access` |
| opencode | `--agent plan` (+ pin `-m provider/model`) | default `build` agent |
| agy      | **none exists** | default (full access) |

- **agy:** no flag sandboxes it (`--sandbox` does NOT stop its `write_file` tool -
  verified). So `readonly = None` -> excluded from the default panel; runs only under `--yolo`.
- **codex caveat:** `-s read-only` is a kernel sandbox that also blocks network, so codex
  won't do web research in default mode (it still reads local files). Acceptable for the patch.
- The builder must **verify the `--yolo` flags live** before shipping.

## Also in this patch (reviewer's minor points)

- `moa doctor` shows each provider's **default model** instead of the now-redundant
  executable: `claude (opus)`, `codex (gpt-5.5)`, `agy (Gemini 3.1 Pro (High))`,
  `opencode (configured default)`. Flag agy as "no read-only - default-excluded".

## Acceptance criteria

- [ ] `Provider` gains a structured `readonly` / `yolo` permission map; the runner splices the right set in.
- [ ] Default run applies `readonly` flags; `--yolo` applies `yolo` flags to all providers.
- [ ] Tools with no read-only mode (agy) are excluded from the default panel and run only under `--yolo`; the stderr selection note says so.
- [ ] `--yolo` flags verified against the installed CLIs.
- [ ] `doctor` shows default models (and flags agy as default-excluded).
- [ ] Tests: default vs `--yolo` argv per provider; agy excluded by default / present under `--yolo`.
- [ ] README documents the read-only default, what each tool can/can't do, agy's exclusion, and `--yolo`.
- [ ] Version bump to 0.1.1.

## Notes

Build on `main` (008 is isolated on its own branch). Run the full loop: builder +
a separate fresh-eyes reviewer (security fix).

# 013 - Strict read-only mode (`--read-only`)

**Status:** proposed
**Touches:** `src/moa_cli/cli.py` (a third permission tier + a `--read-only`/`-r`
flag on the prompt verbs), `tests/test_moa.py`, `README.md`, `docs/cli-permission-modes.md`.
**Related:** 009 (the original read-only + --yolo design, now being relaxed),
`docs/cli-permission-modes.md` (per-tool flag matrix).

## Context

moa originally ran every agent in strict read-only by default, with `--yolo` for
full access. We're relaxing that: **default** now uses each tool's *normal /
recommended* mode (e.g. claude `--permission-mode default`), and **yolo** stays.
This ticket adds back a strict read-only as an **explicit opt-in** for when the
user wants a hard "cannot write/mutate" guarantee (code review, untrusted repos,
running broadly across many agents).

So the permission spectrum becomes: `--read-only  <  (default)  <  --yolo`.

## Goal

A `--read-only` / `-r` flag on `ask`/`distill`/`debate` that spawns each agent in
its strictest no-write mode. Mutually exclusive with `--yolo` (error if both).
This is the third tier the `Provider` permission model must express.

## Decisions

- The `Provider` model currently has `readonly`/`yolo` flag tuples (and
  `readonly_note`). Generalize to three tiers (e.g. a `readonly` / `default` /
  `yolo` set of flag tuples) so each verb can pick one. `default` is the new
  no-flag behaviour; `--read-only` selects the strict set; `--yolo` the full set.
- Per-tool strict read-only flags (verify against `docs/cli-permission-modes.md`):
  - **claude**: `--permission-mode default` already blocks writes headless; for a
    *hard* guarantee use `dontAsk` + a read-only `--allowedTools` allowlist.
  - **codex**: `-s read-only` (kernel sandbox; true read-only).
  - **opencode**: `--agent plan` (read-only agent).
  - **agy**: `--sandbox` is only **partial** (shell only; `write_file` still
    writes). agy has no true read-only mode — keep the honest stderr
    `readonly_note` so the user knows it isn't a hard guarantee.
- Keep the honest partial-protection note (`readonly_note`) surfacing on stderr
  under `--read-only`, exactly as the old read-only default did.

## Acceptance criteria

- [ ] `--read-only`/`-r` on `ask`/`distill`/`debate` runs every selected agent in
      its strict no-write mode; argv per provider matches the matrix above.
- [ ] `--read-only` and `--yolo` together is a clean `BadParameter` error.
- [ ] The stderr selection note states the mode (`read-only`) and still surfaces
      agy's partial-protection note.
- [ ] Default (no flag) and `--yolo` behaviour are unchanged by this ticket.
- [ ] Tests: argv per provider in read-only mode; the mutually-exclusive error;
      the partial-protection note still fires for agy.
- [ ] README permission section documents all three modes; `docs/cli-permission-modes.md`
      read-only rows are confirmed accurate.

## Notes

Depends on the default-flag remap landing first (the work that moves `default` off
strict read-only for codex/opencode/agy). This ticket re-introduces the strict
tier behind an explicit flag rather than as the default.

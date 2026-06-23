# 014 - Fix stale permission-mode docs

**Status:** ready
**Touches:** `README.md`, `docs/cli-permission-modes.md`.
**Related:** 013 (strict read-only mode, whose end-state leaked into current docs), 009
(read-only + --yolo design).

## Context

The code drifted from the docs and the docs were not updated. Three concrete drifts,
all on the security-relevant "what flags does moa actually spawn" surface:

1. `src/moa_cli/providers.py:109` sets claude's default to `--permission-mode default`
   (moved off `plan` in commit `b92c411`), but `README.md:272` (Supported agents table)
   still shows `claude --model opus --permission-mode plan -p PROMPT`. This even
   contradicts the README's own correct table at `:95`.
2. `README.md:273` shows the codex invocation without the `--color never` that
   `_codex` always inserts (`providers.py:68-69`).
3. `README.md:277` says adding a provider is "a single entry in the `PROVIDERS` table
   in `src/moa_cli/cli.py`" - the table moved to `src/moa_cli/providers.py` during the
   module split, and it is not a single entry (see also the rejected "plugin system").
4. `docs/cli-permission-modes.md:22-32` tables ticket 013's *unshipped* default tier
   (codex `-s workspace-write`, opencode `--agent build`) as the live "default (new)"
   column, with a "Heads-up ... deliberate shift away from the old read-only default"
   that reads as released behavior. The actual code default (`providers.py:118,139`) is
   still `-s read-only` (codex) and `--agent plan` (opencode). The doc describes 013's
   end-state as the present.

## Goal

Make the docs describe the **current** 2-tier reality (default = the `readonly` tuples
in `providers.py`, which for codex/opencode ARE strict read-only; `--yolo` = full
access), and move ticket-013 material into a clearly labelled "planned, not yet
shipped" block.

## Decisions

- **README "Supported agents" table (`:270-277`):**
  - claude row: `--permission-mode default` (matching the table at `:95` and the tests
    in `tests/test_providers.py`).
  - codex row: include `--color never` (and optionally note the `-o` temp-file path
    implied by `uses_output_file=True`).
  - "Adding a new agent" sentence: point at `src/moa_cli/providers.py` (not `cli.py`)
    and drop the "single entry" claim - it is currently three coordinated edits.
- **`docs/cli-permission-modes.md`:**
  - Rewrite the "## moa's mapping (current direction)" section so the per-tool
    "default" column matches `providers.py` today (claude `--permission-mode default`,
    codex `-s read-only`, opencode `--agent plan`, agy `--sandbox`).
  - Move the `workspace-write` / `--agent build` / `dontAsk+allowlist` rows into a
    separate "Planned via ticket 013 (not yet shipped)" block so they are no longer
    presented as the live default.
  - The per-tool research sections below (claude/codex/opencode/agy detail) are
    accurate reference material and stay as-is.

## Acceptance criteria

- [ ] README "Supported agents" claude row says `--permission-mode default`; codex row
      includes `--color never`; the "single entry ... src/moa_cli/cli.py" sentence is
      corrected to `providers.py` and no longer claims a single entry.
- [ ] `docs/cli-permission-modes.md` "default" column matches the actual `readonly=`
      tuples in `providers.py` for all four providers.
- [ ] Ticket-013 future tiers are clearly labelled as unshipped in that doc.
- [ ] No behavior change; `uv run pytest` and `uv run ruff check src tests` still pass
      (docs-only edit, but verify nothing regressed).

## Notes

Pure docs edit, no code change. Cross-check each table row against
`src/moa_cli/providers.py` (`readonly=`, `yolo=`, `build()` fns) and the provider tests
rather than against the other docs, so the fix converges on one source of truth.

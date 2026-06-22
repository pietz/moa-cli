# 003 - Self-exclusion / provider exclusion

**Status:** done
**Touches:** `src/moa_cli/cli.py` (selection flow), `tests/test_moa.py`, `README.md`

## Goal

Let the caller drop one or more providers from a run. The motivating case: an
agent (e.g. Claude Code) calls `moa` to get *other* opinions; if `moa` also runs
`claude`, one "peer" is just the caller's own model, defeating the point.

## Decisions (from the user)

- The feature exists, but is **default off** (no provider excluded unless asked).

## Design

- Add `--exclude / -x PROVIDER` (repeatable). Excluded providers are removed from
  the priority list *before* `-n` takes the first N installed. So
  `moa ask -n 3 -x claude` queries codex, agy, opencode.
- Exclusion composes with explicit `-p/--provider` pinning (excluded names are
  dropped from the pinned set too).
- Unknown provider name in `--exclude` -> `BadParameter`, consistent with `-p`.

## Optional enhancement (flag, but propose to user before building)

- **Auto self-detect:** detect the calling agent from the environment (e.g.
  `CLAUDECODE` set => the caller is Claude Code) and offer to exclude it. Keep
  this OFF by default per the user's decision; expose as e.g. `--exclude-self`
  rather than changing default behavior. Mark as a follow-up, not part of v1.

## Acceptance criteria

- [x] `--exclude/-x` repeatable; excluded providers never run.
- [x] Applies before `-n` selection and to explicit `-p` sets.
- [x] Default: nothing excluded.
- [x] Unknown excluded name raises a clear error.
- [x] stderr selection note reflects exclusions.
- [x] Tests: exclusion + `-n`, exclusion + `-p`, unknown-name error.
- [x] README documents the flag and the peer-review motivation.

# 007 - Agent skill for skills.sh

**Status:** proposed
**Depends on:** 004 (so the skill can use the `ask`/`fuse`/`debate` verbs). Can ship
a first version against `moa ask` today and extend once 004 lands.

## Goal

Publish a self-contained Claude agent skill (a `SKILL.md`, like the existing
`peer-review` skill) that wraps `moa` and is listable on **skills.sh**. It lets any
agent get a mixture-of-agents second opinion by calling `moa` instead of
hand-orchestrating parallel `claude -p` / `codex exec` / ... calls - and it walks a
first-time user through setup.

## Requirements

- **Self-contained:** a single skill directory (SKILL.md + optional notes), no
  dependencies beyond `moa` itself. Installable/listable from skills.sh.
- **Initial-setup walkthrough** (the skill guides this on first use):
  1. Install moa: `uv tool install moa-cli` (or run via `uvx --from moa-cli moa ...`).
  2. Run `moa doctor` to see which agent CLIs are installed.
  3. Ensure at least 2 peer CLIs are installed AND authed (claude / codex / agy /
     opencode); link each tool's install + login.
  4. Self-exclusion: the orchestrating agent passes `-x <its-own-model>` so a
     "peer" isn't just itself (e.g. Claude Code -> `-x claude`).
- **Usage guidance for the agent:**
  - Default second opinion: `moa ask --json -x <self> "<fully self-contained prompt>"`,
    then parse the JSONL records.
  - Merged answer: `moa fuse ...`; structured disagreement: `moa debate ...` (post-004).
  - Re-brief fully each call (the CLIs are stateless); report which models responded
    / failed before synthesizing - mirrors the old peer-review skill.
- **skills.sh metadata:** name + one-line description tuned for discovery/triggering
  ("get a second opinion from multiple AI models", etc.).

## Acceptance criteria

- [ ] Self-contained skill dir with a SKILL.md that triggers on "second opinion /
      peer review / ask multiple models" intents.
- [ ] Setup section that takes a user from zero to a working `moa doctor`.
- [ ] Usage examples for `ask` (and `fuse`/`debate` once 004 lands), using `--json`
      and `-x` for self-exclusion.
- [ ] Submitted/listed on skills.sh (follow their submission format).
- [ ] Supersedes the hand-rolled `peer-review` skill (note the migration).

## Notes

This is a *distribution* item, not core CLI code - likely lives in its own skill
dir, not in `src/`. Decide whether to ship it inside this repo (e.g. `skill/`) or as
a separate submission.

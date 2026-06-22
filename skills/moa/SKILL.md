---
name: moa
description: Get a second opinion from multiple AI models at once. Use this whenever you're stuck, want to validate an approach or design, pressure-test a plan or a claim, or want diverse cross-model viewpoints on a hard decision - i.e. anything you'd reach for "peer review", "ask another model", or "council of models" for. Wraps the `moa` CLI, which fans one prompt out to the local agent CLIs (Claude Code, Codex, agy, opencode) in parallel and returns each answer with attribution.
metadata:
  version: "1.0.0"
---

# moa - mixture of agents

`moa` asks one question to several local AI coding CLIs **in parallel** and collects
their answers with attribution. It is the drop-in replacement for hand-rolling parallel
`claude -p` / `codex exec` / `agy -p` calls: one command, clean output, built to be
called by an agent. Reach for it when one model's answer isn't enough and you want to see
where independent models agree, diverge, or contradict.

It has three verbs that share the same flags:

- **`moa ask PROMPT`** - council / peer review. N agents answer the same prompt in
  parallel; every answer streams back with attribution as it lands. **This is the default.**
- **`moa distill PROMPT`** - council, then one strong aggregator merges the answers into a
  single unified response. Use when you want *one* synthesized answer, not N to read.
- **`moa debate PROMPT`** - sequential adversarial rounds, then a neutral judge writes the
  verdict. The costliest and least reliably-beneficial mode - use only to surface and
  stress-test disagreement (see the caveat below).

## First-time setup

Run this once on a machine; skip it if `moa doctor` already lists two or more agents.

1. **Install moa:** `uv tool install moa-cli` (installs the `moa` command). Or run it
   without installing: `uvx --from moa-cli moa ask "..."`.
2. **Check the panel:** `moa doctor` prints which agent CLIs are installed and their
   default models.
3. **Need at least two peers.** moa drives whichever of these are installed AND
   logged in: `claude` (Claude Code), `codex` (OpenAI Codex), `agy` (Google Antigravity),
   `opencode`. If fewer than two are present, install + auth one more so the panel is
   actually diverse - a "council" of one is just the model you already have. Each tool's
   own `login`/auth flow applies; moa uses the CLIs you already pay for and needs no API
   keys of its own.

## Self-exclusion rule (important)

If **you are an agent** calling moa for *other* opinions, exclude your own model so a
"peer" isn't just yourself: pass `-x <your-provider>`. For example, Claude Code should
call `moa ask -x claude "..."`. Map yourself to the right provider name: Claude -> `claude`,
Codex/GPT -> `codex`, Gemini -> `agy`. When in doubt, run `moa doctor` and exclude the one
that is you.

## How to use it

The CLIs are **stateless** - they have no memory of your conversation. Write a fully
self-contained prompt every call: state the question, paste the relevant code/plan/diff,
and say what a good answer looks like. Then pick a verb:

- **Second opinion (default):** `moa ask --json -x <self> "<self-contained prompt>"` and
  parse the JSONL. This is the right choice the vast majority of the time.
- **One merged answer:** swap `ask` for `distill` when you want the models reconciled into
  a single response instead of reading each one.
- **Stress-test a disagreement:** use `debate` only when the *point* is to surface where
  models disagree and have them argue it out.

`--json` emits one JSON object per line (JSONL), ideal when an agent parses the result:

- `ask` -> one `{"type":"response", "provider","model","status","text",...}` per agent.
- `distill` -> a single `{"type":"synthesis","text",...}` (only the merged answer; the
  individual proposer responses are intermediates and are not emitted).
- `debate` -> a `{"type":"debate_turn","round":N,...}` per turn, then `{"type":"verdict",...}`.

Without `--json`, answers print on stdout under a labelled heading; progress/selection notes
go to stderr, so piping stdout stays clean. In a terminal the heading is a box-drawing rule
(`──── claude (opus) · OK · 3.5s ────`); when piped (the agent case) it's a plain `## ...`
heading. Prefer `--json` when parsing programmatically.

**Read-only by default.** Every agent runs in its tool's safest mode and cannot edit files
or run mutating commands. Pass `--yolo` only when you actually want the panel to change your
working tree. (Caveat: `agy` has no true read-only mode - moa shell-sandboxes it but it can
still edit files; moa says so honestly on stderr.)

### Selecting the panel

- `-n/--num N` - ask the top N installed agents in priority order (`claude` -> `codex` ->
  `agy` -> `opencode`). Default 3.
- `-p/--provider NAME` (repeatable) - pin an exact set, ignoring `-n`.
- `-x/--exclude NAME` (repeatable) - drop agents (use for self-exclusion).
- `-m/--model PROVIDER=MODEL` (repeatable) - override a tool's model (e.g. `claude=sonnet`).
- `-t/--timeout SECONDS` - per-agent timeout.
- `-f/--file PATH` (or `-f -` for stdin) - read the prompt from a file/pipe.

`distill` adds `-s/--synthesizer` (`auto` | `random` | a provider). `debate` adds
`-r/--rounds` (default 2, max 4) and `-j/--judge PROVIDER` (must not be a debater; debate
needs at least 3 agents: 2 debaters + 1 judge). Defaults persist in `~/.moa/config.toml` via
`moa config set ...` so you don't repeat flags.

## Reporting results

Mirror what a good peer review does: **be transparent about the panel before you
synthesize.** Tell the user which providers answered, which failed or timed out (check
`status` in the JSON), then summarize where they agree, disagree, and any unique insight
each contributed. Don't silently drop a model that errored - say so and continue with what
you have.

## Examples

**1. Validate an architecture decision (you are Claude, so exclude yourself):**

```bash
moa ask --json -x claude "I'm building a desktop note-taking app for ~10k local notes. \
Should I store them as SQLite or as flat Markdown files? Here are my constraints: offline-first, \
full-text search, sync via the user's own Dropbox. Give a recommendation with tradeoffs."
```

**2. Get one merged answer on a design, reading the prompt from a file:**

```bash
moa distill -x claude -f design-notes.md
# council answers, then the top remaining agent merges them into one synthesized design.
```

**3. Review a diff for bugs with a pinned panel:**

```bash
git diff | moa ask -x claude -p codex -p agy -f - \
  "Review this diff for correctness bugs, race conditions, and security issues. \
Be specific about line and reasoning."
```

**4. Stress-test a contested claim (use debate sparingly):**

```bash
moa debate -x claude "Is the lock ordering in this scheduler actually deadlock-free? \
<paste the two functions and the lock acquisition order>"
# 2 debaters argue across rounds; a separate neutral judge writes the verdict.
```

## Caveat on `debate`

Debate is the costliest mode (~`debaters × rounds + 1` model calls) **and the least
reliably beneficial.** The research is mixed-to-negative: a multi-agent debate can converge
on a *wrong* answer through conformity, and a confident-but-incorrect debater can win on
persuasiveness. The neutral judge and adversarial-stance prompt fight this but don't
eliminate it. For almost everything, `ask` or `distill` is the better default; reach for
`debate` only when surfacing disagreement is the actual goal.

---

*This skill supersedes the hand-rolled `peer-review` skill: instead of orchestrating
parallel Bash CLI calls and a subagent yourself, call `moa` and parse its output.*

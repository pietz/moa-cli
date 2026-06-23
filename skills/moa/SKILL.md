---
name: moa
description: Get a second opinion from multiple AI models at once. Use this whenever you're stuck, want to validate an approach or design, pressure-test a plan or claim, or want diverse cross-model viewpoints on a hard decision - anything you'd reach for "peer review", "ask another model", or "council of models" for. Wraps the `moa` CLI, which fans one prompt out to the local agent CLIs (Claude Code, Codex, agy, opencode) in parallel and returns each answer with attribution.
metadata:
  version: "1.0.0"
---

# moa - mixture of agents

`moa` asks one question to several local AI coding CLIs **in parallel** and collects their
answers with attribution. Reach for it when one model's answer isn't enough and you want to
see where independent models agree, diverge, or contradict.

**The CLI is self-documenting.** Run `moa --help` (and `moa <command> --help`) for the full
flag list - this file only covers what's non-obvious about driving it well.

## Prerequisites

`uv tool install moa-cli` (or run ad-hoc with `uvx moa-cli ...`). Then `moa doctor` lists
which agent CLIs are installed and their default models. You need at least two of `claude`,
`codex`, `agy`, `opencode` on `PATH` and logged in - moa drives the CLIs you already pay for
and needs no API keys of its own.

## Three modes

- **`moa ask`** (the default) - council / peer review. N agents answer in parallel; each
  answer streams back with attribution as it lands. The right choice the vast majority of
  the time.
- **`moa distill`** - council, then one strong aggregator merges the answers into a single
  synthesized response. Use when you want *one* answer, not N to read.
- **`moa debate`** - sequential adversarial rounds plus a moderator verdict. The costliest
  and least reliably-beneficial mode; reach for it only when surfacing disagreement is the
  actual goal.

## The two things to get right

1. **Exclude yourself.** If *you* are an agent calling moa for *other* opinions, pass
   `-x <your-provider>` so a "peer" isn't just you: Claude -> `claude`, Codex/GPT -> `codex`,
   Gemini/agy -> `agy`. When in doubt, `moa doctor` shows which one is you.
2. **Parse with `--json`.** `--json` emits one JSON object per line (JSONL) with a `status`
   field per agent - the right output when an agent consumes the result. Without it, answers
   print under a labelled heading on stdout and progress notes go to stderr (so piping stdout
   stays clean). Prefer `--json` whenever you parse programmatically.

## How to prompt

The CLIs are **stateless** - no memory of your conversation. Write a fully self-contained
prompt every call: state the question, paste the relevant code/plan/diff, and say what a
good answer looks like. Read prompts from a file or pipe with `-f PATH` (or `-f -` for stdin).

Agents run **read-only by default**; pass `--yolo` only when you actually want the panel to
change your working tree.

## Selecting the panel and config

`-n N` asks the top N installed agents in priority order (`claude` -> `codex` -> `agy` ->
`opencode`). `-p NAME` pins an exact set, `-x NAME` drops agents, `-m PROVIDER=MODEL`
overrides a model. Persist defaults with `moa config set ...` (e.g. `moa config set num 2`,
`moa config set exclude codex`) and inspect them with `moa config show`. Per-verb flags:
`moa ask --help` / `moa distill --help` / `moa debate --help`.

## Example (you are Claude, so you exclude yourself)

```bash
moa ask --json -x claude "I'm choosing between SQLite and flat Markdown files for ~10k \
offline-first local notes with full-text search and Dropbox sync. Recommend one with tradeoffs."
```

Report results the way a good peer review would: say which providers answered and which
failed or timed out (check `status` in the JSON), then summarize where they agree, disagree,
and any unique insight each contributed. Don't silently drop a model that errored.

---

*Supersedes hand-rolling parallel `claude -p` / `codex exec` calls, or the older
`peer-review` skill: call `moa` and parse its output.*

# MOA - Mixture of Agents

Ask one question to multiple local AI coding CLIs **in parallel** and collect their answers. MOA detects which agent CLIs you have installed (Claude Code, Codex, Gemini CLI, Antigravity), fans your prompt out to them, and streams each answer back the moment that agent finishes. Optionally, it can synthesize the answers into a single unified response.

It's a drop-in, batteries-included replacement for hand-rolling parallel `claude -p` / `codex exec` / `gemini -p` calls (or a "peer review" agent skill): one command, clean attributed output, made to be called by a human **or** by another agent.

The package is named `moa-cli` but installs the command `moa`.

```bash
uv tool install moa-cli
moa ask "Is Postgres or SQLite better for a desktop app?"
```

Or run it once without installing:

```bash
uvx --from moa-cli moa ask "Review this plan."
```

## Why

A single model gives you one perspective. Asking three frontier models the same question - and seeing where they agree, diverge, or contradict - is a fast, cheap way to pressure-test an answer. MOA makes that a one-liner using the CLIs you already pay for, with no API keys of its own.

## Usage

```bash
moa doctor                                  # show which agent CLIs are installed
moa ask "Should this feature use SQLite?"   # ask the top 3 installed agents
moa ask -n 2 "..."                          # ask only the top 2 (priority order)
moa ask -p claude -p gemini "..."           # pin specific agents
moa ask --synth "..."                       # also merge the answers into one
moa ask --synth --blind "..."              # merge, but hide identities from the synthesizer
moa ask --json "..."                        # machine-readable JSONL (for agents/pipes)
git diff | moa ask -f - "Review this diff." # read the prompt from stdin
```

### How agents are selected

`-n/--num` (default 3) picks the first N **installed** agents from a popularity-ordered priority list:

```
claude  →  codex  →  gemini  →  antigravity
```

So `moa ask -n 3` on a machine with all four installed asks Claude, Codex, and Gemini. Use `-p/--provider` (repeatable) to pin an exact set and ignore `-n`.

### Output

- **stdout** carries only content: each agent's answer as a Markdown block (`## claude (opus) - OK - 3.5s`), flushed the instant that agent finishes, then the synthesis block if `--synth` is set.
- **stderr** carries progress and selection notes (`Asking claude, codex ...`), so piping stdout stays clean.
- `--json` emits one JSON object per line (JSONL): a `{"type": "response", ...}` record per agent as it completes, then a `{"type": "synthesis", ...}` record. Ideal when another agent calls MOA and parses the result.

### Synthesis

`--synth` runs one more pass that merges the collected answers into a single, unified answer. The synthesizer is chosen with `--synthesizer`:

- `auto` (default) - the highest-priority agent that ran (deterministic)
- `random` - pick one of the agents that ran, at random
- a provider name (`claude`, `codex`, `gemini`, `antigravity`)

With `--blind`, responses are shuffled and shown to the synthesizer as "Response A / B / C" with no provider names, so it can't favour a brand. The A→agent mapping is reported back to you (stderr, or `label_map` in JSON) so you keep full attribution.

## Supported agents

| Provider      | CLI     | Invocation                                          |
| ------------- | ------- | --------------------------------------------------- |
| `claude`      | `claude`| `claude --model opus -p PROMPT`                     |
| `codex`       | `codex` | `codex exec -m gpt-5.5 --skip-git-repo-check PROMPT`|
| `gemini`      | `gemini`| `gemini -m gemini-3.1-pro-preview -p PROMPT`        |
| `antigravity` | `agy`   | `agy --model "Gemini 3.1 Pro (High)" -p PROMPT`     |

Adding a new agent is a single entry in the `PROVIDERS` table in `src/moa_cli/cli.py` (executable, default model, command builder); it then participates in detection, `-n` selection, and synthesis automatically.

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests
```

## Related

MOA is the local, terminal-native cousin of [moa.chat](https://moa.chat) - a hosted multi-model synthesis chat that adds frontier models, word-level answer attribution, and a consensus Venn view. This CLI stands on its own; moa.chat is there if you want the hosted experience.

MIT licensed.

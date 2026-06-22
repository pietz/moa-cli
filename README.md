<p align="center">
  <img src="assets/logo-full.png" alt="moa - mixture of agents" width="360">
</p>

<p align="center">
  <a href="https://github.com/pietz/moa-cli/actions/workflows/ci.yml"><img src="https://github.com/pietz/moa-cli/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
</p>

# MOA - Mixture of Agents

Ask one question to multiple local AI coding CLIs **in parallel** and collect their answers. MOA detects which agent CLIs you have installed (Claude Code, Codex, agy, opencode), fans your prompt out to them, and streams each answer back the moment that agent finishes. Or run `moa distill` to have a strong aggregator merge those answers into a single unified response.

It's a drop-in, batteries-included replacement for hand-rolling parallel `claude -p` / `codex exec` / `opencode run` calls (or a "peer review" agent skill): one command, clean attributed output, made to be called by a human **or** by another agent.

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

MOA has two prompt verbs that share the same selection/output options:

- **`moa ask PROMPT`** - council / peer review: N agents answer the same prompt in parallel; every answer is returned with attribution, streamed as it lands.
- **`moa distill PROMPT`** - synthesis: run the council, then one strong aggregator merges the answers into a single unified response.

```bash
moa doctor                                  # show installed CLIs and their default models
moa ask "Should this feature use SQLite?"   # ask the top 3 installed agents (read-only)
moa ask -n 2 "..."                          # ask only the top 2 (priority order)
moa ask -p claude -p agy "..."              # pin specific agents
moa ask -x claude "..."                     # drop an agent (e.g. exclude the caller's own model)
moa ask -m claude=sonnet "..."              # override which model a tool uses
moa ask --yolo "..."                        # grant full write access (default is read-only)
moa ask --json "..."                        # machine-readable JSONL (for agents/pipes)
git diff | moa ask -f - "Review this diff." # read the prompt from stdin
moa distill "Design a rate limiter."        # council, then merge into one answer
moa distill -s codex "..."                  # pick who distills (auto | random | provider)
```

The shared options (`-n/--num`, `-p/--provider`, `-x/--exclude`, `-m/--model`, `-t/--timeout`, `-f/--file`, `--json`, `--yolo`) work identically on both verbs. `distill` adds `-s/--synthesizer`.

### Read-only by default

MOA is built to be called autonomously, so by default **no agent can write files or
run mutating commands**. Each agent runs in its tool's safest mode: it may read local
files (and, where the tool allows, research online), but it cannot edit anything. This
is enforced by spawning each CLI with its own read-only flags:

| Provider   | Read-only (default)        | Reads files | Web research              |
| ---------- | -------------------------- | ----------- | ------------------------- |
| `claude`   | `--permission-mode plan`   | yes         | yes                       |
| `codex`    | `-s read-only`             | yes         | **no** (sandbox blocks network) |
| `opencode` | `--agent plan`             | yes         | yes                       |
| `agy`      | `--sandbox` (partial: shell only - can still edit files) | yes | yes |

`codex`'s read-only mode is a kernel sandbox that also blocks network, so codex does no
web research in the default mode (it still reads local files). `agy` has **no true
read-only mode**: its `--sandbox` flag restricts agy's terminal/shell but does **not** stop
its `write_file` tool, so agy **can still edit files** even in the default mode. This is
**partial** protection (it closes the shell vector only), not read-only. moa applies
`--sandbox` as the next-best safeguard and the selection note on stderr states honestly that
`agy` is shell-sandboxed but can still edit files.

### `--yolo` (full write access)

Pass `--yolo` to grant every agent full write access (file edits and shell commands,
auto-approved). Use it only when you actually want the agents to change your working tree.

```bash
moa ask --yolo "Refactor this module and run the tests."
```

Under `--yolo` every agent gets full write access. For `agy` this means dropping
`--sandbox`, so `agy --yolo` runs with no shell restrictions at all. In the default mode,
`agy` runs with `--sandbox` (partial protection: shell only - it can still edit files), and
MOA states that honestly on stderr.

### How agents are selected

`-n/--num` (default 3) picks the first N **installed** agents from a popularity-ordered priority list:

```
claude  ->  codex  ->  agy  ->  opencode
```

So `moa ask -n 3` on a machine with all four installed asks Claude, Codex, and agy (opencode is #4). `agy` has no true read-only mode, so in the default mode it runs with `--sandbox` (partial protection: shell only - it can still edit files) and MOA flags that with an honest note on stderr; it is **not** excluded. Use `-p/--provider` (repeatable) to pin an exact set and ignore `-n`.

Use `-x/--exclude` (repeatable) to drop one or more agents from the run. Exclusion is applied *before* `-n` takes the first N, and it also drops excluded names from an explicit `-p` set. It is off by default. The motivating case: an agent (e.g. Claude Code) calls `moa` for *other* opinions; `moa ask -x claude` makes sure one "peer" isn't just the caller's own model. So `moa ask -n 3 -x claude` asks Codex, agy, and opencode.

### Choosing models

Each tool ships with a reasonable default model, but you can override which model any tool uses with `-m/--model PROVIDER=MODEL` (repeatable). Only the providers you name change; the rest keep their defaults.

```bash
moa ask -m claude=sonnet -m agy="Gemini 3.1 Pro (Low)" "..."
```

The model-string format differs per tool and is passed through verbatim (the tool's own CLI validates it):

| Provider   | Default                 | `-m` format                                            |
| ---------- | ----------------------- | ------------------------------------------------------ |
| `claude`   | `opus`                  | short id, e.g. `claude=sonnet`                         |
| `codex`    | `gpt-5.5`               | model id, e.g. `codex=gpt-5.5`                         |
| `agy`      | `Gemini 3.1 Pro (High)` | exact display name, e.g. `agy="Gemini 3.1 Pro (Low)"`  |
| `opencode` | (tool's authed default) | `provider/model` slug, e.g. `opencode=anthropic/claude-sonnet-4` |

`opencode` has no built-in default; without an override it omits `-m` and lets opencode pick. Pass `-m opencode=provider/model` to pin one.

### Output

- **stdout** carries only content: each agent's answer as a Markdown block (`## claude (opus) - OK - 3.5s`), flushed the instant that agent finishes. `moa distill` then appends the merged block (`## synthesis · via claude - OK - ...`) once the aggregator finishes.
- **stderr** carries progress and selection notes (`Asking claude, codex ...`), so piping stdout stays clean.
- `--json` emits one JSON object per line (JSONL): a `{"type": "response", ...}` record per agent as it completes; `distill` then adds a `{"type": "synthesis", ...}` record. Ideal when another agent calls MOA and parses the result.

### `moa distill` (synthesis)

`distill` runs the same council fan-out as `ask`, then one more pass where a strong aggregator merges the collected answers into a single, unified answer. It needs at least two successful proposer answers; with fewer it streams what it has and skips the merge. The aggregator is chosen with `-s/--synthesizer`:

- `auto` (default) - the highest-priority agent that ran (deterministic)
- `random` - pick one of the agents that ran, at random
- a provider name (`claude`, `codex`, `agy`, `opencode`)

The aggregator prompt is adapted from the Mixture-of-Agents "Aggregate-and-Synthesize" prompt (Wang et al. 2024): it tells the aggregator to critically evaluate the inputs (some may be biased or incorrect) and not to simply replicate them but offer a refined, accurate, comprehensive reply.

> **Coming in a later release:** `moa debate` - a sequential round-robin where models critique each other's answers and a neutral judge synthesizes the verdict.

### Attribution policy

The human (or agent) reading MOA's output **always gets correct attribution**: every response block shows the real provider name. There is no human-facing anonymization toggle.

The `distill` aggregator is a different story. To stop it picking favourites by brand, it **always** receives the proposer answers anonymized as "Response A / B / C" and order-shuffled (no toggle). The merged answer itself is brand-agnostic prose, and the A/B/C labels never leak into stdout, stderr, or the JSON.

## Supported agents

Invocations below show the default (read-only) flags; `--yolo` swaps in each tool's full-access mode.

| Provider    | CLI        | Invocation (read-only default)                                      |
| ----------- | ---------- | ------------------------------------------------------------------- |
| `claude`    | `claude`   | `claude --model opus --permission-mode plan -p PROMPT`              |
| `codex`     | `codex`    | `codex exec -m gpt-5.5 --skip-git-repo-check -s read-only PROMPT`   |
| `agy`       | `agy`      | `agy --sandbox --model "Gemini 3.1 Pro (High)" -p PROMPT` (partial: shell only - can still edit files) |
| `opencode`  | `opencode` | `opencode run --agent plan PROMPT`                                  |

Adding a new agent is a single entry in the `PROVIDERS` table in `src/moa_cli/cli.py` (executable, default model, command builder, permission flags); it then participates in detection, `-n` selection, and `distill` automatically.

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests
```

MIT licensed.

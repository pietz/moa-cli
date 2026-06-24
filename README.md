<p align="center">
  <img src="assets/logo-full-white.png" alt="moa - mixture of agents" width="300">
</p>

<p align="center">
  <strong>One prompt, fanned out to the local agent CLIs you have, answered in parallel.<br>Each answer streams back attributed, as it lands.</strong>
</p>

<p align="center">
  <a href="https://github.com/pietz/moa-cli/actions/workflows/ci.yml"><img src="https://github.com/pietz/moa-cli/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/moa-cli/"><img src="https://img.shields.io/pypi/v/moa-cli.svg?label=pypi" alt="PyPI"></a>
</p>

---

<p align="center">
  <img src="demo/ask.gif" alt="moa ask - a council of local agents answering one prompt in parallel">
</p>

---

Some problems deserve more than one agent.

**moa** (mixture of agents) fans your prompt out to the local agent CLIs you have installed and runs them in parallel (the top 3 by default, tunable with `-n`), so you can see where independent models agree, differ, or are flat-out wrong. It uses the subscriptions you already pay for, no API key of its own.

```bash
uvx moa-cli ask "Is Postgres or SQLite better for a desktop app?"
```

Or install it for the plain `moa` command:

```bash
uv tool install moa-cli
```

Or give a coding agent (like Claude Code) the moa [skill](https://www.skills.sh/pietz/moa-cli/moa), so it reaches for a second opinion on its own:

```bash
npx skills add https://github.com/pietz/moa-cli --skill moa
```

## Three modes

### `ask` - a council

**Peer review for every prompt.** N agents answer in parallel, each attributed as it lands - the default, and the right call most of the time.

```bash
moa ask "Name one underrated CLI tool every dev should know. One sentence."
```

### `distill` - one merged answer

**Many models, one answer.** The council answers, then a strong aggregator synthesizes them into a single response.

```bash
moa distill "What are the top 5 principles of good API design?"
```

![moa distill demo](demo/distill.gif)

### `debate` - adversarial rounds

**See the debate, trust the decision.** Two agents argue across rounds; a moderator weighs the exchange and writes the verdict.

```bash
moa debate "Should we move our database from SQLite to Postgres now?"
```

![moa debate demo](demo/debate.gif)

## Supported agents

moa auto-detects whichever of these are on your `PATH` and drives them in their safest read-only mode. You need **at least two** for a real council; run **`moa doctor`** to see yours.

| Agent | CLI | Default model | Read-only |
| --- | --- | --- | --- |
| Claude Code | `claude` | `opus` | yes |
| OpenAI Codex | `codex` | `gpt-5.5` | yes |
| Google Antigravity | `agy` | `Gemini 3.5 Flash (High)` | partial |
| opencode | `opencode` | (authed default) | yes |

Adding a new agent is a single entry in the `PROVIDERS` table in `src/moa_cli/providers.py`.

## Features

- **Read-only by default** - every agent runs in its tool's safest mode; pass `--yolo` only when you mean it.
- **Built for agents to call agents** - JSONL output, TTY-aware formatting, a ready-made skill for Claude Code.
- **Honest about its limits** - states caveats (like `agy`'s partial sandbox, or debate's failure modes) on stderr instead of hiding them.
- **One runtime dependency** (`typer`), pure Python 3.12+, ~1.7k lines of source, ~2.1k lines of tests.

## How it works

**`moa ask`** runs a parallel council. Each answer streams back, attributed, the instant it lands:

```
          ┌──► claude ──► "SQLite is almost always the right call..."
 prompt ──┼──► codex  ──► "Use SQLite unless you need concurrent writers..."
          └──► agy    ──► "For a single-user desktop app, SQLite wins..."
```

**`moa distill`** runs the same council, then one strong aggregator merges the answers into a single response (brand labels hidden, order shuffled):

```
          ┌──► claude ─┐
 prompt ──┼──► codex  ─┼──► synthesizer ──► one unified answer
          └──► agy    ─┘
```

**`moa debate`** runs a sequential adversarial exchange: two debaters critique each other across rounds while a moderator checks for convergence and writes the final verdict (transcript anonymized + shuffled to kill brand bias):

```
   round 1:  A answers cold
             B critiques A, then answers
   ┌─ round k:  each sees the other's latest, responds in turn
   │            moderator: DONE (converged) or CONTINUE?
   └─ loops up to N rounds (default 2, hard max 4)
   verdict:  moderator reads the full shuffled transcript, writes the final answer
```

## Example

```text
$ moa ask "Is Postgres or SQLite better for a desktop app?"
Asking claude, codex, agy (timeout 900s, read-only)

──────────────── claude (opus) · OK · 3.2s ────────────────

For a single-user desktop app, SQLite is almost always the right call:
zero-config, serverless, the whole DB is one file you can ship... [trimmed]

──────────────── codex (gpt-5.5) · OK · 4.1s ──────────────

Use SQLite unless you expect concurrent writers or need network access.
For a desktop app neither is likely, so SQLite wins on simplicity... [trimmed]
```

The selection note goes to **stderr**; the attributed answers go to **stdout**. In a terminal each answer gets the box-drawing rule shown above; when piped or read by another agent, the same blocks render as plain `## ...` headings. Add `--json` for machine-readable JSONL.

## Why

The synthesis prompt is adapted from the Mixture-of-Agents "Aggregate-and-Synthesize" prompt ([Wang et al. 2024](https://arxiv.org/abs/2406.04692)): the aggregator is told to critically evaluate its inputs (some may be biased or incorrect) and offer a refined, accurate, comprehensive reply rather than just replicating them.

It's also a drop-in replacement for hand-rolling parallel `claude -p` / `codex exec` / `opencode run` calls, or for a hand-rolled "peer review" agent skill: one command, clean attributed output, made to be called by a human **or** by another agent.

## Usage

moa has three prompt verbs that share the same selection and output options:

- **`moa ask PROMPT`** - council / peer review: N agents answer the same prompt in parallel; every answer is returned with attribution, streamed as it lands.
- **`moa distill PROMPT`** - synthesis: run the council, then one strong aggregator merges the answers into a single unified response.
- **`moa debate PROMPT`** - sequential debate: two debaters answer and adversarially critique each other across rounds, with a moderator that checks for convergence between rounds and writes the final verdict. The costliest mode; read the caveats before reaching for it.

```bash
moa doctor                                  # run this first: which agent CLIs can moa find?

# The three collaboration modes (read-only by default):
moa ask "Should this feature use SQLite?"   # council: top 3 agents answer in parallel
moa distill "Design a rate limiter."        # council, then one merged answer
moa debate "Is this race condition real?"   # adversarial rounds + moderator verdict

# Built for agents to call agents:
moa ask --json -x claude "Review this plan."  # exclude yourself + emit JSONL
git diff | moa ask -f - "Review this diff."   # read the prompt from stdin

# Tune the panel:
moa ask -n 2 "..."              # only the top 2 (priority order)
moa ask -p claude -p agy "..."  # pin an exact set
moa ask -m claude=sonnet "..."  # override a tool's model
moa ask --yolo "..."            # allow file edits and shell (default is read-only)
```

The shared options (`-n/--num`, `-p/--provider`, `-x/--exclude`, `-m/--model`, `-t/--timeout`, `-f/--file`, `--json`, `--yolo`) work identically on all three verbs. `distill` adds `-s/--synthesizer`; `debate` adds `-r/--rounds` and `--moderator`.

### Read-only by default

moa is built to be called autonomously, so by default **no agent can write files or run mutating commands**. Each agent runs in its tool's safest mode: it may read local files (and, where the tool allows, research online), but it cannot edit anything. This is enforced by spawning each CLI with its own read-only flags:

| Provider   | Read-only (default)        | Reads files | Web research              |
| ---------- | -------------------------- | ----------- | ------------------------- |
| `claude`   | `--permission-mode default` | yes        | yes                       |
| `codex`    | `-s read-only`             | yes         | **no** (sandbox blocks network) |
| `opencode` | `--agent plan`             | yes         | yes                       |
| `agy`      | `--sandbox` (partial: shell only - can still edit files) | yes | yes |

`agy` has no true read-only mode - its `--sandbox` only restricts the shell, not its `write_file` tool (see *Honesty & caveats*). Per-tool nuances (why claude's `default` is read-only headless, why codex blocks network) and the full sandbox reference: [`docs/cli-permission-modes.md`](docs/cli-permission-modes.md).

### `--yolo` (full write access)

Pass `--yolo` to grant every agent full write access (file edits and shell commands, auto-approved). Use it only when you actually want the agents to change your working tree.

```bash
moa ask --yolo "Refactor this module and run the tests."
```

### How agents are selected

`-n/--num` (default 3) picks the first N **installed** agents from a popularity-ordered priority list:

```
claude  ->  codex  ->  agy  ->  opencode
```

So `moa ask -n 3` on a machine with all four installed asks Claude, Codex, and agy (opencode is #4). Use `-p/--provider` (repeatable) to pin an exact set and ignore `-n`.

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

### Configuration

Persist your own defaults at `~/.moa/config.toml` so you don't repeat flags. Precedence is `built-in default < config file < CLI flag`; an absent file means today's behaviour. Set `$MOA_CONFIG_DIR` to relocate it.

```toml
# ~/.moa/config.toml
num = 2
exclude = ["claude"]

[providers.codex]
model = "gpt-5.5"
effort = "high"
```

Keys are shared across all verbs (`num`, `timeout`, `exclude`, `synthesizer`, `moderator`, and `[providers.<name>]` with `model`/`effort`). Edit them with `moa config set ...` / `moa config show`, or just write the TOML. See [`docs/configuration.md`](docs/configuration.md) for the full key reference, the `moa config` command list, and the per-provider reasoning/effort mapping.

### Output

- **stdout** carries only content. In a terminal, each agent's answer is fronted by a centered box-drawing rule naming it (`──── claude (opus) · OK · 3.5s ────`) with blank lines for separation, flushed the instant that agent finishes. When stdout is **piped or read by an agent** (not a TTY), the same block renders as a plain, low-noise `## claude (opus) · OK · 3.5s` heading instead - no box-drawing. `moa distill` emits only the final merged block.
- **stderr** carries progress and selection notes (`Asking claude, codex ...`), so piping stdout stays clean.
- **Live status line.** On a TTY, stderr shows an in-place spinner naming each in-flight agent and model with an elapsed timer (`⠋ claude (opus) 3.1s   ⠙ codex (gpt-5.5) 3.1s`) while they work, clearing the instant each answer lands. Off a TTY (piped, logged, or read by an agent) it is a strict no-op, so machine-readable output is byte-identical to a run with no status line.
- `--json` emits one JSON object per line (JSONL): `ask` writes a `{"type": "response", ...}` record per agent as it completes; `distill` writes a single `{"type": "synthesis", ...}` record (only the merged answer); `debate` writes a `{"type": "debate_turn", "round": N, ...}` record per turn plus a final `{"type": "verdict", ...}` record. Ideal when another agent calls moa and parses the result.

## Modes in depth

### `moa distill` (synthesis)

Runs the same council as `ask`, then one strong aggregator merges the answers into a single unified answer. **It returns only the merged answer** - proposer responses are intermediates (their arrival is noted on stderr). It needs at least two successful proposers, or it skips the merge and says so. Pick the aggregator with `-s/--synthesizer` (`auto` = highest-priority that ran, `random`, or a provider name).

### `moa debate` (adversarial + moderator)

Two debaters critique each other across rounds while a moderator checks for convergence and writes the final verdict. The transcript is anonymized and shuffled before the moderator sees it, so brand and position bias can't steer the verdict. Needs 2 agents; `-r/--rounds` (default 2, max 4) and `--moderator` tune it. It's the costliest and least reliably beneficial mode - reach for it only to surface disagreement (see *Honesty & caveats*). Full roles, loop, and output format: [`docs/debate.md`](docs/debate.md).

### Attribution policy

The human (or agent) reading moa's output **always gets correct attribution**: every response block shows the real provider name. There is no human-facing anonymization toggle.

The `distill` aggregator is a different story. To stop it picking favourites by brand, it **always** receives the proposer answers anonymized as "Response A / B / C" and order-shuffled (no toggle). The merged answer itself is brand-agnostic prose, and the A/B/C labels never leak into stdout, stderr, or the JSON.

## Honesty & caveats

moa is designed to be honest about where it can and can't help. Two things worth keeping in mind:

- **`agy`'s read-only is partial.** `agy` has no true read-only mode. Its `--sandbox` restricts the shell but does not block its `write_file` tool, so agy can still edit files even in the default mode. moa applies `--sandbox` as the next-best safeguard and says so plainly on stderr rather than implying full sandboxing.
- **Use `debate` sparingly.** It is the costliest mode (roughly `debaters × rounds` calls, plus a moderator check per round and the verdict) **and the least reliably beneficial.** The research is mixed-to-negative: multi-agent debate can converge on a *wrong* answer through conformity, a confident-but-incorrect debater can win on persuasiveness over correctness, and more rounds can entrench an error rather than fix it. The moderator and the adversarial-stance prompt are there to fight these failure modes, but they do not eliminate them. For most questions, `ask` or `distill` is the better default; reach for `debate` when you specifically want to surface and stress-test disagreement. (See *Can LLM Agents Really Debate?* arXiv:2511.07784, *Talk Isn't Always Cheap* arXiv:2509.05396, and the conformity/position-bias work cited in the design notes.)

## Costs & privacy

moa has no API key and no telemetry of its own, but it is still spending real resources through the CLIs it drives:

- **It uses your subscriptions.** Every selected agent runs a full request against the account you're logged into for that CLI, counting toward its usage/quota.
- **`distill` and `debate` cost more.** `distill` adds one aggregator call on top of the council; `debate` is roughly `debaters × rounds` calls plus a moderator check per round and a verdict.
- **Your prompt still reaches the providers.** moa sends no data anywhere itself, but each agent ships your prompt (and whatever local files its CLI reads) to its own model provider under that tool's terms.

## Use moa from an agent

If you drive moa from an agent (e.g. Claude Code), there's a ready-made skill at [`skills/moa/SKILL.md`](skills/moa/SKILL.md): it tells an agent when to reach for moa and how to use it (verb choice, self-exclusion via `-x <self>`, parsing the JSONL output). It supersedes hand-rolling a "peer review" skill.

Install it with the [`skills`](https://github.com/vercel-labs/skills) CLI (works with Claude Code and 40+ other agents):

```bash
# interactive: pick agent + scope
npx skills add https://github.com/pietz/moa-cli --skill moa

# or non-interactive, e.g. globally for Claude Code
npx skills add https://github.com/pietz/moa-cli --skill moa -a claude-code -g
```

The skill still needs the `moa` CLI itself on your `PATH` (`uv tool install moa-cli`) plus at least two agent CLIs installed and authed - run `moa doctor` to check.

## Contributing

Contributions are welcome. moa uses a subagent-driven backlog workflow: each feature lives as a self-contained spec in [`backlog/`](backlog/!README.md), and a builder subagent implements it end to end (code + tests) before a separate fresh-eyes reviewer signs off. The backlog README documents the full loop, so any agent (or contributor) can pick up a `ready` ticket cold.

- Grab a `ready` ticket from [`backlog/`](backlog/) and open a PR.
- Keep new providers to one entry in the `PROVIDERS` table (`src/moa_cli/providers.py`).
- Match the existing style: pure functions in `workflows.py`, no new runtime deps without a strong reason.

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests
```

CI runs `ruff` + `pytest` on Python 3.12 and 3.13. Releases are tag-driven: bump the version in `pyproject.toml` and `src/moa_cli/__init__.py`, tag `vX.Y.Z`, push, and the Release workflow publishes to PyPI and cuts a GitHub Release. See [`docs/releasing.md`](docs/releasing.md).

## License

MIT.

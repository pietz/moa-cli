<p align="center">
  <img src="assets/logo-full-white.png" alt="moa - mixture of agents" width="360">
</p>

<p align="center">
  <a href="https://github.com/pietz/moa-cli/actions/workflows/ci.yml"><img src="https://github.com/pietz/moa-cli/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
</p>

# MOA - Mixture of Agents

Ask one question to multiple local AI coding CLIs **in parallel** and collect their answers. MOA detects which agent CLIs you have installed (Claude Code, Codex, agy, opencode), fans your prompt out to them, and streams each answer back the moment that agent finishes. Or run `moa distill` to have a strong aggregator merge those answers into a single unified response, or `moa debate` to have them critique each other across rounds while a moderator checks for convergence and writes the verdict.

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

> **Requirements.** MOA drives agent CLIs you install separately - it ships no model
> or API key of its own. You need at least two of `claude` (Claude Code), `codex`,
> `agy` (Antigravity), and `opencode` on your `PATH` and logged in. Run **`moa doctor`**
> first to see which ones MOA can find; with only one installed, the "council" collapses
> to a single answer.

## Why

A single model gives you one perspective. Asking three frontier models the same question - and seeing where they agree, diverge, or contradict - is a fast, cheap way to pressure-test an answer. MOA makes that a one-liner using the CLIs you already pay for, with no API keys of its own.

### Example

```text
$ moa ask "Is Postgres or SQLite better for a desktop app?"
Asking claude, codex, agy (timeout 900s, read-only)

──────────────── claude (opus) · OK · 3.2s ────────────────

For a single-user desktop app, SQLite is almost always the right call:
zero-config, serverless, the whole DB is one file you can ship... [trimmed]

─────────────── codex (gpt-5.5) · OK · 4.1s ───────────────

Use SQLite unless you expect concurrent writers or need network access.
For a desktop app neither is likely, so SQLite wins on simplicity... [trimmed]
```

The selection note goes to stderr; the attributed answers go to stdout. In a terminal
each answer gets the rule shown above; when piped or read by another agent, the same
blocks render as plain `## ...` headings. Add `--json` for machine-readable JSONL.

## Usage

MOA has three prompt verbs that share the same selection/output options:

- **`moa ask PROMPT`** - council / peer review: N agents answer the same prompt in parallel; every answer is returned with attribution, streamed as it lands.
- **`moa distill PROMPT`** - synthesis: run the council, then one strong aggregator merges the answers into a single unified response.
- **`moa debate PROMPT`** - sequential debate: two debaters answer and adversarially critique each other across rounds, with a moderator that checks for convergence between rounds and writes the final verdict. The costliest mode; read the caveats below before reaching for it.

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
moa debate "Is this race condition real?"   # 2 debaters; the first also moderates (default 2 agents)
moa debate -r 3 "..."                        # more rounds (default 2, hard max 4)
moa debate --moderator agy "..."             # pin a neutral moderator (a non-debater)
```

The shared options (`-n/--num`, `-p/--provider`, `-x/--exclude`, `-m/--model`, `-t/--timeout`, `-f/--file`, `--json`, `--yolo`) work identically on all three verbs. `distill` adds `-s/--synthesizer`; `debate` adds `-r/--rounds` and `--moderator`.

### Read-only by default

MOA is built to be called autonomously, so by default **no agent can write files or
run mutating commands**. Each agent runs in its tool's safest mode: it may read local
files (and, where the tool allows, research online), but it cannot edit anything. This
is enforced by spawning each CLI with its own read-only flags:

| Provider   | Read-only (default)        | Reads files | Web research              |
| ---------- | -------------------------- | ----------- | ------------------------- |
| `claude`   | `--permission-mode default` | yes        | yes                       |
| `codex`    | `-s read-only`             | yes         | **no** (sandbox blocks network) |
| `opencode` | `--agent plan`             | yes         | yes                       |
| `agy`      | `--sandbox` (partial: shell only - can still edit files) | yes | yes |

`claude`'s `--permission-mode default` is read-only in moa's non-interactive use: it reads
files and researches online with the full toolset, but any write or edit needs an interactive
approval that never comes under `-p`, so all mutations are denied. (`plan` mode is **not**
usable headless - it emits a plan and waits for approval instead of answering.)

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

### Configuration

To avoid repeating the same flags on every call, persist your own defaults in a config file. MOA reads it for every verb and merges it under your flags.

**Location.** `~/.moa/config.toml` (the dir is created on first write). Set `$MOA_CONFIG_DIR` to point the whole config layer somewhere else (useful in tests/CI).

**Precedence.** `built-in default  <  config file  <  CLI flag`. A flag always wins; the config file only changes a default when that flag is omitted; an absent file means today's built-in behaviour.

**Keys** (all shared across `ask`/`distill`/`debate`):

| Key                | Type                     | Example                       |
| ------------------ | ------------------------ | ----------------------------- |
| `num`              | int (>= 1)               | `num = 2`                     |
| `timeout`          | seconds (> 0)            | `timeout = 120`               |
| `exclude`          | list of provider names   | `exclude = ["claude"]`        |
| `synthesizer`      | `auto`/`random`/provider | `synthesizer = "codex"`       |
| `[providers.<name>]` | per-provider `model` + `effort` | see below              |
| `[models]`         | DEPRECATED provider -> model table | `claude = "sonnet"` |

```toml
# ~/.moa/config.toml
num = 2
timeout = 120
exclude = ["claude"]
synthesizer = "auto"

[providers.codex]
model = "gpt-5.5"
effort = "high"

[providers.opencode]
model = "zai-coding-plan/glm-5.2"
effort = "high"
```

Model and effort are grouped per provider under `[providers.<name>]`. The flat `[models]` table still works as a **deprecated alias** for `[providers.<name>].model`; when both set a model for the same provider, the `[providers.<name>]` block wins (MOA prints a one-line note, not an error).

**`moa config`** inspects and edits the file (it creates the dir/file as needed and validates provider names):

```bash
moa config show                       # effective config (defaults + file) + path
moa config path                       # print the config file path
moa config set num 2                  # set a scalar
moa config set exclude claude,codex   # set the exclude list (comma-separated)
moa config set model codex=gpt-5.5    # set a provider's model
moa config set effort codex=high      # set a provider's reasoning effort
moa config unset num                  # remove a key
moa config unset model codex          # remove one provider's model
moa config unset effort codex         # remove one provider's effort
```

The role defaults are persistable too: the distill `synthesizer` and the debate `moderator` (e.g. `moa config set synthesizer codex`, `moa config set moderator agy`). `debate`'s `-r/--rounds` is not persisted. CLI `-m` overrides win per-provider over the config model.

#### Reasoning / effort

Pin a per-provider **reasoning/effort** level in config so the council runs each tool at the depth you want without repeating flags. This is **config-only**: there is intentionally no `-e/--effort` CLI flag.

MOA uses **raw pass-through with zero value mapping.** It does not normalize effort across providers or invent a canonical low/med/high scale. You write the **exact value the target tool expects**, and MOA pastes it verbatim into that provider's native flag. The only thing MOA maps is *where* the value lands in each provider's argv, never the value itself:

| Provider   | `effort` lands in                    | Notes                                                       |
| ---------- | ------------------------------------ | ----------------------------------------------------------- |
| `codex`    | `-c model_reasoning_effort=<value>`  | generic config override                                     |
| `opencode` | `--variant <value>`                  | opencode's "model variant (provider-specific reasoning effort)" |
| `agy`      | (none)                               | reasoning is part of the model name, e.g. `Gemini 3.1 Pro (High)` |
| `claude`   | (none)                               | no per-call effort flag                                     |

Values are **tool-specific and not validated** by MOA (only "non-empty if present"): a value the target tool rejects fails at that tool, not in MOA. When no effort is configured for a provider, MOA passes **no effort flag at all**, so the tool's own default stands. Setting `effort` for `agy`/`claude` is stored but inert (they have no effort flag); MOA notes this when you set it.

### Output

- **stdout** carries only content. In a terminal, each agent's answer is fronted by a centered box-drawing rule naming it (`──── claude (opus) · OK · 3.5s ────`) with blank lines for separation, flushed the instant that agent finishes. When stdout is **piped or read by an agent** (not a TTY), the same block renders as a plain, low-noise `## claude (opus) · OK · 3.5s` heading instead - no box-drawing. `moa distill` emits only the final merged block.
- **stderr** carries progress and selection notes (`Asking claude, codex ...`), so piping stdout stays clean.
- `--json` emits one JSON object per line (JSONL): `ask` writes a `{"type": "response", ...}` record per agent as it completes; `distill` writes a single `{"type": "synthesis", ...}` record (only the merged answer); `debate` writes a `{"type": "debate_turn", "round": N, ...}` record per turn plus a final `{"type": "verdict", ...}` record. Ideal when another agent calls MOA and parses the result.

### `moa distill` (synthesis)

`distill` runs the same council fan-out as `ask`, then one more pass where a strong aggregator merges the collected answers into a single, unified answer. **It returns only that merged answer** - the individual proposer responses are intermediates and are not printed (each one's arrival is noted on stderr so the wait isn't silent). It needs at least two successful proposer answers; with fewer it skips the merge and says so on stderr. The aggregator is chosen with `-s/--synthesizer`:

- `auto` (default) - the highest-priority agent that ran (deterministic)
- `random` - pick one of the agents that ran, at random
- a provider name (`claude`, `codex`, `agy`, `opencode`)

The aggregator prompt is adapted from the Mixture-of-Agents "Aggregate-and-Synthesize" prompt (Wang et al. 2024): it tells the aggregator to critically evaluate the inputs (some may be biased or incorrect) and not to simply replicate them but offer a refined, accurate, comprehensive reply.

### `moa debate` (sequential debate + moderator)

`debate` is the opt-in, highest-cost mode. Instead of fanning out in parallel, it runs a sequential, adversarial exchange overseen by a **moderator** that checks for convergence between rounds and writes the final answer.

**Roles.** The top **2** selected agents are the debaters. The **moderator** runs the per-round convergence check and writes the verdict; by default it is the top-priority selected agent (so the default 2-agent debate has agent #1 also moderate). Debate only needs **2 agents**; with fewer it exits cleanly rather than silently degrading. For a **neutral** moderator that doesn't also debate, select a third agent and pin it: `moa debate -n 3 --moderator <provider>` (the moderator must be one of the selected agents). The moderator only ever sees the transcript **anonymized + shuffled**, so even when it is itself a debater it can't favour its own answer.

**Rounds.** `-r/--rounds` defaults to **2** (gains plateau around 2-3 rounds while token cost grows multiplicatively) and is hard-capped at **4** - higher values are clamped with a warning on stderr.

**The loop.** Round 1: debater A answers cold; debater B sees A's answer with an adversarial-stance instruction ("identify errors/weaknesses before giving your own answer; do not agree merely to reach consensus"). Each later round, every debater sees the other's latest answer and responds in the same spirit. After each non-final round the **moderator** reads the debaters' latest answers and replies `DONE` (they've converged or fully aired their disagreement) or `CONTINUE`; a `DONE` stops the debate before the cap.

**The verdict.** The moderator reads the full transcript - presented **anonymized and order-shuffled** (so brand/position bias is killed, even when the moderator was a debater) - and writes the final answer. Its prompt instructs it to weigh correctness and evidence **above** confidence and fluency. The verdict is the final block (`──── verdict · moderator <name> · ... ────`).

**Streaming/output.** Each debater's turn streams as it completes (`──── round N · <provider> · ... ────`), then the moderator's verdict last. `--json` emits a `{"type": "debate_turn", "round": N, ...}` record per turn plus a final `{"type": "verdict", "moderator": "<name>", ...}` record.

**Safety.** Debaters and the moderator run in the same read-only (or `--yolo`) mode as the other verbs - there is no permission bypass. agy's partial-sandbox caveat (shell only; it can still edit files) applies here too.

> **Caveat - use sparingly.** Debate is the costliest mode (roughly `debaters × rounds` calls, plus a moderator check per round and the verdict) **and the least reliably beneficial.** The research is mixed-to-negative: multi-agent debate can converge on a *wrong* answer through conformity, a confident-but-incorrect debater can win on persuasiveness over correctness, and more rounds can entrench an error rather than fix it. The moderator and the adversarial-stance prompt are there to fight these failure modes, but they do not eliminate them. For most questions, `ask` or `distill` is the better default; reach for `debate` when you specifically want to surface and stress-test disagreement. (See *Can LLM Agents Really Debate?* arXiv:2511.07784, *Talk Isn't Always Cheap* arXiv:2509.05396, and the conformity/position-bias work cited in the design notes.)

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

## Agent skill

If you drive MOA from an agent (e.g. Claude Code), there's a ready-made skill at
[`skills/moa/SKILL.md`](skills/moa/SKILL.md): it tells an agent when to reach for MOA and
how to use it (verb choice, self-exclusion via `-x <self>`, parsing the JSONL output). It
supersedes hand-rolling a "peer review" skill.

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests
```

MIT licensed.

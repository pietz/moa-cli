# CLI permission & sandbox modes (reference)

A captured reference of how each agent CLI moa drives handles permissions and
sandboxing in **non-interactive / headless** use, so we don't have to re-research
it. Each tool is different. The headless angle matters: a mode that prompts for
approval interactively has no one to approve in `-p`/`exec`/`run`, so it either
auto-denies (effectively read-only) or auto-proceeds (autonomous writes).

## moa's mapping (current direction)

moa exposes **two** modes for now; a stricter read-only is deferred to ticket
[013](../backlog/013-read-only-mode.md).

| moa mode      | flag       | intent                                                        |
| ------------- | ---------- | ------------------------------------------------------------- |
| **default**   | *(none)*   | each tool's *normal / recommended* mode (no longer strict read-only) |
| **yolo**      | `--yolo`   | full write + shell + network; bypass sandbox/approvals        |
| ~~read-only~~ | *(future)* | stricter opt-in than default; can't write/mutate. See 013.    |

Concrete per-tool flags (details + caveats in each section below):

| tool         | default (new)                 | yolo                            | read-only (→ ticket 013)        |
| ------------ | ----------------------------- | ------------------------------- | ------------------------------- |
| **claude**   | `--permission-mode default`   | `--permission-mode bypassPermissions` | `dontAsk` + read-only allowlist |
| **codex**    | `-s workspace-write`          | `-s danger-full-access`         | `-s read-only`                  |
| **opencode** | `--agent build`               | `--dangerously-skip-permissions`| `--agent plan`                  |
| **agy**      | `--sandbox` *(partial guardrail)* | *(drop `--sandbox`)*        | *none — not expressible*        |

**Heads-up:** under this `default`, **codex / opencode / agy can write files in the
working dir** (claude cannot, because headless can't approve its writes). That is a
deliberate shift away from the old read-only default. The strict no-write guarantee
returns as the opt-in in ticket 013.

---

## claude (Claude Code) — researched, Claude Code v2.1.186

Permission is controlled by `--permission-mode <mode>`. The headless `-p`
behaviour is the load-bearing detail.

| Tier (concept) | Exact flags (headless `-p`) | Allows | Blocks | Headless behaviour |
| --- | --- | --- | --- | --- |
| **read-only** (strict) | `--permission-mode dontAsk --allowedTools "Read,Bash(git log *),Bash(git diff *),Bash(grep *),Bash(wc *),Bash(find *),WebFetch,WebSearch"` | reads, read-only Bash patterns, web | all writes/edits (not in allowlist) | aborts with a clear error if it attempts a denied tool |
| **default** (moa default) | `--permission-mode default` | full toolset present: reads, read-only Bash, web; answers normally | writes/edits — there is no prompt to approve them headless, so they're **denied** | effectively read-only *in headless* even though all tools exist; answers directly |
| *(middle, autonomous edits)* | `--permission-mode acceptEdits` | reads + **auto-approved** file edits + safe fs Bash (`mkdir/mv/cp/rm/touch/sed`) on in-scope paths + web | out-of-scope paths, protected dirs (`.git`), non-approved Bash | edits execute **automatically, no review**; the only headless mode that writes-but-scoped |
| **yolo** | `--permission-mode bypassPermissions` | everything | nothing (except `rm -rf /` circuit-breaker) | everything runs immediately; container/VM only |

**Key nuances**
- `--permission-mode plan` is **unusable headless**: it builds a plan and waits for
  human approval that never comes, emitting a meta-message instead of an answer.
  This is why moa moved claude's default off `plan`.
- claude's `default` is "all tools, but can't write headless" — that's why it's a
  fine *default* tier even though it doesn't allow writes. If moa ever wants
  claude to autonomously edit at a tier below yolo, that's `acceptEdits`.
- moa already clears `CLAUDECODE` from the env so claude runs nested.

Sources: [permission-modes](https://code.claude.com/docs/en/permission-modes.md),
[headless](https://code.claude.com/docs/en/headless.md),
[cli-reference](https://code.claude.com/docs/en/cli-reference.md).

---

## codex (OpenAI Codex CLI) — researched, verified on codex-cli 0.142.0

`codex exec` is **always** `approval: never` — it is structurally incapable of
stalling on an approval prompt (passing `-a/--ask-for-approval` actually errors
under `exec`). So the tier is decided **solely** by the `-s/--sandbox` value;
approval policy is irrelevant headless.

| Tier | Exact flags (`codex exec`) | Allows | Blocks | Headless behaviour |
| --- | --- | --- | --- | --- |
| **read-only** | `-s read-only` *(also the exec default)* | reads | writes, mutating shell, network | denies cleanly, never stalls |
| **default** (moa default) | `-s workspace-write` | reads, writes within cwd + `/tmp` + `$TMPDIR`, shell | out-of-workspace writes; network **off by default** | writes succeed headlessly, no prompt |
| **yolo** | `-s danger-full-access` | everything: write anywhere, unrestricted shell, network | nothing | runs fully |

**Key nuances**
- The middle/recommended tier is `-s workspace-write` — codex's own recommended
  mode for local automation. Sits cleanly between the two existing tiers.
- Network is **off** in `workspace-write`; add `-c sandbox_workspace_write.network_access=true` to enable.
- A user's `~/.codex/config.toml` can change defaults (e.g. enable network, trust
  dirs). For deterministic behaviour independent of user config, pass
  `--ignore-user-config` and set `-s`/`-c` explicitly.
- `--full-auto` is **deprecated** → use `-s workspace-write`. For yolo, prefer
  `-s danger-full-access` over `--dangerously-bypass-approvals-and-sandbox`
  (the latter also drops process supervision; only for already-sandboxed envs).

Sources: [Sandbox](https://developers.openai.com/codex/concepts/sandboxing),
[Non-interactive mode](https://developers.openai.com/codex/noninteractive),
[CLI reference](https://developers.openai.com/codex/cli/reference);
plus live `codex exec` probes on 0.142.0.

---

## opencode — researched, verified on opencode 1.17.8

Two layers: **agents** (`--agent <name>` bundles a system prompt + permission set)
and a config **`permission`** map (per-tool `allow`/`ask`/`deny`). Built-in agents:
`build` (default, `*: allow` — full access), `plan` (read-only: `edit *: deny` +
read-only system prompt). Permissions **cannot** be set via CLI flags — only via
config file or the `OPENCODE_CONFIG` / `OPENCODE_CONFIG_CONTENT` env vars.

| Tier | Exact flags (`opencode run`) | Allows | Blocks | Headless behaviour |
| --- | --- | --- | --- | --- |
| **read-only** | `--agent plan` | read, grep/glob, read-only bash, webfetch | edit/write, mutating bash | completes; mutations auto-denied (matches moa today) |
| **default** (moa default) | `--agent build` *(the default agent)* | read, **edit**, bash, webfetch — full normal access | `doom_loop`/`external_directory` (both `ask`→auto-reject headless) | normal mode; can edit files in cwd |
| **yolo** | `--dangerously-skip-permissions` | everything | nothing except explicit `deny` rules | writes succeed |

**Key nuances**
- Headless `opencode run` **auto-rejects** any `ask` permission (it does NOT hang —
  behaviour change; older ~1.2.x versions hung). So an `ask`-based "scoped edits
  with guardrails" middle tier degrades to *deny* headless and isn't really
  expressible without injecting a scoped-**allow** config via `OPENCODE_CONFIG`.
- For moa's 2-mode model: **default = `--agent build`** (opencode's normal mode,
  can edit), **yolo = `--dangerously-skip-permissions`**.
- moa's *current* yolo gap: relying on the bare default `build` leaves
  `doom_loop:ask`/`external_directory:ask`, which auto-reject headless — true yolo
  needs `--dangerously-skip-permissions`. Use the **flag**, not the
  `OPENCODE_DANGEROUSLY_SKIP_PERMISSIONS` env var (env var proved unreliable).

Sources: [agents](https://opencode.ai/docs/agents/),
[permissions](https://opencode.ai/docs/permissions/),
[config](https://opencode.ai/docs/config/); plus live `opencode run` probes on 1.17.8.

---

## agy — researched, verified on agy (Google Antigravity CLI) 1.0.10

`agy` is **Google Antigravity CLI** (successor to the deprecated Gemini CLI), a
multi-model host — moa correctly pins a Gemini model via `--model`. Permission
surface is tiny: `--sandbox` (OS shell sandbox; does **not** block the `write_file`
tool), `--dangerously-skip-permissions`, and a settings.json `toolPermission` key
(interactive-confirmation only — useless headless, no CLI flag).

| Tier | Exact flags | Allows | Blocks | Headless behaviour |
| --- | --- | --- | --- | --- |
| **read-only** | **not expressible** (closest: `--sandbox`) | reads — but still writes + workspace shell | only shell escaping workspace/network | auto-allows writes; **NOT** read-only |
| **default** (moa default) | `--sandbox` | reads, file writes, shell confined to workspace | network + fs outside workspace (shell vector only) | auto-allows all tools; no human gate |
| **yolo** | *(drop `--sandbox`)* | everything: writes, unrestricted shell, network | nothing | auto-allows everything |

**Key nuances**
- **No true read-only mode exists.** Even `toolPermission: "strict"` still wrote a
  file headless (no human to prompt → it proceeds; upstream issue #45 confirms `-p`
  auto-approves `write_file`). moa must surface this — the existing `readonly_note`
  already does. For ticket 013, agy's "read-only" is best-effort `--sandbox` + note.
- Headless `agy -p` **auto-allows** all tools (no hang; bounded by `--print-timeout`,
  default 5m).
- **Don't** add `--dangerously-skip-permissions` to yolo: headless agy already
  auto-allows everything, so it adds nothing (and yolo drops `--sandbox` anyway).
- moa's current agy mapping is already correct; conceptually `--sandbox` is the
  *default/guardrail* tier, not read-only. `--model` composes cleanly with it.

Sources: installed `agy` 1.0.10 (`--help`, `models`, `changelog`, settings.json),
[issue #45 (read-only request)](https://github.com/google-antigravity/antigravity-cli/issues/45),
[issue #36 (sandbox bypass)](https://github.com/google-antigravity/antigravity-cli/issues/36);
all behaviour empirically tested.

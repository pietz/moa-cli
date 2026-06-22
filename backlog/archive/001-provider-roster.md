# 001 - Provider roster: add opencode, fix priority order

**Status:** done
**Touches:** `src/moa_cli/cli.py` (PROVIDERS, PRIORITY), `tests/test_moa.py`, `README.md`

## Goal

Make the provider roster and priority order match how the tool is actually used.
`-n/--num` walks the priority list and queries the first N *installed* providers,
so the order decides who gets dropped when N is small.

## Decisions (from the user)

- Priority order: **`claude` → `codex` → `agy` → `opencode`**.
  - `n=2` queries claude + codex (agy dropped).
  - `n=3` adds agy.
  - `n=4` adds opencode.
- Add **`opencode`** as provider #4.
- **Delete the standalone `gemini` provider** - dead, replaced by `agy` (confirmed by user).

## Open questions

- **Rename `antigravity` -> `agy`?** The user and the CLI both call it `agy`;
  the provider key is `antigravity`. Minor; recommend renaming the key to `agy`
  for consistency (executable is already `agy`). Not blocking.
## opencode invocation (verified, opencode v1.17.8)

- **Argv:** `["opencode", "run", PROMPT]` - prompt is a positional/variadic arg
  (like claude/agy, not a `-p` flag on `run`).
- **Model:** `-m provider/model` (e.g. `zai-coding-plan/glm-5.2`). There is **no
  universal default** - it depends on which provider the user has authed.
  Recommendation: **omit `-m`** and let opencode use the user's configured default.
  Set `default_model=""` so the builder skips `-m` and the heading shows no model.
  (A model override can be exposed later.)
- **stdin:** does NOT block on no-TTY (clean exit). Global `stdin=DEVNULL` is fine.
- **stdout:** CLEAN - only the final answer. Banner / tool chatter / ANSI go to
  stderr. **No output file needed** (unlike codex); read stdout directly. Behaves
  like `agy`.
- **Auth:** uses existing login (`~/.local/share/opencode/auth.json`); no env key
  injected. If no provider is authed the run fails - treat as a normal failure,
  no special-casing.

## Acceptance criteria

- [x] `PRIORITY = ("claude", "codex", "agy", "opencode")`; standalone `gemini` provider removed.
- [x] `opencode` Provider entry with verified executable, default model, and command builder.
- [x] `moa doctor` lists opencode under available/missing correctly.
- [x] `moa ask -n 2` queries only claude + codex; `-n 4` includes opencode.
- [x] opencode answer read directly from stdout (it's clean; no output file).
- [x] Tests: priority/selection at n=2/3/4; opencode command builder shape.
- [x] README provider table + "How agents are selected" updated.

## Notes

Keep the flat `PROVIDERS` table + small builder functions established in `cli.py`.
Adding a provider should remain a single table entry plus one builder function.

# 012 - Capture per-run telemetry to ~/.moa

**Status:** proposed
**Touches:** command orchestration in `src/moa_cli/cli.py`, configuration/storage
location in `src/moa_cli/config.py`, result records in `src/moa_cli/output.py` (or
a focused telemetry module if warranted), the matching split tests
(`tests/test_cli.py`, `tests/test_config.py`, `tests/test_output.py`), and
`README.md`. A read-back command (`moa history`/`moa stats`) is a separate later item.
**Related:** 005 (model mapping), 008 (config dir), 011 (effort), and the existing
`--json` record shape (`result_record`).

## Goal

Every `moa` run is a small, interesting experiment: different harnesses, models, and
reasoning levels answering the same task. Persist that so the user can later see
which tools they actually use, how fast/expensive each is, and which they reach for
by purpose. Append a structured record per run to a file under `~/.moa/`.

## What to capture (per agent, per run)

- run-level: timestamp, verb (`ask`/`distill`/`debate`), mode (read-only/yolo),
  timeout, number of agents, a prompt *fingerprint* (length + hash, NOT the full
  prompt by default - see privacy).
- per-agent: provider (harness), model, effort/reasoning level (from 011), status
  (ok/failed/timeout/missing), wall-clock elapsed, return code.
- if available: input/output token counts, and step/turn count. **These are the
  open question** - see below.

## Open question: do the CLIs even surface tokens/steps?

`claude -p`, `codex exec`, `opencode run` in their default text modes likely do NOT
print token usage or step counts. Some expose it only in a `--json`/structured mode.
**Build step: investigate per CLI** what usage data is reachable read-only, and only
record fields we can actually get. Tokens/steps are best-effort: record `null` when a
tool doesn't expose them rather than faking or blocking the feature on them. Do not
change a provider's invocation just to harvest metrics if it would alter the answer
or break read-only/plan mode.

## Format (decide)

- **Lean: append-only JSONL** at `~/.moa/runs.jsonl` (or `history.jsonl`). Zero deps,
  greppable, and it mirrors the existing `--json` record shape (`result_record`), so
  we can largely reuse that serializer. One JSON object per agent-result (or one per
  run with a nested `agents` array - pick one and document it).
- Alt: **SQLite** at `~/.moa/runs.db` for real querying/aggregation. Heavier; better
  if/when the read-back command does stats. Lean JSONL now, leave SQLite as an
  upgrade path the read-back ticket can take.

## Privacy / control

- On by default, but **disableable**: a config key (e.g. `telemetry = false`) and/or
  `MOA_NO_TELEMETRY=1`. Document it.
- Do NOT store full prompts or answers by default (fingerprint only). Optionally a
  `telemetry_prompts = true` opt-in later.
- This is local-only (a file in the user's home), never sent anywhere. Say so in the
  README.

## Acceptance criteria

- [ ] After each `ask`/`distill`/`debate` run, a record per agent-result is appended
      to `~/.moa/<file>` with the run-level + per-agent fields above; tokens/steps
      recorded when the CLI exposes them, `null` otherwise.
- [ ] Writing telemetry never breaks or slows a run: failures to write are swallowed
      with at most a stderr note; a missing/locked file is created/retried, not fatal.
- [ ] Disable switch (config key + env var) suppresses all writes.
- [ ] Full prompts/answers are not persisted by default (fingerprint only).
- [ ] Honors `$MOA_CONFIG_DIR` so tests write to a temp dir (reuse `config_dir()`).
- [ ] Tests: record written with expected fields, disable switch suppresses it,
      write failure is non-fatal, tokens/steps null when unavailable.
- [ ] README: short "Run history / telemetry" subsection (location, format, fields,
      how to disable, local-only).

## Notes

Keep this a thin write-side feature. The interesting analytics (per-model latency,
cost, "what I use for what") belong in a later read-back command so this ticket stays
small and shippable.

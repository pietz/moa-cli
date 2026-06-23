# 010 - Live connection check for agents

**Status:** proposed
**Touches:** command orchestration in `src/moa_cli/cli.py`, provider selection in
`src/moa_cli/providers.py`, subprocess execution in `src/moa_cli/execution.py`,
configuration in `src/moa_cli/config.py`, the matching split tests
(`tests/test_cli.py`, `tests/test_providers.py`, `tests/test_config.py`), and
`README.md`
**Related:** 001 (provider roster / `doctor`), 005 (model mapping), 008 (persistent config).

## Goal

Give the user a one-shot way to confirm that a selected agent CLI **actually
answers** with its **resolved model** before relying on it in `ask`/`distill`/`debate`.
Today `moa doctor` only checks that the executable is on `PATH` and prints each
tool's *default* model; it never invokes anything. So a broken auth, an expired
token, a wrong/renamed model id, or a misconfigured `[models]` override only
surfaces mid-run, attributed to that agent as a failure. This is exactly the gap
that motivated the manual `opencode run -m zai-coding-plan/glm-5.2 "reply OK"`
ping when wiring GLM 5.2 in as the third tool.

## Behavior

- Send a tiny fixed prompt (e.g. `Reply with exactly: OK`) to each selected agent,
  read-only, and report per agent: **OK / fail**, latency, the **resolved model**
  (config `[models]` override applied, not just the built-in default), and a short
  error excerpt on failure.
- Honor the same selection/override options as the verbs: `-p/--provider`,
  `-x/--exclude`, `-n/--num`, `-m/--model`, `-t/--timeout`, and the persisted
  config (so it tests *the council you'd actually get*). Exit non-zero if any
  selected agent fails, so it's scriptable in setup checks / CI.
- `--json` for machine-readable output, mirroring the verbs.

## Command surface (decide one)

- **Lean: `moa doctor --check`** - extends the existing command. Plain `doctor`
  stays the cheap PATH/roster listing; `--check` adds the live ping. One concept,
  discoverable. Fix the same display gap: `doctor` should show the resolved model
  for `opencode` (config override) instead of the literal "configured default".
- Alt: a dedicated `moa test [PROVIDER...]` verb if we want richer per-agent
  semantics later (e.g. test a single just-connected CLI quickly).

## Acceptance criteria

- [ ] `moa doctor --check` (or `moa test`) pings each selected agent and prints
      OK/fail + latency + resolved model; respects `-p/-x/-n/-m/-t` and config.
- [ ] Resolved-model display is correct for providers with a `[models]` override
      (no more bare "configured default" when one is pinned).
- [ ] Non-zero exit when any selected agent fails; `--json` output supported.
- [ ] Tests: a passing agent, a failing/timeout agent, model-override resolution,
      exit-code behavior. Stub the subprocess layer as existing run tests do.
- [ ] README: short "Verifying a newly connected CLI" note under doctor/setup.

## Notes

Keep the ping prompt and token budget tiny - this is a liveness probe, not a
quality eval. Reuse `run_provider`/the selection helpers rather than forking a
second spawn path, so read-only flags and model resolution stay identical to the
real verbs.

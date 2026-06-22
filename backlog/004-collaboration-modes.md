# 004 - Collaboration modes (council / synthesis / debate)

**Status:** ready (fully specced; verbs `ask`/`fuse`/`debate` and debate roles decided)
**Touches:** `src/moa_cli/cli.py` (new `--mode`, orchestration), `tests/test_moa.py`, `README.md`
**Research:** Opus literature review complete - findings + citations folded in below.

## Goal

Introduce a `--mode` knob that selects *how* the queried models work together.
This becomes the central interface; `-n`, `--exclude`, model mapping, etc. all
feed into whichever mode is active. Each mode reuses the previous one's machinery
(council -> synthesis adds an aggregator; debate is its own sequential loop).

## Modes (from the user)

- **`council`** (default) - peer review. N proposers answer the same prompt in
  parallel; all answers returned with attribution. Today's default behavior.
- **`synthesis`** - council, then one aggregator tool merges the N answers into a
  single unified answer. Today's `--synth` path, reframed as a mode.
- **`debate`** - sequential. Feed the prompt to tool 1; feed prompt + tool 1's
  response to tool 2; pass the running exchange between tools across rounds until
  they converge, or each has cleanly made its case.

## CLI shape: modes are VERBS / subcommands (per user; supersedes the --mode flag)

Each mode is a top-level verb - easy to parse, intuitive, and each command exposes
only its relevant options. No `--mode` flag, no per-mode alias booleans (this also
removes the `-m`/`-M` collision).

- `moa ask PROMPT`    - council / peer review (default mode). Streams each model's
                        answer the instant it finishes.
- `moa fuse PROMPT`   - council + a strong aggregator merges into one answer
                        (proposer answers ALWAYS blind + shuffled to it - see 002).
- `moa debate PROMPT` - sequential debate; the neutral judge's final report is the
                        response. Transcript streamed per round.
- `moa doctor`        - utility (unchanged).

**Verb wording** (decided): `ask` / `fuse` / `debate`. (`fuse` chosen over
`synth`/`merge`.) The "who fuses" selector stays `-s/--synthesizer`.

**Shared options** (all prompt verbs - implement once via a Typer callback/common
helper, don't duplicate): `-n/--num`, `-p/--provider`, `-x/--exclude`,
`-m/--model`, `-t/--timeout`, `-f/--file`, `--json`.
**Mode-specific:** `synth` -> `-s/--synthesizer`; `debate` -> `-r/--rounds`, `-j/--judge`.

**Streaming/output** (see Design principles in backlog/README): `ask` streams each
answer as it lands (already implemented); `synth` streams proposers then the merge;
`debate` streams each round then the judge's verdict. No interactive UI.

## Research-grounded design (Opus literature review)

Cross-cutting findings:
- **Heterogeneity > count.** Cross-family diversity decorrelates errors; that is
  what makes aggregation work. Our priority list is already cross-family - good.
  Homogeneous N (copies of one model at temp 0) adds little and costs N x.
- **Named when a human judges; blind + order-shuffled when a model judges.**
  Position/brand bias in model judges is measured and real (position-bias paper).
- **Surface disagreement** as a first-class signal - for coding tasks, where
  models diverge is the highest-value output.

### council (default)
- Defaults: **N = 3, heterogeneous**, fully parallel, **no shared context**
  (independence is what makes diversity real), **named attribution**.
- Voting (majority vote over discrete answers) is **parked as item 006** - it
  would be its own aggregation mode (`--mode vote`), not a council flag. Poor fit
  for this tool's mostly open-ended prompts; user leans no. See 006.
- Pitfall: never silently pick a "winner" in council - attribution is the point.

### synthesis (single-layer Mixture-of-Agents)
- Defaults: 3 heterogeneous proposers (reuse council output) -> **1 aggregation
  layer** (1 is almost always enough; expose `--layers` only for power users) ->
  **aggregator = the strongest available model** (don't cheap out here).
- **Present proposer answers to the aggregator anonymized + order-shuffled,
  always** (a model is judging; per 002 there is no toggle). Kills brand/position bias.
- **Aggregator prompt:** adopt the MoA "Aggregate-and-Synthesize" prompt nearly
  verbatim. Load-bearing clauses to keep: (a) "critically evaluate... some may be
  biased or incorrect"; (b) "do not simply replicate... offer a refined, accurate,
  comprehensive reply." Adapt "open-source models" -> "AI coding assistants".
  Align the current `SYNTHESIZER_PROMPT` to this.
- Pitfalls: aggregator echoing its own proposal (use a different model or instruct
  "integrate, don't pick"); blending a correct + incorrect answer into a worse
  hybrid for checkable tasks (prefer council `--vote` there); context cost = sum
  of all proposer outputs (watch long code answers).

### debate (opt-in, highest cost/risk)
- Defaults: **sequential**, **2 rounds** (gains plateau at 2-3; cost explodes
  ~17-29x by round 4). `--rounds` with a hard max ~4 and a warning.
- **2-3 participants, heterogeneous essential** - same-model debate underperforms
  single-agent (Degeneration-of-Thought / it just agrees with itself).
- **Model assignment (decided):** default = top 2 installed are the debaters, the
  3rd is the judge - so the default `n=3` maps to 2 debaters + 1 judge.
  `-j/--judge <provider>` overrides who judges (must not be a debater).
- **Adversarial-stance prompt:** instruct each model to find errors/weaknesses in
  prior answers and "not agree merely to reach consensus" - counters sycophancy.
- **Convergence/stop:** early-stop when answers stabilize (checkable: same final
  answer two rounds running; prose: next model reports "no substantive changes"
  or a similarity threshold). Always stop at the round cap.
- **Separate neutral judge** (a model that is NOT a debater) reads the full
  transcript and picks/synthesizes the final answer - beats self-convergence,
  which is exactly where conformity-to-wrong-answer happens. Show the judge the
  transcript **anonymized + order-shuffled**; judge prompt must weight
  correctness/verifiability over confidence and fluency.
- Pitfalls (document honestly): conformity / **correct flips to incorrect**;
  persuasiveness beating correctness; error entrenchment with more rounds. Warn
  on multiplicative token cost. **Not the default.**

## Acceptance criteria

- [ ] `--mode council|synthesis|debate`; `council` default, matches current output.
- [ ] `synthesis` reproduces (and improves) current `--synth`: strong aggregator,
      proposer answers always blind + shuffled, MoA-aligned aggregator prompt.
- [ ] `debate`: sequential passing, `--rounds` (default 2, hard max ~4 + warning),
      adversarial-stance prompt, early-stop on stabilization, separate neutral
      judge (blind + shuffled transcript), streamed per-round transcript, final
      result clearly marked, token-cost warning.
- [ ] `--json` covers all three modes (per-round records for debate).
- [ ] Tests: mode selection, synthesis-via-mode + blind default, debate round cap
      + convergence + judge selection.
- [ ] README documents modes, defaults, and the debate caveats from the literature.

## Citations (for README "why" / docs)

- Mixture-of-Agents - Wang et al. 2024, arXiv:2406.04692 (+ togethercomputer/MoA prompt).
- Multiagent Debate - Du et al. 2023, arXiv:2305.14325 (3 agents, 2 rounds).
- Encouraging Divergent Thinking (MAD, judge + tit-for-tat) - Liang et al. 2023, arXiv:2305.19118.
- Self-Consistency - Wang et al. 2022. More Agents Is All You Need - Li et al. 2024, arXiv:2402.05120.
- Critiques: "Can LLM Agents Really Debate?" arXiv:2511.07784; "Talk Isn't Always Cheap"
  arXiv:2509.05396; Conformity arXiv:2410.12428; Position bias arXiv:2406.07791.

## Notes

Largest item; reshapes the CLI. Build after 001 (roster) and 005 (model mapping),
since synthesis/debate pick specific tools + a strong aggregator/judge model.

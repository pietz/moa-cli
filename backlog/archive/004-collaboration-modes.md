# 004 - Collaboration modes: ask / distill / debate (verbs)

**Status:** done (shipped in 0.2.0; all three verbs on main).
**Touches:** `src/moa_cli/cli.py` (subcommand split + orchestration), `tests/test_moa.py`, `README.md`
**Research:** Opus literature review complete - findings + citations folded in below.

## Goal

Split the tool into three mode VERBS that select *how* the queried models work
together. Shared selection (`-n`, `-p`, `-x`, `-m`, `-t`, `-f`, `--json`) feeds all
three. Each verb reuses the previous one's machinery (distill = ask + an aggregator;
debate is its own sequential loop).

## The three verbs

- **`moa ask PROMPT`** (council / peer review, the baseline) - N proposers answer
  the same prompt in parallel; all answers returned with attribution, streamed as
  each lands. This is today's `moa ask` behavior.
- **`moa distill PROMPT`** (synthesis) - ask, then one strong aggregator merges the
  N answers into a single unified answer. Today's `--synth` path, reframed as a
  verb. Proposer answers are ALWAYS shown to the aggregator blind + shuffled (002).
- **`moa debate PROMPT`** - sequential: feed the prompt to tool 1; feed prompt +
  tool 1's answer to tool 2; pass the exchange across rounds; a neutral judge's
  final report is the response.
- `moa doctor` - utility (unchanged).

(`distill` chosen over `fuse`/`synth`/`merge`: it conveys refine-to-the-best, not
just mash-together. No `--mode` flag, no per-mode alias booleans.)

## CLI shape

- **Shared options** (all prompt verbs; implement once via a Typer callback/common
  helper, don't duplicate): `-n/--num`, `-p/--provider`, `-x/--exclude`,
  `-m/--model`, `-t/--timeout`, `-f/--file`, `--json`.
- **Verb-specific:** `distill` -> `-s/--synthesizer` (who distills); `debate` ->
  `-r/--rounds`, `-j/--judge`.
- **Streaming/output:** `ask` streams each answer as it lands (already
  implemented); `distill` streams proposers then the merged answer; `debate`
  streams each round then the judge's verdict. No interactive UI (agent-first).

## Research-grounded design (Opus literature review)

Cross-cutting:
- **Heterogeneity > count.** Cross-family diversity decorrelates errors; that is
  what makes aggregation work. Our priority list is already cross-family. Homogeneous
  N (copies of one model at temp 0) adds little and costs N x.
- **Named when a human judges; blind + order-shuffled when a model judges**
  (distill aggregator, debate judge). Position/brand bias in model judges is real.
- **Surface disagreement** as a first-class signal - for coding tasks, where models
  diverge is the highest-value output.

### ask (council, default)
- Defaults: **N = 3, heterogeneous**, fully parallel, **no shared context**
  (independence makes diversity real), **named attribution**.
- Voting (majority vote over discrete answers) is **parked as item 006** - it would
  be its own aggregation verb, not an `ask` flag. Poor fit for this tool's mostly
  open-ended prompts.
- Pitfall: never silently pick a "winner" - attribution is the point.

### distill (synthesis, single-layer Mixture-of-Agents)
- Defaults: 3 heterogeneous proposers (reuse `ask` output) -> **1 aggregation
  layer** (1 is almost always enough; expose `--layers` only for power users) ->
  **aggregator = the strongest available model** (don't cheap out).
- **Proposer answers always anonymized + order-shuffled** to the aggregator (a
  model is judging; per 002 there is no toggle). Kills brand/position bias.
- **Aggregator prompt:** adopt the MoA "Aggregate-and-Synthesize" prompt nearly
  verbatim. Keep the load-bearing clauses: (a) "critically evaluate... some may be
  biased or incorrect"; (b) "do not simply replicate... offer a refined, accurate,
  comprehensive reply." Adapt "open-source models" -> "AI coding assistants".
  Align the current `SYNTHESIZER_PROMPT` to this.
- Pitfalls: aggregator echoing its own proposal (use a different model or instruct
  "integrate, don't pick"); blending correct + incorrect into a worse hybrid on
  checkable tasks; context cost = sum of all proposer outputs (watch long code).

### debate (opt-in, highest cost/risk)
- Defaults: **sequential**, **2 rounds** (gains plateau at 2-3; cost explodes
  ~17-29x by round 4). `-r/--rounds` with a hard max ~4 and a warning.
- **2-3 participants, heterogeneous essential** - same-model debate underperforms
  single-agent (Degeneration-of-Thought / it agrees with itself).
- **Model assignment (decided):** default = top 2 installed are the debaters, the
  3rd is the judge - so default `n=3` maps to 2 debaters + 1 judge. `-j/--judge
  <provider>` overrides the judge (must not be a debater).
- **Adversarial-stance prompt:** instruct each model to find errors/weaknesses in
  prior answers and "not agree merely to reach consensus" - counters sycophancy.
- **Convergence/stop:** early-stop when answers stabilize (checkable: same final
  answer two rounds running; prose: next model reports "no substantive changes").
  Always stop at the round cap.
- **Separate neutral judge** (NOT a debater) reads the full transcript and
  picks/synthesizes the final answer - beats self-convergence, which is exactly
  where conformity-to-wrong-answer happens. Show the judge the transcript
  **anonymized + order-shuffled**; the judge prompt must weight
  correctness/verifiability over confidence and fluency.
- Pitfalls (document honestly): conformity / **correct flips to incorrect**;
  persuasiveness beating correctness; error entrenchment with more rounds. Warn on
  multiplicative token cost. **Not the default.**

## Acceptance criteria

- [ ] `moa ask|distill|debate` subcommands; `ask` matches current council output.
- [ ] `distill` reproduces (and improves) current `--synth`: strong aggregator,
      proposer answers always blind + shuffled, MoA-aligned aggregator prompt. The
      old `--synth` flag is removed (verbs replace it).
- [ ] `debate`: sequential passing, `-r/--rounds` (default 2, hard max ~4 +
      warning), adversarial-stance prompt, early-stop on stabilization, separate
      neutral judge (blind + shuffled transcript), streamed per-round transcript,
      final result clearly marked, token-cost warning.
- [ ] Shared options work identically across all three verbs (one helper).
- [ ] `--json` covers all three verbs (per-round records for debate).
- [ ] Tests: verb dispatch, distill blind-by-default, debate round cap +
      convergence + judge selection.
- [ ] README documents the verbs, defaults, and the debate caveats.

## Citations (for README "why" / docs)

- Mixture-of-Agents - Wang et al. 2024, arXiv:2406.04692 (+ togethercomputer/MoA prompt).
- Multiagent Debate - Du et al. 2023, arXiv:2305.14325 (3 agents, 2 rounds).
- Encouraging Divergent Thinking (MAD, judge + tit-for-tat) - Liang et al. 2023, arXiv:2305.19118.
- Self-Consistency - Wang et al. 2022. More Agents Is All You Need - Li et al. 2024, arXiv:2402.05120.
- Critiques: "Can LLM Agents Really Debate?" arXiv:2511.07784; "Talk Isn't Always Cheap"
  arXiv:2509.05396; Conformity arXiv:2410.12428; Position bias arXiv:2406.07791.

## Notes

Largest item; reshapes the CLI from one `ask` command into three verbs. Shipped
0.1.0 still uses `moa ask` + `--synth`; the verbs land in 0.2.0.

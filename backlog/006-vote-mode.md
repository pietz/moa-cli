# 006 - Vote mode (parked)

**Status:** parked (idea; user leans no)

## Idea

Majority voting / self-consistency: run N models, extract each one's final
**discrete** answer, return the majority plus an agreement count. A pure
aggregation strategy (no LLM aggregator needed), sibling to `synthesis`. Would be
`--mode vote`, not a council flag.

## Why parked

- This tool's prompts are mostly **open-ended** (code review, design calls,
  "which is better and why"). Voting needs a **checkable/comparable discrete
  answer** (a number, yes/no, a single choice) to tally - most moa prompts have none.
- Extracting a discrete answer from free-form CLI output is fragile.
- `synthesis` already covers prose aggregation and implicitly reflects agreement.

## Evidence (if revisited)

Self-consistency (+6-18% on reasoning) and "More Agents Is All You Need" show real
gains - but specifically on tasks with a verifiable answer. Reserve for that.

## If we ever build it

- `--mode vote`; gate to discrete-answer prompts; report the tally + agreement level.
- Lighter, broadly-useful cousin (works for prose too): **highlight where the
  council agrees vs diverges**. That's a council output enhancement, not voting.

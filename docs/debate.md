# `moa debate` reference

`debate` is the opt-in, highest-cost mode. Instead of fanning out in parallel,
it runs a sequential, adversarial exchange overseen by a **moderator** that
checks for convergence between rounds and writes the final answer.

```
   round 1:  A answers cold
             B critiques A, then answers
   ┌─ round k:  each sees the other's latest, responds in turn
   │            moderator: DONE (converged) or CONTINUE?
   └─ loops up to N rounds (default 2, hard max 4)
   verdict:  moderator reads the full shuffled transcript, writes the final answer
```

## Roles

The top **2** selected agents are the debaters. The **moderator** runs the
per-round convergence check and writes the verdict; by default it is the
top-priority selected agent (so the default 2-agent debate has agent #1 also
moderate). Debate only needs **2 agents**; with fewer it exits cleanly rather
than silently degrading. For a **neutral** moderator that doesn't also debate,
select a third agent and pin it: `moa debate -n 3 --moderator <provider>` (the
moderator must be one of the selected agents). The moderator only ever sees the
transcript **anonymized + shuffled**, so even when it is itself a debater it
can't favour its own answer.

## Rounds

`-r/--rounds` defaults to **2** (gains plateau around 2-3 rounds while token
cost grows multiplicatively) and is hard-capped at **4** - higher values are
clamped with a warning on stderr.

## The loop

Round 1: debater A answers cold; debater B sees A's answer with an
adversarial-stance instruction ("identify errors/weaknesses before giving your
own answer; do not agree merely to reach consensus"). Each later round, every
debater sees the other's latest answer and responds in the same spirit. After
each non-final round the **moderator** reads the debaters' latest answers and
replies `DONE` (they've converged or fully aired their disagreement) or
`CONTINUE`; a `DONE` stops the debate before the cap.

## The verdict

The moderator reads the full transcript - presented **anonymized and
order-shuffled** (so brand/position bias is killed, even when the moderator was
a debater) - and writes the final answer. Its prompt instructs it to weigh
correctness and evidence **above** confidence and fluency. The verdict is the
final block (`──── verdict · moderator <name> · ... ────`).

## Streaming / output

Each debater's turn streams as it completes (`──── round N · <provider> · ...
────`), then the moderator's verdict last. `--json` emits a
`{"type": "debate_turn", "round": N, ...}` record per turn plus a final
`{"type": "verdict", "moderator": "<name>", ...}` record.

## Safety

Debaters and the moderator run in the same read-only (or `--yolo`) mode as the
other verbs - there is no permission bypass.

## When to reach for debate

Debate is the costliest mode (roughly `debaters × rounds` calls, plus a
moderator check per round and the verdict) **and the least reliably
beneficial.** The research is mixed-to-negative: multi-agent debate can converge
on a *wrong* answer through conformity, a confident-but-incorrect debater can
win on persuasiveness over correctness, and more rounds can entrench an error
rather than fix it. The moderator and the adversarial-stance prompt are there to
fight these failure modes, but they do not eliminate them. For most questions,
`ask` or `distill` is the better default; reach for `debate` when you
specifically want to surface and stress-test disagreement.

See *Can LLM Agents Really Debate?* arXiv:2511.07784, *Talk Isn't Always Cheap*
arXiv:2509.05396, and the conformity/position-bias work cited in the design
notes.

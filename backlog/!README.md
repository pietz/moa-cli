# Backlog

Build-ready feature specs for `moa`. Each numbered file is a self-contained unit
of work that a subagent can pick up cold and implement end to end
(code + tests + README), without needing the design conversation that produced it.

This file documents both the format and the workflow, so the folder is
self-contained: any agent that opens it can run the loop without outside context.

## How it works

- One file per feature: `NNN-slug.md`.
- During design, the orchestrator captures decisions here. When an item is
  `ready`, a subagent builds it from the spec alone.
- Keep each item self-contained: goal, decisions, acceptance criteria, files to
  touch, tests. If a builder would need to ask a question, the spec isn't `ready`.
- Keep specs intent-focused (what + why + how to verify). "Files to touch" is a
  hint, not a mandate; the builder may find better ones.

## The loop

The orchestrator does not write code. It runs this loop:

1. Pick the next `ready` ticket (respect dependencies). Never implement directly.
2. Dispatch ONE subagent with the ticket file as its entire brief: plan, build,
   run tests. One ticket = one subagent = one branch/PR. Use a fresh session.
3. The builder returns a terse structured report (see below), not prose.
4. Verify before `done` (see Definition of done). On failure, leave notes in the
   ticket and re-dispatch.
5. Flip the status, update the index, archive the ticket, move to the next.

Let the orchestrator decide how to split large work into tickets and which are
parallel vs stacked. Do not pre-define agent roles or personas.

## Definition of done

A ticket is `done` only when:

- All acceptance criteria are met.
- Tests and type checks pass.
- A fresh-eyes reviewer (a separate subagent, not the builder) has signed off.

Assume the first pass is ~85% correct. The review pass is not optional.

## Builder report format

Each builder returns one block, no prose:

    ticket: NNN-slug
    status: done | blocked | needs-review
    files:  path/one.py, path/two.py
    tests:  pass | fail (detail)
    notes:  anything the orchestrator must know (surprises, follow-ups)

## Status legend

- `proposed` - under discussion, may still change
- `ready` - decided, safe to hand to a builder
- `building` - a subagent is implementing it
- `done` - merged into `cli.py` + tests + README, then archived
- `parked` - deferred, not currently planned

## Archiving

When a ticket is `done`, move it to `backlog/archive/` so the active set and the
index below stay small. Closed work stays recoverable in git history.

## Index

Active tickets only; `done` items move to `archive/`.

| ID  | Item                                          | Status   |
|-----|-----------------------------------------------|----------|
| 004 | Collaboration modes (ask/distill/debate verbs)| ready    |
| 006 | Vote mode                                     | parked   |
| 007 | Agent skill for skills.sh (depends on 004)    | proposed |
| 008 | Persistent config / default settings          | proposed |

Archived (done, shipped in 0.1.0): 001 roster, 002 attribution, 003 exclusion, 005 model mapping.

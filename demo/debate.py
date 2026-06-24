"""Staged `moa debate` demo: should this project move SQLite -> Postgres?"""

import asyncio

from _engine import Turn, play_debate

ROUNDS = [
    (
        1,
        Turn(
            "claude",
            "opus",
            "It's premature unless you're hitting a real limit. SQLite handles a lot "
            "of load for a read-heavy app with a single writer, and it means zero "
            "ops. The signals that justify moving: concurrent writers contending for "
            "the database lock, needing more than one app server, or wanting "
            "Postgres-only features like rich types or full-text search. Absent "
            "those, switching now is cost without payoff.",
            elapsed=4.0,
        ),
    ),
    (
        1,
        Turn(
            "codex",
            "gpt-5.5",
            "Agree on the trigger, but weigh the migration cost too. Moving later "
            "means porting schema quirks from SQLite's loose typing, rewriting "
            "queries that lean on its behavior, and backfilling data under load - all "
            "harder once the dataset is big. So if you already see write contention, "
            "or know multi-server is coming this quarter, do it now while it's small. "
            "Otherwise stay on SQLite and just keep the schema portable.",
            elapsed=4.6,
        ),
    ),
]

CONVERGENCE = Turn("claude", "opus", "DONE", elapsed=1.6)

VERDICT = Turn(
    "claude",
    "opus",
    "Don't migrate yet - but make the move cheap to do later.\n\n"
    "SQLite is the right default while you have a single writer and one app server: "
    "it costs nothing to run and handles more load than people expect. Switch to "
    "Postgres when a concrete signal appears - write contention on the database "
    "lock, a need to run multiple app servers, or a Postgres-only feature you "
    "actually want.\n\n"
    "To keep that switch painless, avoid leaning on SQLite's loose typing and keep "
    "your queries standard now, so the migration is a config change, not a rewrite.",
    elapsed=3.6,
)

if __name__ == "__main__":
    asyncio.run(play_debate(ROUNDS, CONVERGENCE, VERDICT))

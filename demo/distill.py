"""Staged `moa distill` demo: a top-5 list, consolidated from the council.

The merged answer is the consensus made visible - distill reconciles three
overlapping lists into one. Content is plain prose (no markdown bold/headers)
so it reads cleanly in the terminal, the way real `moa` output prints.
"""

import asyncio

from _engine import Turn, play_distill

PROPOSERS = [
    Turn("codex", "gpt-5.5", "", elapsed=2.6),
    Turn("claude", "opus", "", elapsed=3.8),
    Turn("agy", "Gemini 3.5 Flash (High)", "", elapsed=5.0),
]

SYNTHESIS = Turn(
    "claude",
    "opus",
    "Five principles the strongest APIs share:\n\n"
    "1. Consistency - predictable naming and structure across endpoints.\n"
    "2. Meaningful errors - correct status codes and a clear, parseable body.\n"
    "3. Versioning from day one - evolve without breaking existing callers.\n"
    "4. Pagination and filtering - never return an unbounded collection.\n"
    "5. Docs with real examples, kept beside the code so they stay current.\n\n"
    "The throughline: design for the client, not your database's shape.",
    elapsed=4.2,
)

if __name__ == "__main__":
    asyncio.run(play_distill(PROPOSERS, SYNTHESIS))

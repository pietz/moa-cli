"""Staged `moa ask` demo: each agent names a different underrated CLI tool."""

import asyncio

from _engine import Turn, play_ask

TURNS = [
    Turn(
        "codex",
        "gpt-5.5",
        "fd - a faster, friendlier `find` with sane defaults, gitignore awareness, "
        "and an intuitive syntax you actually remember.",
        elapsed=2.6,
    ),
    Turn(
        "claude",
        "opus",
        "entr - reruns any command the instant files change, giving you a "
        "test/reload loop in one line with zero config or watch boilerplate.",
        elapsed=3.4,
    ),
    Turn(
        "agy",
        "Gemini 3.5 Flash (High)",
        "jq - slices, filters, and reshapes JSON right in the shell, so you stop "
        "piping API responses into a throwaway Python script.",
        elapsed=4.3,
    ),
]

if __name__ == "__main__":
    asyncio.run(play_ask(TURNS))

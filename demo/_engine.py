"""Staged playback engine for the README demos.

This does NOT call the real agents. It reuses moa's own StatusLine spinner and
block renderers (so the output is byte-for-byte what `moa` produces) but feeds
them canned content and timing we control - fast, deterministic, no API cost.

The per-mode scripts (ask.py / distill.py / debate.py) define the content and
call one of the play_* coroutines below.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
from dataclasses import dataclass

# Import moa's real rendering code from the source tree.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from moa_cli.execution import RunResult  # noqa: E402
from moa_cli.output import (  # noqa: E402
    emit,
    render_block,
    render_debate_turn_block,
    render_synthesis_block,
    render_verdict_block,
)
from moa_cli.status import StatusLine  # noqa: E402


@dataclass
class Turn:
    """One staged agent response: who, what, and how long it 'took'."""

    provider: str
    model: str
    text: str
    elapsed: float  # seconds shown in the block header and reached by the spinner


def _call_label(what: str, name: str, model: str) -> str:
    base = f"{name} ({model})" if model else name
    return f"{what} · {base}" if what else base


def _result(turn: Turn) -> RunResult:
    return RunResult(turn.provider, turn.model, "ok", turn.text, "", turn.elapsed, 0)


async def play_ask(turns: list[Turn]) -> None:
    """Council: all spin in parallel, each line vanishes as its answer reveals."""
    status = StatusLine()
    for turn in turns:
        status.add(turn.provider, _call_label("", turn.provider, turn.model))
    status.start()
    try:
        prev = 0.0
        for turn in sorted(turns, key=lambda t: t.elapsed):
            await asyncio.sleep(max(0.0, turn.elapsed - prev))
            prev = turn.elapsed
            status.clear()
            emit(render_block(_result(turn), plain=False))
            status.remove(turn.provider)
    finally:
        await status.stop()


async def play_distill(
    proposers: list[Turn], synthesizer: Turn, gap: float = 0.6
) -> None:
    """Proposers get a ✓ as they land; then the synthesizer spins and reveals."""
    status = StatusLine()
    for turn in proposers:
        status.add(turn.provider, _call_label("", turn.provider, turn.model))
    status.start()
    try:
        prev = 0.0
        for turn in sorted(proposers, key=lambda t: t.elapsed):
            await asyncio.sleep(max(0.0, turn.elapsed - prev))
            prev = turn.elapsed
            status.complete(turn.provider)  # ✓ stays on screen
        await asyncio.sleep(gap)
        status.add(
            "synthesis",
            _call_label("synthesis", synthesizer.provider, synthesizer.model),
        )
        await asyncio.sleep(synthesizer.elapsed)
        status.clear()
        emit(render_synthesis_block(_result(synthesizer), synthesizer.provider, plain=False))
    finally:
        await status.stop()


async def play_debate(
    rounds: list[tuple[int, Turn]],
    convergence: Turn,
    verdict: Turn,
) -> None:
    """Sequential turns, then a convergence check, then the moderator verdict."""
    for round_num, turn in rounds:
        status = StatusLine()
        status.add(turn.provider, _call_label(f"round {round_num}", turn.provider, turn.model))
        status.start()
        await asyncio.sleep(turn.elapsed)
        await status.stop()
        emit(render_debate_turn_block(_result(turn), round_num, plain=False))

    status = StatusLine()
    status.add(
        convergence.provider,
        _call_label("convergence", convergence.provider, convergence.model),
    )
    status.start()
    await asyncio.sleep(convergence.elapsed)
    await status.stop()
    note = f"Moderator {convergence.provider}: converged; writing the final answer..."
    sys.stderr.write(note + "\n")
    sys.stderr.flush()

    status = StatusLine()
    status.add(
        verdict.provider,
        _call_label("verdict", verdict.provider, verdict.model),
    )
    status.start()
    await asyncio.sleep(verdict.elapsed)
    await status.stop()
    emit(render_verdict_block(_result(verdict), verdict.provider, plain=False))

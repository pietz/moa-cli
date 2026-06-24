"""A TTY-only live status line for in-flight provider calls.

Renders an in-place spinner on stderr while one or more agents are running, so a
human at a terminal sees work progressing. It is a strict no-op when stderr is not
a TTY (piped, logged, or read by an agent): nothing is written and no tasks are
created, so the agent-facing stdout/JSONL output is byte-identical to a run with no
status line at all. It never touches stdout.
"""

from __future__ import annotations

import asyncio
import sys
import time

_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_CHECK = "✓"
_CLEAR_BELOW = "\033[J"  # clear from the cursor to the end of the screen


def format_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    remainder = int(seconds % 60)
    return f"{minutes}m{remainder:02d}s"


class StatusLine:
    """An in-place stderr spinner for active calls; a no-op off a TTY."""

    def __init__(self, stream=None, interval: float = 0.1) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self.active = self._stream.isatty()
        self._interval = interval
        # key -> (label, started, finished_at | None). A finished job stays on
        # screen with a ✓ and its frozen elapsed until it is removed/cleared.
        self._jobs: dict[str, tuple[str, float, float | None]] = {}
        self._task: asyncio.Task | None = None
        self._frame = 0
        self._shown = False
        self._prev_lines = 0

    def add(self, key: str, label: str) -> None:
        if not self.active:
            return
        self._jobs[key] = (label, time.monotonic(), None)
        self._draw()

    def complete(self, key: str) -> None:
        """Mark a job done: it keeps its place with a ✓ and frozen elapsed."""
        if not self.active:
            return
        job = self._jobs.get(key)
        if job is None:
            return
        label, started, _ = job
        self._jobs[key] = (label, started, time.monotonic())
        self._draw()

    def remove(self, key: str) -> None:
        if not self.active:
            return
        self._jobs.pop(key, None)
        if self._jobs:
            self._draw()
        else:
            self.clear()

    def clear(self) -> None:
        """Wipe the status block; call before emitting a stdout block."""
        if not self.active or not self._shown:
            return
        self._stream.write(self._reset_cursor())
        self._stream.flush()
        self._shown = False
        self._prev_lines = 0

    def _reset_cursor(self) -> str:
        """Return to the start of the drawn block and clear it (or just "\\r")."""
        seq = "\r"
        if self._shown:
            if self._prev_lines > 1:
                seq += f"\033[{self._prev_lines - 1}A"
            seq += _CLEAR_BELOW
        return seq

    def start(self) -> None:
        """Begin the redraw ticker (must run inside a live event loop)."""
        if not self.active or self._task is not None:
            return
        self._task = asyncio.create_task(self._tick())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.clear()

    async def _tick(self) -> None:
        try:
            while True:
                self._draw()
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            raise

    def _draw(self) -> None:
        if not self.active or not self._jobs:
            return
        now = time.monotonic()
        lines: list[str] = []
        for index, (label, started, finished_at) in enumerate(self._jobs.values()):
            if finished_at is None:
                marker = _FRAMES[(self._frame + index) % len(_FRAMES)]
                elapsed = now - started
            else:
                marker = _CHECK
                elapsed = finished_at - started
            lines.append(f"{marker} {label} {format_elapsed(elapsed)}")
        self._frame += 1
        self._stream.write(self._reset_cursor() + "\n".join(lines))
        self._stream.flush()
        self._prev_lines = len(lines)
        self._shown = True

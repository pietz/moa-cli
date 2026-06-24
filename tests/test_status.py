import asyncio

from moa_cli.status import StatusLine, format_elapsed


class _FakeTTY:
    """A minimal stderr stand-in that reports as a TTY."""

    def __init__(self) -> None:
        self.written: list[str] = []

    def isatty(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self.written.append(text)
        return len(text)

    def flush(self) -> None:
        pass


class _Pipe:
    """A non-TTY stand-in (like a pipe or file)."""

    def __init__(self) -> None:
        self.written: list[str] = []

    def isatty(self) -> bool:
        return False

    def write(self, text: str) -> int:
        self.written.append(text)
        return len(text)

    def flush(self) -> None:
        pass


# --- format_elapsed ---------------------------------------------------------


def test_format_elapsed_under_a_minute() -> None:
    assert format_elapsed(0.0) == "0.0s"
    assert format_elapsed(4.16) == "4.2s"
    assert format_elapsed(59.9) == "59.9s"


def test_format_elapsed_at_or_over_a_minute() -> None:
    assert format_elapsed(60.0) == "1m00s"
    assert format_elapsed(125.0) == "2m05s"
    assert format_elapsed(900.0) == "15m00s"


# --- off a TTY: strict no-op ------------------------------------------------


def test_inactive_writes_nothing_and_never_raises() -> None:
    stream = _Pipe()
    status = StatusLine(stream=stream)
    assert status.active is False
    status.add("claude", "claude (opus)")
    status.remove("claude")
    status.clear()
    status.start()  # no running loop, but inactive -> must not create a task
    assert stream.written == []
    assert status._task is None


# --- on a TTY: draws and clears --------------------------------------------


def test_add_draws_label_with_spinner_and_elapsed() -> None:
    stream = _FakeTTY()
    status = StatusLine(stream=stream)
    status.add("claude", "claude (opus)")
    rendered = "".join(stream.written)
    assert "claude (opus)" in rendered
    assert rendered.startswith("\r")  # in-place line
    assert any(frame in rendered for frame in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
    assert "s" in rendered  # elapsed suffix


def test_two_jobs_render_on_separate_lines() -> None:
    stream = _FakeTTY()
    status = StatusLine(stream=stream)
    status.add("claude", "claude (opus)")
    status.add("codex", "codex (gpt-5.5)")
    rendered = "".join(stream.written)
    assert "claude (opus)" in rendered
    assert "codex (gpt-5.5)" in rendered
    # The two jobs are stacked vertically, not joined on one line.
    last_draw = stream.written[-1]
    assert "claude (opus)" in last_draw and "codex (gpt-5.5)" in last_draw
    assert "\n" in last_draw
    # A subsequent redraw of the 2-line block walks the cursor back up one line.
    stream.written.clear()
    status._draw()
    assert "\033[1A" in stream.written[-1]


def test_clear_wipes_a_drawn_line_and_is_idempotent() -> None:
    stream = _FakeTTY()
    status = StatusLine(stream=stream)
    status.add("claude", "claude (opus)")
    assert status._shown is True
    status.clear()
    assert "".join(stream.written).endswith("\r\033[J")
    assert status._shown is False
    before = len(stream.written)
    status.clear()  # nothing shown now -> no-op
    assert len(stream.written) == before


def test_remove_last_job_clears_others_redraw() -> None:
    stream = _FakeTTY()
    status = StatusLine(stream=stream)
    status.add("claude", "claude (opus)")
    status.add("codex", "codex (gpt-5.5)")
    stream.written.clear()

    status.remove("claude")  # one job remains -> redraw
    rendered = "".join(stream.written)
    assert "codex (gpt-5.5)" in rendered
    assert "claude" not in rendered

    stream.written.clear()
    status.remove("codex")  # none remain -> clear the block
    assert "".join(stream.written) == "\r\033[J"


# --- ticker lifecycle -------------------------------------------------------


def test_start_stop_runs_and_cancels_the_ticker() -> None:
    stream = _FakeTTY()
    status = StatusLine(stream=stream, interval=0.01)

    async def run() -> None:
        status.add("claude", "claude (opus)")
        status.start()
        assert status._task is not None
        await asyncio.sleep(0.05)  # let a few ticks land
        await status.stop()
        assert status._task is None

    asyncio.run(run())
    # The ticker redrew the line several times while running.
    assert sum(s.startswith("\r") for s in stream.written) >= 2


def test_stop_is_a_clean_noop_without_start() -> None:
    stream = _FakeTTY()
    status = StatusLine(stream=stream)
    status.add("claude", "claude (opus)")

    async def run() -> None:
        await status.stop()  # no task -> just clears

    asyncio.run(run())
    assert status._task is None

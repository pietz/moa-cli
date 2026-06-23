"""Subprocess execution for agent providers."""

from __future__ import annotations

import asyncio
import os
import signal
import tempfile
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .providers import Provider

Status = Literal["ok", "failed", "timeout", "missing"]


@dataclass(frozen=True)
class RunResult:
    provider: str
    model: str
    status: Status
    stdout: str
    stderr: str
    elapsed: float
    returncode: int | None


def _decode(data: bytes | None) -> str:
    return (data or b"").decode(errors="replace").strip()


async def _terminate(process: asyncio.subprocess.Process) -> None:
    """Kill the process group with SIGTERM, then SIGKILL after two seconds."""
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
        return
    except asyncio.TimeoutError:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except Exception:
        process.kill()
    await process.wait()


async def run_provider(
    provider: Provider,
    prompt: str,
    timeout: float,
    model: str | None = None,
    yolo: bool = False,
    effort: str | None = None,
) -> RunResult:
    model = model or provider.default_model
    out_file: str | None = None
    if provider.uses_output_file:
        handle, out_file = tempfile.mkstemp(prefix="moa-", suffix=".txt")
        os.close(handle)

    start = time.monotonic()
    process: asyncio.subprocess.Process | None = None
    try:
        try:
            process = await asyncio.create_subprocess_exec(
                *provider.build(
                    prompt,
                    model,
                    out_file,
                    provider.perm_args(yolo),
                    provider.effort_args(effort),
                ),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=provider.env(),
                start_new_session=True,
            )
        except FileNotFoundError:
            return RunResult(
                provider.name,
                model,
                "missing",
                "",
                f"{provider.executable} is not installed.",
                time.monotonic() - start,
                None,
            )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            await _terminate(process)
            return RunResult(
                provider.name,
                model,
                "timeout",
                "",
                f"Timed out after {timeout:g}s.",
                time.monotonic() - start,
                None,
            )
        except asyncio.CancelledError:
            await _terminate(process)
            raise

        error = _decode(stderr)
        answer = (
            Path(out_file).read_text(encoding="utf-8", errors="replace").strip()
            if out_file
            else _decode(stdout)
        )
        status: Status = "ok" if process.returncode == 0 and answer else "failed"
        return RunResult(
            provider.name,
            model,
            status,
            answer,
            error,
            time.monotonic() - start,
            process.returncode,
        )
    finally:
        if out_file:
            try:
                os.unlink(out_file)
            except OSError:
                pass


async def stream(
    providers: list[Provider],
    prompt: str,
    timeout: float,
    models: dict[str, str] | None = None,
    yolo: bool = False,
    efforts: dict[str, str] | None = None,
) -> AsyncIterator[RunResult]:
    """Run providers in parallel and yield each result as it finishes."""
    models = models or {}
    efforts = efforts or {}
    tasks = [
        asyncio.create_task(
            run_provider(
                provider,
                prompt,
                timeout,
                models.get(provider.name),
                yolo,
                efforts.get(provider.name),
            )
        )
        for provider in providers
    ]
    for completed in asyncio.as_completed(tasks):
        yield await completed

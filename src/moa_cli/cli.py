"""moa - ask one question to multiple local AI coding CLIs in parallel.

Everything lives in this one module on purpose: the tool is small, and a single
file is easier to read end to end than five files that each do one small thing.
The sections below (providers / runner / synthesis / render / cli) are the seams
to split on if it ever genuinely outgrows one file.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import shutil
import signal
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

import typer

# --------------------------------------------------------------------------- #
# Providers: each agent CLI we know how to drive.
# --------------------------------------------------------------------------- #

# A command builder turns (prompt, model, output_file) into an argv list.
# output_file is a path the CLI may be told to write its final answer to; it is
# None for providers that answer cleanly on stdout. Only codex uses it.
CommandBuilder = Callable[[str, str, str | None], list[str]]


@dataclass(frozen=True)
class Provider:
    name: str
    executable: str
    default_model: str
    build: CommandBuilder
    # Env keys to drop before spawning. claude refuses to run nested inside
    # Claude Code unless CLAUDECODE is cleared, so moa can call it from an agent.
    unset_env: tuple[str, ...] = ()
    # codex's stdout is session chrome; its real answer goes to an output file.
    uses_output_file: bool = False

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.setdefault("NO_COLOR", "1")
        env.setdefault("TERM", "dumb")
        for key in self.unset_env:
            env.pop(key, None)
        return env


def _claude(prompt: str, model: str, _out: str | None) -> list[str]:
    return ["claude", "--model", model, "-p", prompt]


def _codex(prompt: str, model: str, out: str | None) -> list[str]:
    cmd = ["codex", "exec", "-m", model, "--skip-git-repo-check", "--color", "never"]
    if out:
        cmd += ["-o", out]
    cmd.append(prompt)
    return cmd


def _gemini(prompt: str, model: str, _out: str | None) -> list[str]:
    return ["gemini", "-m", model, "-p", prompt]


def _antigravity(prompt: str, model: str, _out: str | None) -> list[str]:
    # agy also hosts Claude/GPT-OSS models, so we pin a Gemini model explicitly
    # to keep the panel diverse. Without --model it defaults to Gemini Flash.
    return ["agy", "--model", model, "-p", prompt]


PROVIDERS: dict[str, Provider] = {
    "claude": Provider("claude", "claude", "opus", _claude, unset_env=("CLAUDECODE",)),
    "codex": Provider("codex", "codex", "gpt-5.5", _codex, uses_output_file=True),
    "gemini": Provider("gemini", "gemini", "gemini-3.1-pro-preview", _gemini),
    "antigravity": Provider("antigravity", "agy", "Gemini 3.1 Pro (High)", _antigravity),
}

# Auto-selection order, roughly by popularity. -n/--num walks this list and
# takes the first N that are actually installed.
PRIORITY: tuple[str, ...] = ("claude", "codex", "gemini", "antigravity")


def _installed(name: str) -> bool:
    return shutil.which(PROVIDERS[name].executable) is not None


def available_provider_names() -> list[str]:
    return [name for name in PRIORITY if _installed(name)]


def missing_provider_names() -> list[str]:
    return [name for name in PRIORITY if not _installed(name)]


def select_for_run(num: int, names: tuple[str, ...] | None) -> tuple[list[Provider], list[str]]:
    """Pick providers to run, returning (to_run, skipped_because_not_installed).

    With an explicit `names` list we honour it in order, skipping any not
    installed. Otherwise we take the first `num` installed providers from
    PRIORITY.
    """
    if names:
        unknown = [name for name in names if name not in PROVIDERS]
        if unknown:
            raise ValueError(f"Unknown provider(s): {', '.join(unknown)}")
        chosen = [PROVIDERS[name] for name in names if _installed(name)]
        skipped = [name for name in names if not _installed(name)]
        return chosen, skipped
    return [PROVIDERS[name] for name in available_provider_names()[:num]], []


# --------------------------------------------------------------------------- #
# Runner: spawn each CLI as a parallel subprocess and collect its answer.
# --------------------------------------------------------------------------- #

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
    """Kill the whole process group (SIGTERM, then SIGKILL) on timeout."""
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


async def run_provider(provider: Provider, prompt: str, timeout: float) -> RunResult:
    model = provider.default_model
    out_file: str | None = None
    if provider.uses_output_file:
        handle, out_file = tempfile.mkstemp(prefix="moa-", suffix=".txt")
        os.close(handle)

    start = time.monotonic()
    try:
        try:
            process = await asyncio.create_subprocess_exec(
                *provider.build(prompt, model, out_file),
                # DEVNULL is essential: codex and agy block forever on an
                # inherited TTY stdin, burning the entire timeout otherwise.
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=provider.env(),
                start_new_session=True,  # own process group, so _terminate can killpg
            )
        except FileNotFoundError:
            return RunResult(provider.name, model, "missing", "", f"{provider.executable} is not installed.", time.monotonic() - start, None)

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            await _terminate(process)
            return RunResult(provider.name, model, "timeout", "", f"Timed out after {timeout:g}s.", time.monotonic() - start, None)

        elapsed = time.monotonic() - start
        error = _decode(stderr)
        # For output-file providers the file is authoritative; stdout is noise,
        # so an empty file means failure rather than reporting that noise.
        if out_file:
            answer = Path(out_file).read_text(encoding="utf-8", errors="replace").strip()
        else:
            answer = _decode(stdout)
        status: Status = "ok" if process.returncode == 0 and answer else "failed"
        return RunResult(provider.name, model, status, answer, error, elapsed, process.returncode)
    finally:
        if out_file:
            try:
                os.unlink(out_file)
            except OSError:
                pass


async def stream(providers: list[Provider], prompt: str, timeout: float) -> AsyncIterator[RunResult]:
    """Run every provider in parallel, yielding each result as it finishes."""
    tasks = [asyncio.create_task(run_provider(p, prompt, timeout)) for p in providers]
    for completed in asyncio.as_completed(tasks):
        yield await completed


# --------------------------------------------------------------------------- #
# Synthesis: merge the collected answers into one unified answer.
# --------------------------------------------------------------------------- #

SYNTHESIZER_PROMPT = """You are the synthesizer in a mixture-of-agents system. You are given a \
user's question and several independent answers produced by different AI assistants. Produce a \
single, unified answer that is more accurate, complete, and useful than any individual response.

Guidelines:
- Identify where the answers agree, where they complement each other, and where they conflict.
- Resolve conflicts by the quality of reasoning and evidence; use agreement as a tiebreaker.
- Keep what is correct and valuable; drop what is wrong, redundant, or unsupported.
- Write a clear, well-structured, self-contained answer. Do not refer to "Response A", the other \
answers, or the fact that you are synthesizing. Just give the best possible answer.
- Do not invent information that none of the responses support."""


def choose_synthesizer(choice: str, candidates: list[str], rng: random.Random | None = None) -> str:
    """Resolve --synthesizer against the providers that actually ran.

    "auto"/"first" takes the highest-priority candidate, "random" picks one at
    random, anything else must name a known provider.
    """
    if not candidates:
        raise ValueError("No candidate providers available to synthesize.")
    if choice in ("auto", "first"):
        return candidates[0]
    if choice == "random":
        return (rng or random).choice(candidates)
    if choice in PROVIDERS:
        return choice
    raise ValueError(f"Unknown synthesizer: {choice}")


def build_synthesis_prompt(
    question: str,
    results: list[RunResult],
    blind: bool,
    rng: random.Random | None = None,
) -> tuple[str, dict[str, str]]:
    """Build the synthesizer prompt and return (prompt, label_map).

    In blind mode the answers are shuffled and shown as "Response A/B/C" with no
    provider names, so the synthesizer can't favour a brand. The label_map
    (A -> claude, ...) lets the caller reveal attribution afterwards.
    """
    answers = [r for r in results if r.status == "ok"]
    sections: list[str] = []
    label_map: dict[str, str] = {}

    if blind:
        shuffled = list(answers)
        (rng or random).shuffle(shuffled)
        for offset, result in enumerate(shuffled):
            tag = chr(ord("A") + offset)
            sections.append(f"### Response {tag}\n\n{result.stdout.strip()}")
            label_map[tag] = result.provider
    else:
        for result in answers:
            sections.append(f"### {result.provider}\n\n{result.stdout.strip()}")
            label_map[result.provider] = result.provider

    prompt = (
        f"{SYNTHESIZER_PROMPT}\n\n"
        f"## User question\n\n{question}\n\n"
        f"## Responses to synthesize\n\n" + "\n\n".join(sections) + "\n\n## Your synthesized answer\n"
    )
    return prompt, label_map


# --------------------------------------------------------------------------- #
# Render: stdout carries content (Markdown or JSONL); stderr carries progress.
# --------------------------------------------------------------------------- #

_STATUS_LABELS = {"ok": "OK", "failed": "FAILED", "timeout": "TIMEOUT", "missing": "MISSING"}


def _status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status.upper())


def _body(result: RunResult) -> list[str]:
    if result.status == "ok":
        return [result.stdout.strip(), ""]
    detail = result.stderr or f"Process exited with return code {result.returncode}."
    return ["```text", detail[-1200:], "```", ""]


def render_block(result: RunResult) -> str:
    model = f" ({result.model})" if result.model else ""
    heading = f"## {result.provider}{model} - {_status_label(result.status)} - {result.elapsed:.1f}s"
    return "\n".join([heading, "", *_body(result)])


def render_synthesis_block(result: RunResult, synthesizer: str, blind: bool) -> str:
    mode = "blind" if blind else "named"
    heading = f"## synthesis · via {synthesizer} ({mode}) - {_status_label(result.status)} - {result.elapsed:.1f}s"
    return "\n".join([heading, "", *_body(result)])


def result_record(result: RunResult) -> dict:
    return {
        "type": "response",
        "provider": result.provider,
        "model": result.model,
        "status": result.status,
        "elapsed": round(result.elapsed, 3),
        "returncode": result.returncode,
        "text": result.stdout,
        "stderr": result.stderr,
    }


def synthesis_record(result: RunResult, synthesizer: str, blind: bool, label_map: dict[str, str]) -> dict:
    return {
        "type": "synthesis",
        "synthesizer": synthesizer,
        "blind": blind,
        "label_map": label_map,
        "status": result.status,
        "elapsed": round(result.elapsed, 3),
        "text": result.stdout,
        "stderr": result.stderr,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

app = typer.Typer(
    name="moa",
    help="Ask one question to multiple local AI coding CLIs in parallel and collect their answers.",
    no_args_is_help=True,
    add_completion=False,
)


def _read_prompt(prompt: str | None, file: Path | None) -> str:
    if file is not None:
        if str(file) == "-":
            return sys.stdin.read().strip()
        return file.read_text(encoding="utf-8").strip()
    if prompt == "-":
        return sys.stdin.read().strip()
    if prompt:
        return prompt.strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    raise typer.BadParameter("Provide a prompt, --file, or pipe prompt text on stdin.")


def _note(message: str) -> None:
    """Progress and selection notes go to stderr so stdout stays pure content."""
    typer.echo(message, err=True)


def _emit(text: str) -> None:
    sys.stdout.write(text.rstrip("\n") + "\n")
    sys.stdout.flush()


async def _collect(providers: list[Provider], prompt: str, timeout: float, json_output: bool) -> list[RunResult]:
    results: list[RunResult] = []
    async for result in stream(providers, prompt, timeout):
        results.append(result)
        _emit(json.dumps(result_record(result)) if json_output else render_block(result))
    return results


@app.command()
def ask(
    prompt: Annotated[str | None, typer.Argument(help="Prompt to send to each agent. Use '-' for stdin.")] = None,
    num: Annotated[int, typer.Option("--num", "-n", help="How many agents to ask, taken in priority order.")] = 3,
    provider: Annotated[
        list[str] | None,
        typer.Option("--provider", "-p", help="Pin specific agent(s). Repeatable. Overrides --num."),
    ] = None,
    file: Annotated[Path | None, typer.Option("--file", "-f", help="Read the prompt from a file or '-' for stdin.")] = None,
    timeout: Annotated[float, typer.Option("--timeout", "-t", help="Per-agent timeout in seconds.")] = 180,
    synth: Annotated[bool, typer.Option("--synth", help="Also synthesize the answers into one unified answer.")] = False,
    synthesizer: Annotated[
        str,
        typer.Option("--synthesizer", help="Who synthesizes: auto | random | a provider name."),
    ] = "auto",
    blind: Annotated[bool, typer.Option("--blind", help="Hide provider identities from the synthesizer.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSONL.")] = False,
) -> None:
    """Ask multiple agents in parallel; answers stream back as each one finishes."""
    prompt_text = _read_prompt(prompt, file)
    if not prompt_text:
        raise typer.BadParameter("Prompt cannot be empty.")
    if num < 1:
        raise typer.BadParameter("--num must be at least 1.")

    try:
        selected, skipped = select_for_run(num, tuple(provider) if provider else None)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if not selected:
        _note("No agents available. Run `moa doctor` to see which CLIs are installed.")
        raise typer.Exit(code=1)

    note = f"Asking {', '.join(p.name for p in selected)} (timeout {timeout:g}s)"
    if skipped:
        note += f"; skipped (not installed): {', '.join(skipped)}"
    _note(note)

    results = asyncio.run(_collect(selected, prompt_text, timeout, json_output))
    successes = [r for r in results if r.status == "ok"]

    if synth:
        _run_synthesis(prompt_text, results, successes, selected, synthesizer, blind, timeout, json_output)

    if not successes:
        raise typer.Exit(code=1)


def _run_synthesis(
    prompt_text: str,
    results: list[RunResult],
    successes: list[RunResult],
    selected: list[Provider],
    synthesizer: str,
    blind: bool,
    timeout: float,
    json_output: bool,
) -> None:
    if len(successes) < 2:
        _note("Synthesis skipped: need at least 2 successful responses.")
        return

    candidates = [p.name for p in selected]
    try:
        synth_name = choose_synthesizer(synthesizer, candidates)
    except ValueError as exc:
        _note(f"Synthesis skipped: {exc}")
        return

    synth_prompt, label_map = build_synthesis_prompt(prompt_text, results, blind)
    _note(f"Synthesizing with {synth_name} ({'blind' if blind else 'named'})...")
    synth_result = asyncio.run(run_provider(PROVIDERS[synth_name], synth_prompt, timeout))

    if json_output:
        _emit(json.dumps(synthesis_record(synth_result, synth_name, blind, label_map)))
    else:
        _emit(render_synthesis_block(synth_result, synth_name, blind))
        if blind and synth_result.status == "ok":
            mapping = ", ".join(f"{tag}={name}" for tag, name in label_map.items())
            _note(f"Blind labels: {mapping}")


@app.command()
def doctor() -> None:
    """Show which agent CLIs are installed."""
    available = available_provider_names()
    missing = missing_provider_names()

    def fmt(names: list[str]) -> str:
        return ", ".join(f"{name} ({PROVIDERS[name].executable})" for name in names) or "none"

    typer.echo("Available agents: " + fmt(available))
    typer.echo("Missing agents:   " + fmt(missing))


def main() -> None:
    app()

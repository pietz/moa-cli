"""Human-readable and JSON output formatting."""

from __future__ import annotations

import sys

import typer

from .execution import RunResult

_STATUS_LABELS = {
    "ok": "OK",
    "failed": "FAILED",
    "timeout": "TIMEOUT",
    "missing": "MISSING",
}
_RULE_WIDTH = 60


def status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status.upper())


def _rule(label: str) -> str:
    text = f" {label} "
    if len(text) >= _RULE_WIDTH:
        return text.strip()
    pad = _RULE_WIDTH - len(text)
    left = pad // 2
    return "─" * left + text + "─" * (pad - left)


def _body(result: RunResult) -> list[str]:
    if result.status == "ok":
        return [result.stdout.strip(), ""]
    detail = result.stderr or f"Process exited with return code {result.returncode}."
    return ["```text", detail[-1200:], "```", ""]


def _plain_output() -> bool:
    return not sys.stdout.isatty()


def _render(label: str, result: RunResult, plain: bool) -> str:
    if plain:
        return "\n".join(["", f"## {label}", *_body(result)])
    return "\n".join(["", _rule(label), *_body(result)])


def render_block(result: RunResult, plain: bool | None = None) -> str:
    if plain is None:
        plain = _plain_output()
    model = f" ({result.model})" if result.model else ""
    label = (
        f"{result.provider}{model} · {status_label(result.status)} · "
        f"{result.elapsed:.1f}s"
    )
    return _render(label, result, plain)


def render_synthesis_block(
    result: RunResult,
    synthesizer: str,
    plain: bool | None = None,
) -> str:
    if plain is None:
        plain = _plain_output()
    label = (
        f"synthesis · via {synthesizer} · "
        f"{status_label(result.status)} · {result.elapsed:.1f}s"
    )
    return _render(label, result, plain)


def render_debate_turn_block(
    result: RunResult,
    round_num: int,
    plain: bool | None = None,
) -> str:
    if plain is None:
        plain = _plain_output()
    model = f" ({result.model})" if result.model else ""
    label = (
        f"round {round_num} · {result.provider}{model} · "
        f"{status_label(result.status)} · {result.elapsed:.1f}s"
    )
    return _render(label, result, plain)


def render_verdict_block(
    result: RunResult,
    moderator: str,
    plain: bool | None = None,
) -> str:
    if plain is None:
        plain = _plain_output()
    label = (
        f"verdict · moderator {moderator} · "
        f"{status_label(result.status)} · {result.elapsed:.1f}s"
    )
    return _render(label, result, plain)


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


def synthesis_record(result: RunResult, synthesizer: str) -> dict:
    return {
        "type": "synthesis",
        "synthesizer": synthesizer,
        "status": result.status,
        "elapsed": round(result.elapsed, 3),
        "text": result.stdout,
        "stderr": result.stderr,
    }


def debate_turn_record(result: RunResult, round_num: int) -> dict:
    return {
        "type": "debate_turn",
        "round": round_num,
        "provider": result.provider,
        "model": result.model,
        "status": result.status,
        "elapsed": round(result.elapsed, 3),
        "returncode": result.returncode,
        "text": result.stdout,
        "stderr": result.stderr,
    }


def verdict_record(result: RunResult, moderator: str) -> dict:
    return {
        "type": "verdict",
        "moderator": moderator,
        "status": result.status,
        "elapsed": round(result.elapsed, 3),
        "text": result.stdout,
        "stderr": result.stderr,
    }


def note(message: str) -> None:
    typer.echo(message, err=True)


def emit(text: str) -> None:
    sys.stdout.write(text.rstrip("\n") + "\n")
    sys.stdout.flush()

"""Definitions and selection logic for supported agent CLIs."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass

CommandBuilder = Callable[
    [str, str, str | None, tuple[str, ...], tuple[str, ...]], list[str]
]


@dataclass(frozen=True)
class Provider:
    name: str
    executable: str
    default_model: str
    build: CommandBuilder
    readonly: tuple[str, ...] | None = ()
    yolo: tuple[str, ...] = ()
    readonly_note: str | None = None
    unset_env: tuple[str, ...] = ()
    uses_output_file: bool = False
    effort_flag: Callable[[str], tuple[str, ...]] | None = None

    def effort_args(self, value: str | None) -> tuple[str, ...]:
        if not value or self.effort_flag is None:
            return ()
        return self.effort_flag(value)

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.setdefault("NO_COLOR", "1")
        env.setdefault("TERM", "dumb")
        for key in self.unset_env:
            env.pop(key, None)
        return env

    def perm_args(self, yolo: bool) -> tuple[str, ...]:
        return self.yolo if yolo else self.readonly or ()


def _claude(
    prompt: str,
    model: str,
    _out: str | None,
    perm: tuple[str, ...],
    _effort: tuple[str, ...],
) -> list[str]:
    return ["claude", "--model", model, *perm, "-p", prompt]


def _codex(
    prompt: str,
    model: str,
    out: str | None,
    perm: tuple[str, ...],
    effort: tuple[str, ...],
) -> list[str]:
    command = [
        "codex",
        "exec",
        "-m",
        model,
        "--skip-git-repo-check",
        "--color",
        "never",
        *perm,
        *effort,
    ]
    if out:
        command += ["-o", out]
    command.append(prompt)
    return command


def _agy(
    prompt: str,
    model: str,
    _out: str | None,
    perm: tuple[str, ...],
    _effort: tuple[str, ...],
) -> list[str]:
    return ["agy", *perm, "--model", model, "-p", prompt]


def _opencode(
    prompt: str,
    model: str,
    _out: str | None,
    perm: tuple[str, ...],
    effort: tuple[str, ...],
) -> list[str]:
    command = ["opencode", "run", *perm, *effort]
    if model:
        command += ["-m", model]
    command.append(prompt)
    return command


PROVIDERS: dict[str, Provider] = {
    "claude": Provider(
        "claude",
        "claude",
        "opus",
        _claude,
        readonly=("--permission-mode", "default"),
        yolo=("--permission-mode", "bypassPermissions"),
        unset_env=("CLAUDECODE",),
    ),
    "codex": Provider(
        "codex",
        "codex",
        "gpt-5.5",
        _codex,
        readonly=("-s", "read-only"),
        yolo=("-s", "danger-full-access"),
        uses_output_file=True,
        effort_flag=lambda value: ("-c", f"model_reasoning_effort={value}"),
    ),
    "agy": Provider(
        "agy",
        "agy",
        "Gemini 3.5 Flash (High)",
        _agy,
        readonly=("--sandbox",),
        readonly_note=(
            "agy is shell-sandboxed but can still edit files (no true read-only mode)"
        ),
        yolo=(),
    ),
    "opencode": Provider(
        "opencode",
        "opencode",
        "",
        _opencode,
        readonly=("--agent", "plan"),
        yolo=("--dangerously-skip-permissions",),
        effort_flag=lambda value: ("--variant", value),
    ),
}

PRIORITY: tuple[str, ...] = ("claude", "codex", "agy", "opencode")


def _installed(name: str) -> bool:
    return shutil.which(PROVIDERS[name].executable) is not None


def available_provider_names() -> list[str]:
    return [name for name in PRIORITY if _installed(name)]


def missing_provider_names() -> list[str]:
    return [name for name in PRIORITY if not _installed(name)]


def select_for_run(
    num: int, names: tuple[str, ...] | None, exclude: tuple[str, ...] = ()
) -> tuple[list[Provider], list[str]]:
    """Return selected providers and explicitly requested missing providers."""
    unknown = [name for name in (*(names or ()), *exclude) if name not in PROVIDERS]
    if unknown:
        raise ValueError(f"Unknown provider(s): {', '.join(unknown)}")

    excluded = set(exclude)
    if names:
        kept = [name for name in names if name not in excluded]
        chosen = [PROVIDERS[name] for name in kept if _installed(name)]
        skipped = [name for name in kept if not _installed(name)]
        return chosen, skipped

    available = [name for name in available_provider_names() if name not in excluded]
    return [PROVIDERS[name] for name in available[:num]], []

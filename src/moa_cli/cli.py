"""Command-line interface and workflow orchestration for MOA."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

# Public compatibility imports. Existing users may import these from
# moa_cli.cli; the implementation now lives in responsibility-focused modules.
from .config import (
    CONFIG_DEFAULTS as _CONFIG_DEFAULTS,
    CONFIG_KEYS as _CONFIG_KEYS,
    CONFIG_SCALARS as _CONFIG_SCALARS,
    config_dir,
    config_path,
    load_config,
    read_config_or_empty as _read_config_or_empty,
    resolve_option,
    serialize_config,
    validate_providers as _validate_providers,
    validate_scalar as _validate_scalar,
    write_config,
)
from .execution import Status, RunResult, run_provider, stream
from .status import StatusLine
from .output import (
    debate_turn_record,
    emit as _emit,
    note as _note,
    render_block,
    render_debate_turn_block,
    render_synthesis_block,
    render_verdict_block,
    result_record,
    status_label as _status_label,
    synthesis_record,
    verdict_record,
)
from .providers import (
    CommandBuilder,
    PRIORITY,
    PROVIDERS,
    Provider,
    available_provider_names,
    missing_provider_names,
    select_for_run,
)
from .workflows import (
    ADVERSARIAL_INSTRUCTION,
    CONVERGENCE_DONE,
    MODERATOR_CONVERGENCE_PROMPT,
    MODERATOR_VERDICT_PROMPT,
    ROUNDS_MAX,
    SYNTHESIZER_PROMPT,
    assign_debate_roles,
    build_convergence_prompt,
    build_debate_turn_prompt,
    build_synthesis_prompt,
    build_verdict_prompt,
    choose_synthesizer,
    clamp_rounds,
)

__all__ = [
    "ADVERSARIAL_INSTRUCTION",
    "CommandBuilder",
    "MODERATOR_CONVERGENCE_PROMPT",
    "MODERATOR_VERDICT_PROMPT",
    "PRIORITY",
    "PROVIDERS",
    "Provider",
    "RunResult",
    "SYNTHESIZER_PROMPT",
    "Status",
    "app",
    "assign_debate_roles",
    "available_provider_names",
    "build_convergence_prompt",
    "build_debate_turn_prompt",
    "build_synthesis_prompt",
    "build_verdict_prompt",
    "choose_synthesizer",
    "clamp_rounds",
    "config_dir",
    "config_path",
    "load_config",
    "main",
    "missing_provider_names",
    "parse_model_overrides",
    "render_block",
    "render_synthesis_block",
    "result_record",
    "run_provider",
    "select_for_run",
    "serialize_config",
]

app = typer.Typer(
    name="moa",
    help=(
        "Ask one question to multiple local AI coding CLIs in parallel "
        "and collect their answers."
    ),
    no_args_is_help=True,
    add_completion=False,
)

config_app = typer.Typer(
    name="config",
    help=(
        "Inspect and edit persisted defaults at ~/.moa/config.toml "
        "(override the dir with $MOA_CONFIG_DIR)."
    ),
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(config_app)


def parse_model_overrides(
    entries: list[str] | None,
) -> dict[str, str]:
    models: dict[str, str] = {}
    for entry in entries or []:
        if "=" not in entry:
            raise typer.BadParameter(f"--model expects PROVIDER=MODEL, got: {entry!r}")
        provider, model = entry.split("=", 1)
        provider = provider.strip()
        if provider not in PROVIDERS:
            raise typer.BadParameter(
                f"Unknown provider in --model: {provider!r}. "
                f"Known: {', '.join(PROVIDERS)}."
            )
        models[provider] = model
    return models


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


async def _collect(
    providers: list[Provider],
    prompt: str,
    timeout: float,
    json_output: bool,
    models: dict[str, str] | None = None,
    yolo: bool = False,
    emit_blocks: bool = True,
    efforts: dict[str, str] | None = None,
) -> list[RunResult]:
    resolved_models = models or {}
    status = StatusLine()
    for provider in providers:
        model = resolved_models.get(provider.name) or provider.default_model
        status.add(provider.name, _call_label("", provider.name, model))
    status.start()
    results: list[RunResult] = []
    try:
        async for result in stream(
            providers, prompt, timeout, resolved_models, yolo, efforts
        ):
            results.append(result)
            status.clear()
            if emit_blocks:
                output = (
                    json.dumps(result_record(result))
                    if json_output
                    else render_block(result)
                )
                _emit(output)
            elif not status.active:
                # Off a TTY there is no spinner, so log each arrival on stderr.
                _note(
                    f"  {result.provider} responded "
                    f"({_status_label(result.status)}, {result.elapsed:.1f}s)"
                )
            status.remove(result.provider)
    finally:
        await status.stop()
    return results


def _call_label(what: str, name: str, model: str) -> str:
    base = f"{name} ({model})" if model else name
    return f"{what} · {base}" if what else base


def _progress_note(message: str) -> None:
    """A 'work is starting' note. On a TTY the live status line replaces it."""
    if not sys.stderr.isatty():
        _note(message)


async def _run_with_status(
    provider: Provider,
    prompt: str,
    timeout: float,
    model: str | None,
    yolo: bool,
    effort: str | None,
    label: str,
) -> RunResult:
    status = StatusLine()
    status.add(provider.name, label)
    status.start()
    try:
        return await run_provider(provider, prompt, timeout, model, yolo, effort)
    finally:
        await status.stop()


PromptArg = Annotated[
    str | None,
    typer.Argument(help="Prompt to send to each agent. Use '-' for stdin."),
]
NumOpt = Annotated[
    int | None,
    typer.Option(
        "--num",
        "-n",
        help="How many agents to ask, taken in priority order.",
    ),
]
ProviderOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--provider",
        "-p",
        help="Pin specific agent(s). Repeatable. Overrides --num.",
    ),
]
ExcludeOpt = Annotated[
    list[str] | None,
    typer.Option("--exclude", "-x", help="Drop agent(s) from the run. Repeatable."),
]
ModelOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--model",
        "-m",
        help="Override a tool's model: PROVIDER=MODEL. Repeatable.",
    ),
]
FileOpt = Annotated[
    Path | None,
    typer.Option("--file", "-f", help="Read the prompt from a file or '-' for stdin."),
]
TimeoutOpt = Annotated[
    float | None,
    typer.Option("--timeout", "-t", help="Per-agent timeout in seconds."),
]
JsonOpt = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSONL.")]
YoloOpt = Annotated[
    bool,
    typer.Option(
        "--yolo",
        help="Grant agents full write access (default is read-only).",
    ),
]


@dataclass(frozen=True)
class RunConfig:
    prompt: str
    selected: list[Provider]
    models: dict[str, str]
    timeout: float
    json_output: bool
    yolo: bool
    efforts: dict[str, str]


def resolve_run(
    prompt: str | None,
    file: Path | None,
    num: int | None,
    provider: list[str] | None,
    exclude: list[str] | None,
    model: list[str] | None,
    timeout: float | None,
    json_output: bool,
    yolo: bool,
    default_num: int = 3,
) -> RunConfig:
    prompt_text = _read_prompt(prompt, file)
    if not prompt_text:
        raise typer.BadParameter("Prompt cannot be empty.")

    try:
        config = load_config()
    except ValueError as exc:
        raise typer.BadParameter(f"{config_path()}: {exc}") from exc

    num = resolve_option(num, "num", config, default_num)
    timeout = resolve_option(timeout, "timeout", config, 900.0)
    exclude_names = tuple(exclude) if exclude else tuple(config.get("exclude", ()))
    models = {
        **config.get("models", {}),
        **parse_model_overrides(model),
    }
    efforts = dict(config.get("efforts", {}))

    if num < 1:
        raise typer.BadParameter("--num must be at least 1.")

    try:
        selected, skipped = select_for_run(
            num,
            tuple(provider) if provider else None,
            exclude_names,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if not selected:
        _note("No agents available. Run `moa doctor` to see which CLIs are installed.")
        raise typer.Exit(code=1)

    mode = "yolo (full write access)" if yolo else "read-only"
    selection_note = (
        f"Asking {', '.join(item.name for item in selected)} "
        f"(timeout {timeout:g}s, {mode})"
    )
    if skipped:
        selection_note += f"; skipped (not installed): {', '.join(skipped)}"
    if exclude_names:
        selection_note += f"; excluded: {', '.join(exclude_names)}"
    if not yolo:
        for item in selected:
            if item.readonly_note:
                selection_note += f"; note: {item.readonly_note}"
    _note(selection_note)

    return RunConfig(
        prompt_text,
        selected,
        models,
        timeout,
        json_output,
        yolo,
        efforts,
    )


@app.command()
def ask(
    prompt: PromptArg = None,
    num: NumOpt = None,
    provider: ProviderOpt = None,
    exclude: ExcludeOpt = None,
    model: ModelOpt = None,
    file: FileOpt = None,
    timeout: TimeoutOpt = None,
    json_output: JsonOpt = False,
    yolo: YoloOpt = False,
) -> None:
    """Ask agents in parallel and stream each answer as it finishes."""
    cfg = resolve_run(
        prompt,
        file,
        num,
        provider,
        exclude,
        model,
        timeout,
        json_output,
        yolo,
    )
    results = asyncio.run(
        _collect(
            cfg.selected,
            cfg.prompt,
            cfg.timeout,
            cfg.json_output,
            cfg.models,
            cfg.yolo,
            efforts=cfg.efforts,
        )
    )
    if not any(result.status == "ok" for result in results):
        raise typer.Exit(code=1)


@app.command()
def distill(
    prompt: PromptArg = None,
    num: NumOpt = None,
    provider: ProviderOpt = None,
    exclude: ExcludeOpt = None,
    model: ModelOpt = None,
    file: FileOpt = None,
    timeout: TimeoutOpt = None,
    synthesizer: Annotated[
        str | None,
        typer.Option(
            "--synthesizer",
            "-s",
            help="Who distills: auto | random | a provider name.",
        ),
    ] = None,
    json_output: JsonOpt = False,
    yolo: YoloOpt = False,
) -> None:
    """Run the council, then merge its answers into one."""
    cfg = resolve_run(
        prompt,
        file,
        num,
        provider,
        exclude,
        model,
        timeout,
        json_output,
        yolo,
    )
    synthesizer = resolve_option(
        synthesizer,
        "synthesizer",
        _read_config_or_empty(),
        "auto",
    )
    results = asyncio.run(
        _collect(
            cfg.selected,
            cfg.prompt,
            cfg.timeout,
            cfg.json_output,
            cfg.models,
            cfg.yolo,
            emit_blocks=False,
            efforts=cfg.efforts,
        )
    )
    successes = [result for result in results if result.status == "ok"]
    synthesis = _run_synthesis(cfg, results, successes, synthesizer)
    if synthesis is None or synthesis.status != "ok":
        raise typer.Exit(code=1)


def _run_synthesis(
    cfg: RunConfig,
    results: list[RunResult],
    successes: list[RunResult],
    synthesizer: str,
) -> RunResult | None:
    if len(successes) < 2:
        _note("Distill skipped: need at least 2 successful responses.")
        return None

    try:
        synth_name = choose_synthesizer(
            synthesizer, [provider.name for provider in cfg.selected]
        )
    except ValueError as exc:
        _note(f"Distill skipped: {exc}")
        return None

    synth_prompt, _label_map = build_synthesis_prompt(cfg.prompt, results, blind=True)
    synth_model = cfg.models.get(synth_name) or PROVIDERS[synth_name].default_model
    _progress_note(f"Distilling with {synth_name}...")
    synth_result = asyncio.run(
        _run_with_status(
            PROVIDERS[synth_name],
            synth_prompt,
            cfg.timeout,
            cfg.models.get(synth_name),
            cfg.yolo,
            cfg.efforts.get(synth_name),
            _call_label("synthesis", synth_name, synth_model),
        )
    )
    output = (
        json.dumps(synthesis_record(synth_result, synth_name))
        if cfg.json_output
        else render_synthesis_block(synth_result, synth_name)
    )
    _emit(output)
    return synth_result


RoundsOpt = Annotated[
    int,
    typer.Option(
        "--rounds",
        "-r",
        help=f"Debate rounds (default 2, hard max {ROUNDS_MAX}).",
    ),
]
ModeratorOpt = Annotated[
    str | None,
    typer.Option(
        "--moderator",
        "-j",
        help=(
            "Moderator that checks convergence and writes the verdict: "
            "auto | a provider."
        ),
    ),
]


@app.command()
def debate(
    prompt: PromptArg = None,
    num: NumOpt = None,
    provider: ProviderOpt = None,
    exclude: ExcludeOpt = None,
    model: ModelOpt = None,
    file: FileOpt = None,
    timeout: TimeoutOpt = None,
    rounds: RoundsOpt = 2,
    moderator: ModeratorOpt = None,
    json_output: JsonOpt = False,
    yolo: YoloOpt = False,
) -> None:
    """Run a sequential debate and moderator verdict."""
    cfg = resolve_run(
        prompt,
        file,
        num,
        provider,
        exclude,
        model,
        timeout,
        json_output,
        yolo,
        default_num=2,
    )
    moderator = resolve_option(
        moderator,
        "moderator",
        _read_config_or_empty(),
        "auto",
    )
    rounds, warning = clamp_rounds(rounds)
    if warning:
        _note(warning)

    try:
        debaters, moderator_provider = assign_debate_roles(cfg.selected, moderator)
    except ValueError as exc:
        _note(f"debate: {exc}")
        raise typer.Exit(code=1) from exc

    _note(
        f"Debating: {', '.join(item.name for item in debaters)} over "
        f"up to {rounds} round(s), moderator "
        f"{moderator_provider.name}. Debate is the costliest mode and "
        "can converge on a wrong answer."
    )
    _transcript, verdict = asyncio.run(
        _run_debate(cfg, debaters, moderator_provider, rounds)
    )
    if verdict is None or verdict.status != "ok":
        raise typer.Exit(code=1)


async def _moderator_signals_done(
    cfg: RunConfig,
    moderator: Provider,
    latest_ok: list[RunResult],
    round_num: int,
) -> bool:
    prompt = build_convergence_prompt(cfg.prompt, latest_ok)
    mod_model = cfg.models.get(moderator.name) or moderator.default_model
    _progress_note(
        f"Round {round_num}: moderator {moderator.name} checking for convergence..."
    )
    result = await _run_with_status(
        moderator,
        prompt,
        cfg.timeout,
        cfg.models.get(moderator.name),
        cfg.yolo,
        cfg.efforts.get(moderator.name),
        _call_label("convergence", moderator.name, mod_model),
    )
    done = result.status == "ok" and result.stdout.strip().upper().startswith(
        CONVERGENCE_DONE
    )
    if done:
        _note(
            f"Moderator {moderator.name}: converged; stopping after round {round_num}."
        )
    return done


async def _run_debate(
    cfg: RunConfig,
    debaters: list[Provider],
    moderator: Provider,
    rounds: int,
) -> tuple[list[RunResult], RunResult | None]:
    transcript: list[RunResult] = []
    latest: dict[str, RunResult] = {}

    for round_num in range(1, rounds + 1):
        for debater in debaters:
            prior = [
                (
                    "the other participant",
                    latest[other.name].stdout,
                )
                for other in debaters
                if other.name != debater.name and other.name in latest
            ]
            turn_prompt = build_debate_turn_prompt(cfg.prompt, prior)
            debater_model = cfg.models.get(debater.name) or debater.default_model
            _progress_note(f"Round {round_num}: {debater.name} responding...")
            result = await _run_with_status(
                debater,
                turn_prompt,
                cfg.timeout,
                cfg.models.get(debater.name),
                cfg.yolo,
                cfg.efforts.get(debater.name),
                _call_label(f"round {round_num}", debater.name, debater_model),
            )
            transcript.append(result)
            latest[debater.name] = result
            output = (
                json.dumps(debate_turn_record(result, round_num))
                if cfg.json_output
                else render_debate_turn_block(result, round_num)
            )
            _emit(output)

        if round_num < rounds:
            latest_ok = [
                latest[debater.name]
                for debater in debaters
                if debater.name in latest and latest[debater.name].status == "ok"
            ]
            if len(latest_ok) >= 2 and await _moderator_signals_done(
                cfg, moderator, latest_ok, round_num
            ):
                break

    if not any(result.status == "ok" for result in transcript):
        _note("Debate produced no usable answers; skipping the moderator verdict.")
        return transcript, None

    verdict_prompt, _label_map = build_verdict_prompt(cfg.prompt, transcript)
    mod_model = cfg.models.get(moderator.name) or moderator.default_model
    _progress_note(f"Moderator {moderator.name} writing the final answer...")
    verdict = await _run_with_status(
        moderator,
        verdict_prompt,
        cfg.timeout,
        cfg.models.get(moderator.name),
        cfg.yolo,
        cfg.efforts.get(moderator.name),
        _call_label("verdict", moderator.name, mod_model),
    )
    transcript.append(verdict)
    output = (
        json.dumps(verdict_record(verdict, moderator.name))
        if cfg.json_output
        else render_verdict_block(verdict, moderator.name)
    )
    _emit(output)
    return transcript, verdict


@app.command()
def doctor() -> None:
    """Show installed agent CLIs and their default models."""

    def fmt(names: list[str]) -> str:
        parts: list[str] = []
        for name in names:
            provider = PROVIDERS[name]
            model = provider.default_model or "configured default"
            label = f"{name} ({model})"
            if provider.readonly_note:
                label += " [partial sandbox - shell only; can still edit files]"
            elif provider.readonly is None:
                label += " [no read-only mode (runs unsandboxed)]"
            parts.append(label)
        return ", ".join(parts) or "none"

    typer.echo("Available agents: " + fmt(available_provider_names()))
    typer.echo("Missing agents:   " + fmt(missing_provider_names()))


def _load_config_or_exit() -> dict:
    try:
        return load_config()
    except ValueError as exc:
        raise typer.BadParameter(f"{config_path()}: {exc}") from exc


@config_app.command("path")
def config_path_cmd() -> None:
    """Print the config file path."""
    typer.echo(str(config_path()))


@config_app.command("show")
def config_show() -> None:
    """Print effective configuration and its file path."""
    effective = {**_CONFIG_DEFAULTS, **_load_config_or_exit()}
    typer.echo(f"# {config_path()}")
    typer.echo(serialize_config(effective).rstrip("\n"))


@config_app.command("set")
def config_set(
    key: Annotated[
        str,
        typer.Argument(
            help=(
                "Config key: num | timeout | synthesizer | moderator | "
                "exclude | model | effort."
            )
        ),
    ],
    value: Annotated[
        str,
        typer.Argument(
            help=(
                "Value. For model/effort: PROVIDER=VALUE. "
                "For exclude: comma-separated names."
            )
        ),
    ],
) -> None:
    """Write a value to the config file."""
    config = _load_config_or_exit()

    if key == "model":
        if "=" not in value:
            raise typer.BadParameter(
                "model expects PROVIDER=MODEL, e.g. "
                "`moa config set model claude=sonnet`."
            )
        provider, model = value.split("=", 1)
        provider = provider.strip()
        if provider not in PROVIDERS:
            raise typer.BadParameter(
                f"Unknown provider: {provider!r}. Known: {', '.join(PROVIDERS)}."
            )
        config.setdefault("models", {})[provider] = model
    elif key == "effort":
        if "=" not in value:
            raise typer.BadParameter(
                "effort expects PROVIDER=VALUE, e.g. "
                "`moa config set effort codex=high`."
            )
        provider, effort = value.split("=", 1)
        provider = provider.strip()
        if provider not in PROVIDERS:
            raise typer.BadParameter(
                f"Unknown provider: {provider!r}. Known: {', '.join(PROVIDERS)}."
            )
        if not effort:
            raise typer.BadParameter("effort value cannot be empty.")
        config.setdefault("efforts", {})[provider] = effort
        if PROVIDERS[provider].effort_flag is None:
            _note(
                f"Note: {provider} has no effort flag; this value "
                "will be ignored at runtime."
            )
    elif key == "exclude":
        names = [name.strip() for name in value.split(",") if name.strip()]
        try:
            _validate_providers(names, "exclude")
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        config["exclude"] = names
    elif key in _CONFIG_SCALARS:
        kind = _CONFIG_SCALARS[key]
        try:
            coerced = kind(value)
        except ValueError as exc:
            raise typer.BadParameter(
                f"{key} must be {kind.__name__}, got {value!r}."
            ) from exc
        try:
            _validate_scalar(key, coerced)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        config[key] = coerced
    else:
        known = "num, timeout, synthesizer, moderator, exclude, model, effort"
        raise typer.BadParameter(f"Unknown config key: {key!r}. Known: {known}.")

    write_config(config)
    typer.echo(f"Set {key} in {config_path()}")


@config_app.command("unset")
def config_unset(
    key: Annotated[
        str,
        typer.Argument(
            help=(
                "Config key to remove. Use `model PROVIDER` / "
                "`effort PROVIDER` to drop one."
            )
        ),
    ],
    provider: Annotated[
        str | None,
        typer.Argument(help=("Provider name, only when key is 'model' or 'effort'.")),
    ] = None,
) -> None:
    """Remove a config key or one provider's model/effort."""
    config = _load_config_or_exit()

    if key in ("model", "effort"):
        table_key = "models" if key == "model" else "efforts"
        if not provider:
            raise typer.BadParameter(
                f"unset {key} expects a provider, e.g. `moa config unset {key} codex`."
            )
        table = config.get(table_key, {})
        if provider in table:
            del table[provider]
            if not table:
                config.pop(table_key, None)
            write_config(config)
            typer.echo(f"Unset {key} {provider} in {config_path()}")
        else:
            typer.echo(f"{key} {provider} was not set.")
        return

    if key not in _CONFIG_KEYS:
        raise typer.BadParameter(
            f"Unknown config key: {key!r}. Known: {', '.join(_CONFIG_KEYS)}."
        )
    if key in config:
        del config[key]
        write_config(config)
        typer.echo(f"Unset {key} in {config_path()}")
    else:
        typer.echo(f"{key} was not set.")


def main() -> None:
    app()

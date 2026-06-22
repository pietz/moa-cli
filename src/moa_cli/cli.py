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

# A command builder turns (prompt, model, output_file, perm) into an argv list.
# output_file is a path the CLI may be told to write its final answer to; it is
# None for providers that answer cleanly on stdout. Only codex uses it. `perm`
# is the permission argv (read-only or yolo flags) spliced in before the prompt.
CommandBuilder = Callable[[str, str, str | None, tuple[str, ...]], list[str]]


@dataclass(frozen=True)
class Provider:
    name: str
    executable: str
    default_model: str
    build: CommandBuilder
    # Permission flags, declared as data rather than branched per tool. `readonly`
    # is spliced in for the safe default (no write access); `None` means the tool
    # has NO read-only mode, so by default it runs UNSCOPED (no permission args)
    # and moa notes on stderr that it isn't sandboxed. `yolo` is spliced in under
    # --yolo to grant full write access.
    readonly: tuple[str, ...] | None = ()
    yolo: tuple[str, ...] = ()
    # When the default ("readonly") flags give only PARTIAL protection - they
    # restrict something but do not fully prevent file writes - this holds an
    # honest one-line note moa surfaces on stderr. `None` means the default mode
    # is true read-only (or the tool has no read-only mode at all).
    readonly_note: str | None = None
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

    def perm_args(self, yolo: bool) -> tuple[str, ...]:
        """The permission argv for this run: yolo flags under --yolo, else readonly."""
        if yolo:
            return self.yolo
        # readonly is None for tools with no scoping flag at all: they run
        # unscoped, with no permission args spliced in.
        return self.readonly or ()


def _claude(prompt: str, model: str, _out: str | None, perm: tuple[str, ...]) -> list[str]:
    return ["claude", "--model", model, *perm, "-p", prompt]


def _codex(prompt: str, model: str, out: str | None, perm: tuple[str, ...]) -> list[str]:
    cmd = ["codex", "exec", "-m", model, "--skip-git-repo-check", "--color", "never", *perm]
    if out:
        cmd += ["-o", out]
    cmd.append(prompt)
    return cmd


def _agy(prompt: str, model: str, _out: str | None, perm: tuple[str, ...]) -> list[str]:
    # agy also hosts Claude/GPT-OSS models, so we pin a Gemini model explicitly
    # to keep the panel diverse. Without --model it defaults to Gemini Flash.
    # perm (e.g. --sandbox) goes first so the default reads `agy --sandbox
    # --model ... -p ...`.
    return ["agy", *perm, "--model", model, "-p", prompt]


def _opencode(prompt: str, model: str, _out: str | None, perm: tuple[str, ...]) -> list[str]:
    # opencode has no universal default model (it depends on which provider the
    # user has authed), so we omit -m when no model is given and let opencode
    # pick its own default. The prompt is a positional arg.
    cmd = ["opencode", "run", *perm]
    if model:
        cmd += ["-m", model]
    cmd.append(prompt)
    return cmd


PROVIDERS: dict[str, Provider] = {
    "claude": Provider(
        "claude", "claude", "opus", _claude,
        readonly=("--permission-mode", "plan"),
        yolo=("--permission-mode", "bypassPermissions"),
        unset_env=("CLAUDECODE",),
    ),
    "codex": Provider(
        "codex", "codex", "gpt-5.5", _codex,
        readonly=("-s", "read-only"),
        yolo=("-s", "danger-full-access"),
        uses_output_file=True,
    ),
    "agy": Provider(
        "agy", "agy", "Gemini 3.1 Pro (High)", _agy,
        # --sandbox restricts agy's terminal/shell but does NOT stop its
        # write_file tool, so this is PARTIAL protection (shell vector only),
        # not true read-only: agy can still edit files. readonly_note makes that
        # honest on stderr. Under --yolo agy drops --sandbox (full access).
        readonly=("--sandbox",),
        readonly_note="agy is shell-sandboxed but can still edit files (no true read-only mode)",
        yolo=(),
    ),
    "opencode": Provider(
        "opencode", "opencode", "", _opencode,
        readonly=("--agent", "plan"),
        yolo=(),  # default = build agent (full access)
    ),
}

# Auto-selection order, roughly by popularity. -n/--num walks this list and
# takes the first N that are actually installed.
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
    """Pick providers to run.

    Returns (to_run, skipped_not_installed).

    With an explicit `names` list we honour it in order, skipping any not
    installed. Otherwise we take the first `num` installed providers from
    PRIORITY. Excluded providers are dropped before either path takes effect, so
    `-n` counts only non-excluded installs and `-p` pins drop excluded names too.

    All installed providers are eligible, including ones whose default mode is
    only partial protection (e.g. agy's --sandbox, which still allows file
    writes); the caller surfaces an honest note on stderr rather than dropping
    or erroring on them.
    """
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


async def run_provider(
    provider: Provider, prompt: str, timeout: float, model: str | None = None, yolo: bool = False
) -> RunResult:
    model = model or provider.default_model
    out_file: str | None = None
    if provider.uses_output_file:
        handle, out_file = tempfile.mkstemp(prefix="moa-", suffix=".txt")
        os.close(handle)

    start = time.monotonic()
    try:
        try:
            process = await asyncio.create_subprocess_exec(
                *provider.build(prompt, model, out_file, provider.perm_args(yolo)),
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


async def stream(
    providers: list[Provider],
    prompt: str,
    timeout: float,
    models: dict[str, str] | None = None,
    yolo: bool = False,
) -> AsyncIterator[RunResult]:
    """Run every provider in parallel, yielding each result as it finishes."""
    models = models or {}
    tasks = [
        asyncio.create_task(run_provider(p, prompt, timeout, models.get(p.name), yolo))
        for p in providers
    ]
    for completed in asyncio.as_completed(tasks):
        yield await completed


# --------------------------------------------------------------------------- #
# Synthesis: merge the collected answers into one unified answer.
# --------------------------------------------------------------------------- #

# Aligned to the Mixture-of-Agents "Aggregate-and-Synthesize" prompt (Wang et al.
# 2024, togethercomputer/MoA), adapted "open-source models" -> "AI coding
# assistants". The two load-bearing clauses are kept nearly verbatim: critically
# evaluate (some may be biased or incorrect) and do not simply replicate (offer a
# refined, accurate, comprehensive reply).
SYNTHESIZER_PROMPT = """You have been provided with a set of responses from various AI coding \
assistants to the latest user question. Your task is to synthesize these responses into a single, \
high-quality response. It is crucial to critically evaluate the information provided in these \
responses, recognizing that some of it may be biased or incorrect. Your response should not simply \
replicate the given answers but should offer a refined, accurate, comprehensive, and well-structured \
reply to the question. Ensure your response is the best possible answer, addressing all aspects of \
the question.

Guidelines:
- Identify where the responses agree, where they complement each other, and where they conflict.
- Resolve conflicts by the quality of reasoning and evidence; use agreement as a tiebreaker.
- Keep what is correct and valuable; drop what is wrong, redundant, or unsupported.
- Do not refer to "Response A", the responses, or the fact that you are synthesizing. Just give the \
best possible answer.
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
# Debate: sequential adversarial rounds, then one neutral judge writes the
# verdict from the (anonymized + shuffled) full transcript. The literature is
# clear that debate is the costliest and least reliably-beneficial mode: it can
# converge on a wrong answer (conformity), so the judge is a separate model and
# its prompt weighs correctness/evidence over confidence and fluency.
# --------------------------------------------------------------------------- #

ROUNDS_MAX = 4

# Spliced into every debater turn after round 1: the prior answer(s) come first,
# then this instruction tells the debater to attack before answering. Counters
# sycophancy / Degeneration-of-Thought (the model agreeing to reach consensus).
ADVERSARIAL_INSTRUCTION = """Before giving your own answer, critically examine the \
other participant's answer above: identify any errors, weaknesses, unsupported claims, or \
gaps in reasoning. Do NOT agree merely to reach consensus - only concede a point if it is \
genuinely correct. Then give your own best, complete answer to the original question, \
incorporating any valid corrections.

If, after this scrutiny, you have no substantive change to your previous answer and you agree \
with the other participant, say so explicitly by starting your reply with the line \
"NO SUBSTANTIVE CHANGE" - this lets the debate stop early."""

# Phrase a debater emits when it has nothing substantive to add. When all active
# debaters in a round signal this, the debate stops before the round cap.
CONVERGENCE_MARKER = "NO SUBSTANTIVE CHANGE"

# The neutral judge reads the full transcript (anonymized + shuffled) and writes
# the final answer. It must weigh correctness/evidence over confidence/fluency -
# this is where conformity-to-a-wrong-answer is most dangerous, so the judge
# never just echoes the most fluent or most confident debater.
JUDGE_PROMPT = """You are a neutral judge. Below is a transcript of a debate between AI coding \
assistants who answered the user's question and then critiqued each other's answers across \
several rounds. The participants are anonymized and presented in arbitrary order.

Your task is to read the full debate and write the single best, final answer to the user's \
question. Weigh correctness and the strength of evidence and reasoning ABOVE confidence, \
fluency, and assertiveness - a wrong answer stated confidently must not win. Where the \
participants disagree, decide on the merits; where they agree, verify the agreement is actually \
sound rather than shared error.

Guidelines:
- Do not pick a "winner" by name or refer to participants, rounds, or the debate. Just give the \
best possible answer.
- Keep what is correct and well-supported; discard what is wrong, unsupported, or merely \
asserted.
- Do not invent information that the debate does not support."""


def assign_debate_roles(
    selected: list[Provider], judge: str | None
) -> tuple[list[Provider], Provider]:
    """Split the selected providers into (debaters, judge).

    Default: the top 2 selected providers debate and the next one judges (so the
    default n=3 maps to 2 debaters + 1 judge). `judge` (from -j/--judge) pins the
    judge to a named provider, which must be one of the selected providers and
    must NOT also be a debater. Requires at least 2 debaters and 1 distinct judge;
    raises ValueError otherwise (the caller turns this into a clean exit - debate
    never silently degrades to fewer participants).
    """
    if judge is not None:
        names = [p.name for p in selected]
        if judge not in PROVIDERS:
            raise ValueError(f"Unknown judge: {judge}")
        if judge not in names:
            raise ValueError(
                f"Judge {judge!r} is not among the selected providers ({', '.join(names)}). "
                f"Pin it with -p {judge} or widen the selection."
            )
        judge_provider = next(p for p in selected if p.name == judge)
        debaters = [p for p in selected if p.name != judge]
        if len(debaters) < 2:
            raise ValueError(
                f"debate needs at least 2 debaters plus the judge ({judge}); only "
                f"{len(debaters)} non-judge provider(s) available. Increase -n or -p."
            )
        return debaters, judge_provider

    if len(selected) < 3:
        raise ValueError(
            f"debate needs at least 3 providers (2 debaters + 1 neutral judge); "
            f"only {len(selected)} available. Increase -n, pin more with -p, or install more agents."
        )
    return selected[:2], selected[2]


def clamp_rounds(rounds: int) -> tuple[int, str | None]:
    """Clamp rounds to [1, ROUNDS_MAX], returning (rounds, warning_or_None).

    Gains plateau at 2-3 rounds while cost grows multiplicatively, so the cap is
    hard at 4: values above it are clamped with a warning rather than honoured.
    """
    if rounds < 1:
        return 1, "--rounds must be at least 1; using 1."
    if rounds > ROUNDS_MAX:
        return ROUNDS_MAX, f"--rounds capped at {ROUNDS_MAX} (cost grows multiplicatively); using {ROUNDS_MAX}."
    return rounds, None


def build_debate_turn_prompt(
    question: str, prior: list[tuple[str, str]]
) -> str:
    """Prompt for one debater turn.

    `prior` is the other debaters' latest answers as (label, text) pairs, kept
    anonymized ("the other participant") so a debater can't anchor on a brand.
    With no prior answers (round 1, first debater) the debater answers cold; once
    there is prior context the adversarial instruction is appended.
    """
    if not prior:
        return f"## Question\n\n{question}\n\n## Your answer\n"
    others = "\n\n".join(f"### {label}\n\n{text.strip()}" for label, text in prior)
    return (
        f"## Question\n\n{question}\n\n"
        f"## The other participant's latest answer\n\n{others}\n\n"
        f"## Instruction\n\n{ADVERSARIAL_INSTRUCTION}\n\n## Your answer\n"
    )


def build_judge_prompt(
    question: str,
    transcript: list[RunResult],
    rng: random.Random | None = None,
) -> tuple[str, dict[str, str]]:
    """Build the judge prompt from the debate transcript, anonymized + shuffled.

    The transcript is the per-turn RunResults; the judge sees only the final
    answer text of each turn, relabelled "Participant 1/2/.." in shuffled order
    (a model is judging, so brand/position bias is killed per the research). The
    label_map maps each label back to the real provider for the caller, though
    debate does not reveal it in the verdict.
    """
    turns = [r for r in transcript if r.status == "ok"]
    shuffled = list(turns)
    (rng or random).shuffle(shuffled)
    sections: list[str] = []
    label_map: dict[str, str] = {}
    for offset, result in enumerate(shuffled):
        label = f"Participant {offset + 1}"
        sections.append(f"### {label}\n\n{result.stdout.strip()}")
        label_map[label] = result.provider
    prompt = (
        f"{JUDGE_PROMPT}\n\n"
        f"## User question\n\n{question}\n\n"
        f"## Debate transcript\n\n" + "\n\n".join(sections) + "\n\n## Your final answer\n"
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


def render_synthesis_block(result: RunResult, synthesizer: str) -> str:
    heading = f"## synthesis · via {synthesizer} - {_status_label(result.status)} - {result.elapsed:.1f}s"
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


def synthesis_record(result: RunResult, synthesizer: str) -> dict:
    return {
        "type": "synthesis",
        "synthesizer": synthesizer,
        "status": result.status,
        "elapsed": round(result.elapsed, 3),
        "text": result.stdout,
        "stderr": result.stderr,
    }


def render_debate_turn_block(result: RunResult, round_num: int) -> str:
    model = f" ({result.model})" if result.model else ""
    heading = (
        f"## round {round_num} · {result.provider}{model} - "
        f"{_status_label(result.status)} - {result.elapsed:.1f}s"
    )
    return "\n".join([heading, "", *_body(result)])


def render_judge_block(result: RunResult, judge: str) -> str:
    heading = f"## verdict · judge {judge} - {_status_label(result.status)} - {result.elapsed:.1f}s"
    return "\n".join([heading, "", *_body(result)])


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


def judge_record(result: RunResult, judge: str) -> dict:
    return {
        "type": "verdict",
        "judge": judge,
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


def parse_model_overrides(entries: list[str] | None) -> dict[str, str]:
    """Parse repeated `-m provider=model` flags into a {provider: model} dict.

    Each entry must contain `=` and name a known provider. The model string is
    passed through verbatim (formats differ per tool); the underlying CLI
    validates it. Bad format or unknown provider raises BadParameter.
    """
    models: dict[str, str] = {}
    for entry in entries or []:
        if "=" not in entry:
            raise typer.BadParameter(f"--model expects PROVIDER=MODEL, got: {entry!r}")
        provider, model = entry.split("=", 1)
        provider = provider.strip()
        if provider not in PROVIDERS:
            raise typer.BadParameter(
                f"Unknown provider in --model: {provider!r}. Known: {', '.join(PROVIDERS)}."
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


def _note(message: str) -> None:
    """Progress and selection notes go to stderr so stdout stays pure content."""
    typer.echo(message, err=True)


def _emit(text: str) -> None:
    sys.stdout.write(text.rstrip("\n") + "\n")
    sys.stdout.flush()


async def _collect(
    providers: list[Provider],
    prompt: str,
    timeout: float,
    json_output: bool,
    models: dict[str, str] | None = None,
    yolo: bool = False,
) -> list[RunResult]:
    results: list[RunResult] = []
    async for result in stream(providers, prompt, timeout, models, yolo):
        results.append(result)
        _emit(json.dumps(result_record(result)) if json_output else render_block(result))
    return results


# --------------------------------------------------------------------------- #
# Shared options: every prompt verb (ask, distill, and later debate) takes the
# same selection/IO options. They are declared ONCE as reusable Annotated types
# and resolved ONCE by `resolve_run`, which returns a RunConfig the verbs act on.
# Item 008 (config) plugs a config-default merge into resolve_run; stage 2
# (debate) reuses both the option types and the resolver unchanged.
# --------------------------------------------------------------------------- #

PromptArg = Annotated[
    str | None, typer.Argument(help="Prompt to send to each agent. Use '-' for stdin.")
]
NumOpt = Annotated[
    int, typer.Option("--num", "-n", help="How many agents to ask, taken in priority order.")
]
ProviderOpt = Annotated[
    list[str] | None,
    typer.Option("--provider", "-p", help="Pin specific agent(s). Repeatable. Overrides --num."),
]
ExcludeOpt = Annotated[
    list[str] | None,
    typer.Option("--exclude", "-x", help="Drop agent(s) from the run. Repeatable."),
]
ModelOpt = Annotated[
    list[str] | None,
    typer.Option("--model", "-m", help="Override a tool's model: PROVIDER=MODEL. Repeatable."),
]
FileOpt = Annotated[
    Path | None, typer.Option("--file", "-f", help="Read the prompt from a file or '-' for stdin.")
]
TimeoutOpt = Annotated[float, typer.Option("--timeout", "-t", help="Per-agent timeout in seconds.")]
JsonOpt = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSONL.")]
YoloOpt = Annotated[
    bool, typer.Option("--yolo", help="Grant agents full write access (default is read-only).")
]


@dataclass(frozen=True)
class RunConfig:
    """Everything a prompt verb needs after the shared options are resolved."""

    prompt: str
    selected: list[Provider]
    models: dict[str, str]
    timeout: float
    json_output: bool
    yolo: bool


def resolve_run(
    prompt: str | None,
    file: Path | None,
    num: int,
    provider: list[str] | None,
    exclude: list[str] | None,
    model: list[str] | None,
    timeout: float,
    json_output: bool,
    yolo: bool,
) -> RunConfig:
    """Resolve the shared options into a RunConfig, emitting the selection note.

    The single place ask/distill (and later debate) funnel the shared options
    through: read the prompt, parse model overrides, select providers, and print
    the stderr selection note (including agy's honest partial-protection note).
    Raises typer.BadParameter on bad input and typer.Exit(1) when nothing runs.
    Item 008 will merge config defaults here, ahead of selection.
    """
    prompt_text = _read_prompt(prompt, file)
    if not prompt_text:
        raise typer.BadParameter("Prompt cannot be empty.")
    if num < 1:
        raise typer.BadParameter("--num must be at least 1.")

    models = parse_model_overrides(model)

    try:
        selected, skipped = select_for_run(
            num, tuple(provider) if provider else None, tuple(exclude) if exclude else ()
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if not selected:
        _note("No agents available. Run `moa doctor` to see which CLIs are installed.")
        raise typer.Exit(code=1)

    mode = "yolo (full write access)" if yolo else "read-only"
    note = f"Asking {', '.join(p.name for p in selected)} (timeout {timeout:g}s, {mode})"
    if skipped:
        note += f"; skipped (not installed): {', '.join(skipped)}"
    if exclude:
        note += f"; excluded: {', '.join(exclude)}"
    # Providers whose default mode is only partial protection (e.g. agy's
    # --sandbox still allows file writes) carry an honest note so the user knows
    # what's actually guarded (not relevant under --yolo).
    if not yolo:
        for p in selected:
            if p.readonly_note:
                note += f"; note: {p.readonly_note}"
    _note(note)

    return RunConfig(prompt_text, selected, models, timeout, json_output, yolo)


@app.command()
def ask(
    prompt: PromptArg = None,
    num: NumOpt = 3,
    provider: ProviderOpt = None,
    exclude: ExcludeOpt = None,
    model: ModelOpt = None,
    file: FileOpt = None,
    timeout: TimeoutOpt = 180,
    json_output: JsonOpt = False,
    yolo: YoloOpt = False,
) -> None:
    """Council / peer review: ask N agents in parallel; answers stream back as each finishes."""
    cfg = resolve_run(prompt, file, num, provider, exclude, model, timeout, json_output, yolo)

    results = asyncio.run(
        _collect(cfg.selected, cfg.prompt, cfg.timeout, cfg.json_output, cfg.models, cfg.yolo)
    )
    if not any(r.status == "ok" for r in results):
        raise typer.Exit(code=1)


@app.command()
def distill(
    prompt: PromptArg = None,
    num: NumOpt = 3,
    provider: ProviderOpt = None,
    exclude: ExcludeOpt = None,
    model: ModelOpt = None,
    file: FileOpt = None,
    timeout: TimeoutOpt = 180,
    synthesizer: Annotated[
        str,
        typer.Option("--synthesizer", "-s", help="Who distills: auto | random | a provider name."),
    ] = "auto",
    json_output: JsonOpt = False,
    yolo: YoloOpt = False,
) -> None:
    """Synthesis: run the council, then one aggregator merges the answers into one."""
    cfg = resolve_run(prompt, file, num, provider, exclude, model, timeout, json_output, yolo)

    results = asyncio.run(
        _collect(cfg.selected, cfg.prompt, cfg.timeout, cfg.json_output, cfg.models, cfg.yolo)
    )
    successes = [r for r in results if r.status == "ok"]

    _run_synthesis(cfg, results, successes, synthesizer)

    if not successes:
        raise typer.Exit(code=1)


def _run_synthesis(
    cfg: RunConfig,
    results: list[RunResult],
    successes: list[RunResult],
    synthesizer: str,
) -> None:
    if len(successes) < 2:
        _note("Distill skipped: need at least 2 successful responses.")
        return

    candidates = [p.name for p in cfg.selected]
    try:
        synth_name = choose_synthesizer(synthesizer, candidates)
    except ValueError as exc:
        _note(f"Distill skipped: {exc}")
        return

    # The aggregator always gets the proposer answers anonymized + shuffled so it
    # can't favour a brand (item 002, no toggle). The A/B/C labels stay internal;
    # the human already sees real names on the response blocks above.
    synth_prompt, _label_map = build_synthesis_prompt(cfg.prompt, results, blind=True)
    _note(f"Distilling with {synth_name}...")
    synth_model = cfg.models.get(synth_name)
    synth_result = asyncio.run(
        run_provider(PROVIDERS[synth_name], synth_prompt, cfg.timeout, synth_model, cfg.yolo)
    )

    if cfg.json_output:
        _emit(json.dumps(synthesis_record(synth_result, synth_name)))
    else:
        _emit(render_synthesis_block(synth_result, synth_name))


RoundsOpt = Annotated[
    int, typer.Option("--rounds", "-r", help=f"Debate rounds (default 2, hard max {ROUNDS_MAX}).")
]
JudgeOpt = Annotated[
    str | None,
    typer.Option("--judge", "-j", help="Provider that judges (must not be a debater)."),
]


@app.command()
def debate(
    prompt: PromptArg = None,
    num: NumOpt = 3,
    provider: ProviderOpt = None,
    exclude: ExcludeOpt = None,
    model: ModelOpt = None,
    file: FileOpt = None,
    timeout: TimeoutOpt = 180,
    rounds: RoundsOpt = 2,
    judge: JudgeOpt = None,
    json_output: JsonOpt = False,
    yolo: YoloOpt = False,
) -> None:
    """Debate: debaters answer and critique each other across rounds; a neutral judge gives the verdict."""
    cfg = resolve_run(prompt, file, num, provider, exclude, model, timeout, json_output, yolo)

    rounds, warning = clamp_rounds(rounds)
    if warning:
        _note(warning)

    try:
        debaters, judge_provider = assign_debate_roles(cfg.selected, judge)
    except ValueError as exc:
        _note(f"debate: {exc}")
        raise typer.Exit(code=1) from exc

    _note(
        f"Debating: {', '.join(p.name for p in debaters)} over {rounds} round(s), "
        f"judge {judge_provider.name}. Debate is the costliest mode "
        f"(~{len(debaters) * rounds + 1} model calls) and can converge on a wrong answer."
    )

    transcript = asyncio.run(_run_debate(cfg, debaters, judge_provider, rounds))
    if not any(r.status == "ok" for r in transcript):
        raise typer.Exit(code=1)


def _signals_convergence(result: RunResult) -> bool:
    """A debater concedes when its answer opens with the convergence marker."""
    return result.status == "ok" and result.stdout.strip().upper().startswith(CONVERGENCE_MARKER)


async def _run_debate(
    cfg: RunConfig,
    debaters: list[Provider],
    judge: Provider,
    rounds: int,
) -> list[RunResult]:
    """Run the sequential debate, then the judge. Returns the full transcript.

    Each debater keeps its latest answer in `latest`. A turn shows the debater
    the OTHER debaters' latest answers (anonymized) plus the adversarial
    instruction; the very first turn (no priors yet) is a cold answer. Turns
    stream as they complete (stderr progress + stdout/JSON block). If every
    active debater signals "no substantive change" in a round, the debate stops
    before the cap. The judge then reads the blind+shuffled transcript and writes
    the verdict last.
    """
    transcript: list[RunResult] = []
    latest: dict[str, RunResult] = {}

    for round_num in range(1, rounds + 1):
        converged_this_round = True
        for debater in debaters:
            prior = [
                ("the other participant", latest[other.name].stdout)
                for other in debaters
                if other.name != debater.name and other.name in latest
            ]
            turn_prompt = build_debate_turn_prompt(cfg.prompt, prior)
            _note(f"Round {round_num}: {debater.name} responding...")
            result = await run_provider(
                debater, turn_prompt, cfg.timeout, cfg.models.get(debater.name), cfg.yolo
            )
            transcript.append(result)
            latest[debater.name] = result
            _emit(
                json.dumps(debate_turn_record(result, round_num))
                if cfg.json_output
                else render_debate_turn_block(result, round_num)
            )
            # A debater that errors out is not "converged"; only an explicit
            # no-change signal counts toward an early stop.
            if not _signals_convergence(result):
                converged_this_round = False

        # Round 1 always has at least one cold answer (no prior to converge on),
        # so early-stop is only meaningful from round 2 onward.
        if round_num >= 2 and converged_this_round:
            _note(f"Debate converged after round {round_num} (no substantive changes); stopping early.")
            break

    if not any(r.status == "ok" for r in transcript):
        _note("Debate produced no usable answers; skipping judge.")
        return transcript

    # The judge always sees the transcript anonymized + shuffled (a model is
    # judging; per item 002 there is no toggle). It runs in the same read-only /
    # --yolo mode as the debaters - no permission bypass.
    judge_prompt, _label_map = build_judge_prompt(cfg.prompt, transcript)
    _note(f"Judging with {judge.name}...")
    verdict = await run_provider(
        judge, judge_prompt, cfg.timeout, cfg.models.get(judge.name), cfg.yolo
    )
    transcript.append(verdict)
    _emit(
        json.dumps(judge_record(verdict, judge.name))
        if cfg.json_output
        else render_judge_block(verdict, judge.name)
    )
    return transcript


@app.command()
def doctor() -> None:
    """Show which agent CLIs are installed and their default models."""
    available = available_provider_names()
    missing = missing_provider_names()

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

    typer.echo("Available agents: " + fmt(available))
    typer.echo("Missing agents:   " + fmt(missing))


def main() -> None:
    app()

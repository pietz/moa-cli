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
import tomllib
from collections.abc import AsyncIterator, Callable, Iterable
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
# Debate: sequential adversarial rounds, with a moderator that checks for
# convergence after each round and then writes the verdict from the (anonymized
# + shuffled) full transcript. The literature is clear that debate is the
# costliest and least reliably-beneficial mode: it can converge on a wrong answer
# (conformity), so the verdict prompt weighs correctness/evidence over confidence
# and fluency, and the anonymization holds even when the moderator also debated.
# --------------------------------------------------------------------------- #

ROUNDS_MAX = 4

# Spliced into every debater turn after round 1: the prior answer(s) come first,
# then this instruction tells the debater to attack before answering. Counters
# sycophancy / Degeneration-of-Thought (the model agreeing to reach consensus).
ADVERSARIAL_INSTRUCTION = """Before giving your own answer, critically examine the \
other participant's answer above: identify any errors, weaknesses, unsupported claims, or \
gaps in reasoning. Do NOT agree merely to reach consensus - only concede a point if it is \
genuinely correct. Then give your own best, complete answer to the original question, \
incorporating any valid corrections."""

# The moderator reads the full transcript (anonymized + shuffled) and writes the
# final answer. It must weigh correctness/evidence over confidence/fluency - this
# is where conformity-to-a-wrong-answer is most dangerous, so it never just echoes
# the most fluent or most confident debater.
MODERATOR_VERDICT_PROMPT = """You are the moderator of this debate. Below is a transcript of a \
debate between AI coding assistants who answered the user's question and then critiqued each \
other's answers across several rounds. The participants are anonymized and presented in \
arbitrary order.

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

# After each non-final round the moderator decides whether another round would
# materially help. It replies with a single leading word the caller branches on.
CONVERGENCE_DONE = "DONE"
MODERATOR_CONVERGENCE_PROMPT = """You are the moderator of this debate. Below are the debaters' \
latest answers to the user's question, anonymized. Decide whether they have converged on an \
answer, or at least fully aired and clarified their disagreement, so that another round would \
add nothing material.

Reply with EXACTLY one word on the first line: DONE if the debate should stop now, or CONTINUE \
if another round would materially improve the final answer. Add nothing else."""


def assign_debate_roles(
    selected: list[Provider], moderator: str | None
) -> tuple[list[Provider], Provider]:
    """Split the selected providers into (debaters, moderator).

    The top 2 selected providers debate. The moderator runs the per-round
    convergence check and writes the final verdict; it MAY be one of the debaters.
    `moderator` is "auto" (or None) -> the top-priority selected provider (so the
    default 2-agent debate has agent #1 also moderate), or a provider name that
    must be among the selected providers (pin a non-debating 3rd for a neutral
    moderator). Requires at least 2 selected providers; raises ValueError
    otherwise (the caller turns this into a clean exit - debate never silently
    degrades).
    """
    if len(selected) < 2:
        raise ValueError(
            f"debate needs at least 2 providers (2 debaters); only {len(selected)} available. "
            f"Increase -n, pin more with -p, or install more agents."
        )
    debaters = selected[:2]
    if moderator in (None, "auto"):
        return debaters, selected[0]

    names = [p.name for p in selected]
    if moderator not in PROVIDERS:
        raise ValueError(f"Unknown moderator: {moderator}")
    if moderator not in names:
        raise ValueError(
            f"Moderator {moderator!r} is not among the selected providers ({', '.join(names)}). "
            f"Pin it with -p {moderator} or widen the selection."
        )
    return debaters, next(p for p in selected if p.name == moderator)


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


def build_verdict_prompt(
    question: str,
    transcript: list[RunResult],
    rng: random.Random | None = None,
) -> tuple[str, dict[str, str]]:
    """Build the moderator's final-verdict prompt from the transcript, anonymized
    + shuffled.

    The transcript is the per-turn RunResults; the moderator sees only the final
    answer text of each turn, relabelled "Participant 1/2/.." in shuffled order so
    brand/position bias is killed - this matters even when the moderator is itself
    a debater, since it can't tell which answer is its own. The label_map maps each
    label back to the real provider for the caller, though debate never reveals it.
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
        f"{MODERATOR_VERDICT_PROMPT}\n\n"
        f"## User question\n\n{question}\n\n"
        f"## Debate transcript\n\n" + "\n\n".join(sections) + "\n\n## Your final answer\n"
    )
    return prompt, label_map


def build_convergence_prompt(question: str, latest: list[RunResult]) -> str:
    """The moderator's per-round convergence check. `latest` is the debaters' most
    recent answers, anonymized so the moderator judges substance over brand. The
    expected reply starts with DONE (stop) or CONTINUE (another round helps)."""
    answers = "\n\n".join(
        f"### Participant {i + 1}\n\n{r.stdout.strip()}" for i, r in enumerate(latest)
    )
    return (
        f"{MODERATOR_CONVERGENCE_PROMPT}\n\n"
        f"## User question\n\n{question}\n\n"
        f"## The debaters' latest answers\n\n{answers}\n\n## Your decision\n"
    )


# --------------------------------------------------------------------------- #
# Render: stdout carries content (Markdown or JSONL); stderr carries progress.
# --------------------------------------------------------------------------- #

_STATUS_LABELS = {"ok": "OK", "failed": "FAILED", "timeout": "TIMEOUT", "missing": "MISSING"}

# Width of the separator rule that fronts each answer block. Fixed (not terminal-
# derived) so output is identical whether shown live or piped to a file.
_RULE_WIDTH = 60


def _status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status.upper())


def _rule(label: str) -> str:
    """A centered, box-drawing separator that names the block, e.g.
    `──────── claude (opus) · OK · 2.3s ────────`. Falls back to the bare label
    when it's wider than the rule."""
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
    """True when stdout is not an interactive terminal - piped, redirected, or
    read by another agent (the common "an agent shells out to moa" case). There
    we drop the decorative box-drawing rule and extra blank lines for a plain,
    low-noise `## label` heading that is cheaper for a model to consume."""
    return not sys.stdout.isatty()


def _render(label: str, result: RunResult, plain: bool) -> str:
    """One answer block. In a terminal: two leading blank lines and a centered
    box-drawing rule, for clear visual separation as blocks stream in. When
    piped: a plain `## label` heading with a single blank line, no box-drawing."""
    if plain:
        return "\n".join(["", f"## {label}", "", *_body(result)])
    return "\n".join(["", "", _rule(label), "", *_body(result)])


def render_block(result: RunResult, plain: bool | None = None) -> str:
    if plain is None:
        plain = _plain_output()
    model = f" ({result.model})" if result.model else ""
    label = f"{result.provider}{model} · {_status_label(result.status)} · {result.elapsed:.1f}s"
    return _render(label, result, plain)


def render_synthesis_block(result: RunResult, synthesizer: str, plain: bool | None = None) -> str:
    if plain is None:
        plain = _plain_output()
    label = f"synthesis · via {synthesizer} · {_status_label(result.status)} · {result.elapsed:.1f}s"
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


def render_debate_turn_block(result: RunResult, round_num: int, plain: bool | None = None) -> str:
    if plain is None:
        plain = _plain_output()
    model = f" ({result.model})" if result.model else ""
    label = (
        f"round {round_num} · {result.provider}{model} · "
        f"{_status_label(result.status)} · {result.elapsed:.1f}s"
    )
    return _render(label, result, plain)


def render_verdict_block(result: RunResult, moderator: str, plain: bool | None = None) -> str:
    if plain is None:
        plain = _plain_output()
    label = f"verdict · moderator {moderator} · {_status_label(result.status)} · {result.elapsed:.1f}s"
    return _render(label, result, plain)


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


# --------------------------------------------------------------------------- #
# Config: persisted user defaults at ~/.moa/config.toml.
#
# Precedence is built-in default < config file < CLI flag: a flag always wins,
# the file only changes a default when the flag is omitted, and an absent file
# means today's built-in behaviour. We read with stdlib tomllib (no dep) and
# write with a tiny serializer for this flat schema (scalars, a string list,
# and a [models] string table), so there is no TOML-writer dependency. The
# merge happens once, in resolve_run, so all verbs pick up defaults identically.
# --------------------------------------------------------------------------- #

# Scalar config keys and the type each maps to. `exclude` (list[str]) and the
# `[models]` table are handled separately because they aren't plain scalars.
_CONFIG_SCALARS: dict[str, type] = {"num": int, "timeout": float, "synthesizer": str, "moderator": str}
_CONFIG_KEYS: tuple[str, ...] = (*_CONFIG_SCALARS, "exclude", "models")
# Synthesizer accepts the special modes plus any known provider name.
_SYNTHESIZER_MODES: tuple[str, ...] = ("auto", "first", "random")
# Moderator accepts "auto" (the top-priority selected agent) or a provider name.
_MODERATOR_MODES: tuple[str, ...] = ("auto",)
# The built-in defaults, shown by `config show` when a key isn't in the file.
_CONFIG_DEFAULTS: dict = {
    "num": 3,
    "timeout": 180.0,
    "synthesizer": "auto",
    "moderator": "auto",
    "exclude": [],
    "models": {},
}


def config_dir() -> Path:
    """Directory holding the config file: $MOA_CONFIG_DIR if set, else ~/.moa.

    Honouring the env var is what lets tests point the whole config layer at a
    temp dir so the real ~/.moa is never read or written.
    """
    override = os.environ.get("MOA_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".moa"


def config_path() -> Path:
    return config_dir() / "config.toml"


def _validate_providers(names: Iterable[str], where: str) -> None:
    unknown = [n for n in names if n not in PROVIDERS]
    if unknown:
        raise ValueError(f"Unknown provider(s) in {where}: {', '.join(unknown)}. Known: {', '.join(PROVIDERS)}.")


def _validate_scalar(key: str, value) -> None:
    """Range/value checks shared by load (hand-edited file) and `config set`."""
    if key == "num" and value < 1:
        raise ValueError("num must be at least 1.")
    if key == "timeout" and value <= 0:
        raise ValueError("timeout must be greater than 0.")
    if key == "synthesizer" and value not in (*_SYNTHESIZER_MODES, *PROVIDERS):
        allowed = ", ".join((*_SYNTHESIZER_MODES, *PROVIDERS))
        raise ValueError(f"synthesizer must be one of: {allowed}.")
    if key == "moderator" and value not in (*_MODERATOR_MODES, *PROVIDERS):
        allowed = ", ".join((*_MODERATOR_MODES, *PROVIDERS))
        raise ValueError(f"moderator must be one of: {allowed}.")


def load_config() -> dict:
    """Read and validate the config file. Missing file == empty config.

    Returns a dict with only the keys actually present; values are validated
    and coerced (num->int, timeout->float). Unknown top-level keys and bad
    provider names raise ValueError so callers can surface a clean message.
    """
    path = config_path()
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    unknown = [k for k in raw if k not in _CONFIG_KEYS]
    if unknown:
        raise ValueError(f"Unknown config key(s): {', '.join(unknown)}. Known: {', '.join(_CONFIG_KEYS)}.")

    config: dict = {}
    for key, kind in _CONFIG_SCALARS.items():
        if key in raw:
            try:
                config[key] = kind(raw[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Config key {key!r} must be {kind.__name__}.") from exc
            _validate_scalar(key, config[key])
    if "exclude" in raw:
        value = raw["exclude"]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError("Config key 'exclude' must be a list of provider names.")
        _validate_providers(value, "exclude")
        config["exclude"] = value
    if "models" in raw:
        models = raw["models"]
        if not isinstance(models, dict) or not all(isinstance(v, str) for v in models.values()):
            raise ValueError("Config table '[models]' must map provider names to model strings.")
        _validate_providers(models, "[models]")
        config["models"] = dict(models)
    return config


def _toml_str(value: str) -> str:
    """Serialize a string as a basic TOML string, escaping what would break it.

    Beyond `\\` and `"`, control characters (a stray newline/tab in a model
    string) must be escaped or the file we write back wouldn't reload, so we
    map the named escapes TOML defines and \\uXXXX everything else below 0x20.
    """
    out: list[str] = []
    named = {"\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r", '"': '\\"', "\\": "\\\\"}
    for char in value:
        if char in named:
            out.append(named[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def serialize_config(config: dict) -> str:
    """Render our flat config schema back to TOML text.

    Hand-rolled on purpose (no writer dependency): we only ever emit scalars,
    the `exclude` string list, and the `[models]` string table, in that order.
    """
    lines: list[str] = []
    if "num" in config:
        lines.append(f"num = {int(config['num'])}")
    if "timeout" in config:
        timeout = float(config["timeout"])
        # repr() round-trips losslessly via tomllib; trim a whole number's .0
        # for a tidy file. (`:g` was lossy past 6 significant figures.)
        lines.append(f"timeout = {int(timeout) if timeout.is_integer() else timeout!r}")
    if "synthesizer" in config:
        lines.append(f"synthesizer = {_toml_str(config['synthesizer'])}")
    if "moderator" in config:
        lines.append(f"moderator = {_toml_str(config['moderator'])}")
    if "exclude" in config:
        items = ", ".join(_toml_str(v) for v in config["exclude"])
        lines.append(f"exclude = [{items}]")
    if config.get("models"):
        lines.append("")
        lines.append("[models]")
        for provider, model in config["models"].items():
            lines.append(f"{provider} = {_toml_str(model)}")
    return "\n".join(lines) + "\n" if lines else ""


def write_config(config: dict) -> None:
    """Persist the config, creating the directory and file on first write."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_config(config), encoding="utf-8")


def _read_config_or_empty() -> dict:
    """Load config for a verb-specific merge, swallowing errors.

    resolve_run already loads + validates the config and raises a clean
    BadParameter on a bad file, so by the time a verb merges its own key
    (e.g. distill's synthesizer) the file is known-good; on the off chance it
    isn't we return an empty dict rather than raising a second, duplicate error.
    """
    try:
        return load_config()
    except ValueError:
        return {}


def resolve_option(flag, config_key: str, config: dict, default):
    """Pick a value by precedence: CLI flag > config file > built-in default.

    `flag` is the value Typer parsed (None when the option was omitted). When
    it's None we fall back to the config file, then the built-in default. This
    is the single place the three layers meet, so every verb merges the same way.
    """
    if flag is not None:
        return flag
    if config_key in config:
        return config[config_key]
    return default


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
    emit_blocks: bool = True,
) -> list[RunResult]:
    """Gather every agent's result. With emit_blocks (ask), each complete answer
    is flushed to stdout the instant it arrives. Without it (distill), the
    individual answers are intermediates the user shouldn't see - only the final
    distilled block is content - so we keep stdout clean and just heartbeat each
    arrival to stderr so a multi-agent run doesn't look frozen while it waits."""
    results: list[RunResult] = []
    async for result in stream(providers, prompt, timeout, models, yolo):
        results.append(result)
        if emit_blocks:
            _emit(json.dumps(result_record(result)) if json_output else render_block(result))
        else:
            _note(f"  {result.provider} responded ({_status_label(result.status)}, {result.elapsed:.1f}s)")
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
    int | None, typer.Option("--num", "-n", help="How many agents to ask, taken in priority order.")
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
TimeoutOpt = Annotated[
    float | None, typer.Option("--timeout", "-t", help="Per-agent timeout in seconds.")
]
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
    num: int | None,
    provider: list[str] | None,
    exclude: list[str] | None,
    model: list[str] | None,
    timeout: float | None,
    json_output: bool,
    yolo: bool,
    default_num: int = 3,
) -> RunConfig:
    """Resolve the shared options into a RunConfig, emitting the selection note.

    The single place ask/distill/debate funnel the shared options through: read
    the prompt, MERGE the persisted config (built-in default < config file < CLI
    flag), parse model overrides, select providers, and print the stderr
    selection note (including agy's honest partial-protection note). Every verb
    picks up config defaults identically because the merge lives only here.
    `default_num` is the built-in fallback when neither flag nor config sets num
    (debate passes 2, since it only needs 2 agents). Raises typer.BadParameter on
    bad input and typer.Exit(1) when nothing runs.
    """
    prompt_text = _read_prompt(prompt, file)
    if not prompt_text:
        raise typer.BadParameter("Prompt cannot be empty.")

    # Merge built-in default < config file < CLI flag for every shared option.
    try:
        config = load_config()
    except ValueError as exc:
        raise typer.BadParameter(f"{config_path()}: {exc}") from exc

    num = resolve_option(num, "num", config, default_num)
    timeout = resolve_option(timeout, "timeout", config, 180.0)
    # Repeatable flags are an empty list when omitted, not None, so treat empty
    # as "fall back to config" for exclude.
    exclude_names = tuple(exclude) if exclude else tuple(config.get("exclude", ()))
    # CLI -m overrides win per-provider over config [models]; unnamed providers
    # keep their config value, then their built-in default.
    models = {**config.get("models", {}), **parse_model_overrides(model)}

    if num < 1:
        raise typer.BadParameter("--num must be at least 1.")

    try:
        selected, skipped = select_for_run(
            num, tuple(provider) if provider else None, exclude_names
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
    if exclude_names:
        note += f"; excluded: {', '.join(exclude_names)}"
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
    num: NumOpt = None,
    provider: ProviderOpt = None,
    exclude: ExcludeOpt = None,
    model: ModelOpt = None,
    file: FileOpt = None,
    timeout: TimeoutOpt = None,
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
    num: NumOpt = None,
    provider: ProviderOpt = None,
    exclude: ExcludeOpt = None,
    model: ModelOpt = None,
    file: FileOpt = None,
    timeout: TimeoutOpt = None,
    synthesizer: Annotated[
        str | None,
        typer.Option("--synthesizer", "-s", help="Who distills: auto | random | a provider name."),
    ] = None,
    json_output: JsonOpt = False,
    yolo: YoloOpt = False,
) -> None:
    """Synthesis: run the council, then one aggregator merges the answers into one."""
    cfg = resolve_run(prompt, file, num, provider, exclude, model, timeout, json_output, yolo)

    # `synthesizer` is verb-specific (not in RunConfig) but still persistable, so
    # it merges through the same precedence: CLI flag > config file > built-in.
    synthesizer = resolve_option(synthesizer, "synthesizer", _read_config_or_empty(), "auto")

    # distill returns only the merged answer, so the proposer responses are
    # intermediates: collect them without printing each to stdout.
    results = asyncio.run(
        _collect(
            cfg.selected, cfg.prompt, cfg.timeout, cfg.json_output, cfg.models, cfg.yolo,
            emit_blocks=False,
        )
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
ModeratorOpt = Annotated[
    str | None,
    typer.Option(
        "--moderator", "-j",
        help="Moderator that checks convergence and writes the verdict: auto | a provider.",
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
    """Debate: two debaters answer and critique each other across rounds; a moderator checks convergence and writes the verdict."""
    # Debate only needs 2 agents (the moderator may also be a debater), so its
    # built-in default selection is 2, not the usual 3.
    cfg = resolve_run(
        prompt, file, num, provider, exclude, model, timeout, json_output, yolo, default_num=2
    )

    # moderator is verb-specific (like distill's synthesizer) but persistable, so
    # it merges through the same precedence: CLI flag > config file > built-in.
    moderator = resolve_option(moderator, "moderator", _read_config_or_empty(), "auto")

    rounds, warning = clamp_rounds(rounds)
    if warning:
        _note(warning)

    try:
        debaters, moderator_provider = assign_debate_roles(cfg.selected, moderator)
    except ValueError as exc:
        _note(f"debate: {exc}")
        raise typer.Exit(code=1) from exc

    _note(
        f"Debating: {', '.join(p.name for p in debaters)} over up to {rounds} round(s), "
        f"moderator {moderator_provider.name}. Debate is the costliest mode and can "
        f"converge on a wrong answer."
    )

    transcript = asyncio.run(_run_debate(cfg, debaters, moderator_provider, rounds))
    if not any(r.status == "ok" for r in transcript):
        raise typer.Exit(code=1)


async def _moderator_signals_done(
    cfg: RunConfig, moderator: Provider, latest_ok: list[RunResult], round_num: int
) -> bool:
    """Ask the moderator whether the debate has converged. Returns True (stop)
    only on a clean DONE reply; a failed or CONTINUE check keeps debating."""
    prompt = build_convergence_prompt(cfg.prompt, latest_ok)
    _note(f"Round {round_num}: moderator {moderator.name} checking for convergence...")
    result = await run_provider(
        moderator, prompt, cfg.timeout, cfg.models.get(moderator.name), cfg.yolo
    )
    done = result.status == "ok" and result.stdout.strip().upper().startswith(CONVERGENCE_DONE)
    if done:
        _note(f"Moderator {moderator.name}: converged; stopping after round {round_num}.")
    return done


async def _run_debate(
    cfg: RunConfig,
    debaters: list[Provider],
    moderator: Provider,
    rounds: int,
) -> list[RunResult]:
    """Run the sequential debate, then the moderator's verdict. Returns the full
    transcript.

    Each debater keeps its latest answer in `latest`. A turn shows the debater the
    OTHER debaters' latest answers (anonymized) plus the adversarial instruction;
    the very first turn (no priors yet) is a cold answer. Turns stream as they
    complete (stderr progress + stdout/JSON block). After each non-final round the
    moderator decides whether the debate has converged and can stop early. The
    moderator then reads the blind+shuffled transcript and writes the verdict last
    (it may itself be a debater - the anonymization stops it favouring its own
    answer).
    """
    transcript: list[RunResult] = []
    latest: dict[str, RunResult] = {}

    for round_num in range(1, rounds + 1):
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

        # After each non-final round, let the moderator stop early if the debaters
        # have converged. Needs both debaters' latest answers to compare.
        if round_num < rounds:
            latest_ok = [
                latest[d.name] for d in debaters
                if d.name in latest and latest[d.name].status == "ok"
            ]
            if len(latest_ok) >= 2 and await _moderator_signals_done(
                cfg, moderator, latest_ok, round_num
            ):
                break

    if not any(r.status == "ok" for r in transcript):
        _note("Debate produced no usable answers; skipping the moderator verdict.")
        return transcript

    # The moderator always sees the transcript anonymized + shuffled (a model is
    # judging; no toggle). It runs in the same read-only / --yolo mode as the
    # debaters - no permission bypass.
    verdict_prompt, _label_map = build_verdict_prompt(cfg.prompt, transcript)
    _note(f"Moderator {moderator.name} writing the final answer...")
    verdict = await run_provider(
        moderator, verdict_prompt, cfg.timeout, cfg.models.get(moderator.name), cfg.yolo
    )
    transcript.append(verdict)
    _emit(
        json.dumps(verdict_record(verdict, moderator.name))
        if cfg.json_output
        else render_verdict_block(verdict, moderator.name)
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


# --------------------------------------------------------------------------- #
# config subcommand: inspect and edit the persisted defaults.
# --------------------------------------------------------------------------- #

config_app = typer.Typer(
    name="config",
    help="Inspect and edit persisted defaults at ~/.moa/config.toml (override the dir with $MOA_CONFIG_DIR).",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(config_app)


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
    """Print the effective config (built-in defaults merged with the file) and the file path.

    Output is the same TOML we write, so what you see matches what's stored.
    """
    effective = {**_CONFIG_DEFAULTS, **_load_config_or_exit()}
    typer.echo(f"# {config_path()}")
    typer.echo(serialize_config(effective).rstrip("\n"))


@config_app.command("set")
def config_set(
    key: Annotated[str, typer.Argument(help="Config key: num | timeout | synthesizer | moderator | exclude | model.")],
    value: Annotated[str, typer.Argument(help="Value. For models: PROVIDER=MODEL. For exclude: comma-separated names.")],
) -> None:
    """Write a value to the config file, creating the dir/file if missing."""
    config = _load_config_or_exit()

    if key == "model":
        if "=" not in value:
            raise typer.BadParameter("model expects PROVIDER=MODEL, e.g. `moa config set model claude=sonnet`.")
        provider, model = value.split("=", 1)
        provider = provider.strip()
        if provider not in PROVIDERS:
            raise typer.BadParameter(f"Unknown provider: {provider!r}. Known: {', '.join(PROVIDERS)}.")
        config.setdefault("models", {})[provider] = model
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
            raise typer.BadParameter(f"{key} must be {kind.__name__}, got {value!r}.") from exc
        try:
            _validate_scalar(key, coerced)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        config[key] = coerced
    else:
        known = "num, timeout, synthesizer, moderator, exclude, model"
        raise typer.BadParameter(f"Unknown config key: {key!r}. Known: {known}.")

    write_config(config)
    typer.echo(f"Set {key} in {config_path()}")


@config_app.command("unset")
def config_unset(
    key: Annotated[str, typer.Argument(help="Config key to remove. Use `model PROVIDER` to drop one model.")],
    provider: Annotated[str | None, typer.Argument(help="Provider name, only when key is 'model'.")] = None,
) -> None:
    """Remove a key from the config file (or a single model with `unset model PROVIDER`)."""
    config = _load_config_or_exit()

    if key == "model":
        if not provider:
            raise typer.BadParameter("unset model expects a provider, e.g. `moa config unset model claude`.")
        models = config.get("models", {})
        if provider in models:
            del models[provider]
            if not models:
                config.pop("models", None)
            write_config(config)
            typer.echo(f"Unset model {provider} in {config_path()}")
        else:
            typer.echo(f"model {provider} was not set.")
        return

    if key not in _CONFIG_KEYS:
        raise typer.BadParameter(f"Unknown config key: {key!r}. Known: {', '.join(_CONFIG_KEYS)}.")
    if key in config:
        del config[key]
        write_config(config)
        typer.echo(f"Unset {key} in {config_path()}")
    else:
        typer.echo(f"{key} was not set.")


def main() -> None:
    app()

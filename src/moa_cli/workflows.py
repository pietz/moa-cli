"""Pure domain logic and prompts for synthesis and debate workflows."""

from __future__ import annotations

import random

from .execution import RunResult
from .providers import PROVIDERS, Provider

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

ROUNDS_MAX = 4

ADVERSARIAL_INSTRUCTION = """Before giving your own answer, critically examine the \
other participant's answer above: identify any errors, weaknesses, unsupported claims, or \
gaps in reasoning. Do NOT agree merely to reach consensus - only concede a point if it is \
genuinely correct. Work toward closing the debate, not expanding it: resolve the points \
already on the table rather than opening new ones, and do not introduce a fresh angle just \
to have something to add. Once you and the other participant substantively agree on the core \
answer, say so plainly instead of finding a new dimension to debate. Then give your own best, \
complete answer to the original question, incorporating any valid corrections."""

DEBATER_OPENING_INSTRUCTION = """This is the opening move of a debate: another \
participant will critique your answer next, so take a clear, specific position on the \
question and justify it. A vague, hedging, or one-word answer gives them nothing \
meaningful to engage with. Give your best, complete answer."""

DEBATER_STYLE = """Write conversationally, like one expert talking to another, in a few short \
paragraphs at most. Do not use headings or section labels. Refer to the other participant as \
"you", not by name."""

MODERATOR_VERDICT_PROMPT = """You are the moderator of this debate. Below is a transcript of a \
debate between AI coding assistants who answered the user's question and then critiqued each \
other's answers across several rounds. The participants are anonymized and presented in \
arbitrary order.

Your task is to read the full debate and write the final answer the user should walk away with. \
Weigh correctness and the strength of evidence and reasoning ABOVE confidence, fluency, and \
assertiveness - a wrong answer stated confidently must not win.
- If the participants converged on a sound conclusion, state it clearly and confidently.
- If a disagreement is genuinely decidable on the merits, decide it and explain why the winning \
position is right.
- If they settled into a real, unresolved disagreement, do not paper over it: state where they \
agree, then lay out each position and the strongest reason behind it so the user can decide.

Guidelines:
- Lead with the answer, not a recap of who said what. Do not pick a "winner" by name or refer to \
participants, rounds, or the debate.
- Where the participants agree, verify the agreement is actually sound rather than shared error.
- Keep what is correct and well-supported; discard what is wrong, unsupported, or merely \
asserted.
- Do not invent information that the debate does not support."""

CONVERGENCE_DONE = "DONE"

MODERATOR_CONVERGENCE_PROMPT = """You are the moderator of this debate. Below are the debaters' \
latest answers to the user's question, anonymized. Decide whether the debate has converged: \
every participant has given a substantive answer AND they now either agree on a conclusion, or \
have fully and clearly stated an irreconcilable disagreement along with their reasons.

Default to CONTINUE. Reply CONTINUE if any of these hold:
- Any participant has not substantively engaged (a bare yes/no, a trivial or contentless \
reply, or no real justification).
- A participant's reasoning, evidence, or core position is still unclear, shifting, or \
not yet addressed by the other side.
- Another round could materially sharpen, correct, or deepen the answers.

Reply DONE only when the participants have genuinely engaged throughout and a further round \
would merely repeat what is already on the table.

Reply with EXACTLY one word on the first line: DONE or CONTINUE. Add nothing else."""


def choose_synthesizer(
    choice: str,
    candidates: list[str],
    rng: random.Random | None = None,
) -> str:
    if not candidates:
        raise ValueError("No candidate providers available to synthesize.")
    if choice in ("auto", "first"):
        return candidates[0]
    if choice == "random":
        return (rng or random).choice(candidates)
    if choice in candidates:
        return choice
    if choice in PROVIDERS:
        raise ValueError(
            f"Synthesizer {choice!r} is not among the selected providers "
            f"({', '.join(candidates)}). Pin it with -p {choice} or widen "
            "the selection."
        )
    raise ValueError(f"Unknown synthesizer: {choice}")


def build_synthesis_prompt(
    question: str,
    results: list[RunResult],
    blind: bool,
    rng: random.Random | None = None,
) -> tuple[str, dict[str, str]]:
    answers = [result for result in results if result.status == "ok"]
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
        f"## Responses to synthesize\n\n"
        + "\n\n".join(sections)
        + "\n\n## Your synthesized answer\n"
    )
    return prompt, label_map


def assign_debate_roles(
    selected: list[Provider], moderator: str | None
) -> tuple[list[Provider], Provider]:
    if len(selected) < 2:
        raise ValueError(
            "debate needs at least 2 providers (2 debaters); "
            f"only {len(selected)} available. Increase -n, pin more with -p, "
            "or install more agents."
        )
    debaters = selected[:2]
    if moderator in (None, "auto"):
        # Prefer a neutral moderator (a third selected agent that doesn't debate);
        # with only two agents, the top-priority one moderates its own debate.
        moderator_provider = selected[2] if len(selected) >= 3 else selected[0]
        return debaters, moderator_provider

    names = [provider.name for provider in selected]
    if moderator not in PROVIDERS:
        raise ValueError(f"Unknown moderator: {moderator}")
    if moderator not in names:
        raise ValueError(
            f"Moderator {moderator!r} is not among the selected providers "
            f"({', '.join(names)}). Pin it with -p {moderator} or widen "
            "the selection."
        )
    return debaters, next(
        provider for provider in selected if provider.name == moderator
    )


def clamp_rounds(rounds: int) -> tuple[int, str | None]:
    if rounds < 1:
        return 1, "--rounds must be at least 1; using 1."
    if rounds > ROUNDS_MAX:
        return (
            ROUNDS_MAX,
            f"--rounds capped at {ROUNDS_MAX} "
            f"(cost grows multiplicatively); using {ROUNDS_MAX}.",
        )
    return rounds, None


def build_debate_turn_prompt(question: str, prior: list[tuple[str, str]]) -> str:
    if not prior:
        return (
            f"## Question\n\n{question}\n\n"
            f"## Instruction\n\n{DEBATER_OPENING_INSTRUCTION}\n\n{DEBATER_STYLE}\n\n"
            "## Your answer\n"
        )
    others = "\n\n".join(f"### {label}\n\n{text.strip()}" for label, text in prior)
    return (
        f"## Question\n\n{question}\n\n"
        f"## The other participant's latest answer\n\n{others}\n\n"
        f"## Instruction\n\n{ADVERSARIAL_INSTRUCTION}\n\n{DEBATER_STYLE}\n\n"
        "## Your answer\n"
    )


def build_verdict_prompt(
    question: str,
    transcript: list[RunResult],
    rng: random.Random | None = None,
) -> tuple[str, dict[str, str]]:
    turns = [result for result in transcript if result.status == "ok"]
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
        f"## Debate transcript\n\n"
        + "\n\n".join(sections)
        + "\n\n## Your final answer\n"
    )
    return prompt, label_map


def build_convergence_prompt(question: str, latest: list[RunResult]) -> str:
    answers = "\n\n".join(
        f"### Participant {index + 1}\n\n{result.stdout.strip()}"
        for index, result in enumerate(latest)
    )
    return (
        f"{MODERATOR_CONVERGENCE_PROMPT}\n\n"
        f"## User question\n\n{question}\n\n"
        f"## The debaters' latest answers\n\n{answers}\n\n"
        "## Your decision\n"
    )

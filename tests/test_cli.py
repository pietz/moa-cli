import random
import re

import pytest
from typer.testing import CliRunner

from moa_cli import cli, execution, providers
from moa_cli.execution import RunResult
from moa_cli.providers import PROVIDERS, Provider
from moa_cli.workflows import (
    ADVERSARIAL_INSTRUCTION,
    DEBATER_OPENING_INSTRUCTION,
    SYNTHESIZER_PROMPT,
    assign_debate_roles,
    build_convergence_prompt,
    build_debate_turn_prompt,
    build_verdict_prompt,
    clamp_rounds,
)


def test_legacy_cli_import_surface_remains_available() -> None:
    for name in (
        "CommandBuilder",
        "Status",
        "MODERATOR_VERDICT_PROMPT",
        "MODERATOR_CONVERGENCE_PROMPT",
    ):
        assert hasattr(cli, name)


def _ok(provider: str, text: str) -> RunResult:
    return RunResult(provider, "m", "ok", text, "", 1.0, 0)


def _failed(provider: str, detail: str = "failed") -> RunResult:
    return RunResult(provider, "m", "failed", "", detail, 1.0, 1)


# --- ask selection note -----------------------------------------------------


def _fake_stream(*results: RunResult):
    async def stream(providers, prompt, timeout, models=None, yolo=False, efforts=None):
        for r in results:
            yield r

    return stream


def test_ask_emits_agy_partial_protection_note(monkeypatch) -> None:
    # When agy runs in the default (non-yolo) mode, the stderr selection note
    # must honestly state agy is shell-sandboxed but can still edit files.
    installed = {"claude", "codex", "agy", "opencode"}
    monkeypatch.setattr(
        providers.shutil, "which", lambda exe: exe if exe in installed else None
    )
    monkeypatch.setattr(cli, "stream", _fake_stream(_ok("agy", "OK")))
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ask", "-p", "agy", "hi"])
    assert result.exit_code == 0
    assert (
        "agy is shell-sandboxed but can still edit files (no true read-only mode)"
        in result.stderr
    )


def test_ask_omits_agy_note_under_yolo(monkeypatch) -> None:
    # Under --yolo agy drops --sandbox (full access), so no partial-protection note.
    installed = {"claude", "codex", "agy", "opencode"}
    monkeypatch.setattr(
        providers.shutil, "which", lambda exe: exe if exe in installed else None
    )
    monkeypatch.setattr(cli, "stream", _fake_stream(_ok("agy", "OK")))
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ask", "-p", "agy", "--yolo", "hi"])
    assert result.exit_code == 0
    assert "can still edit files" not in result.stderr


# --- verbs (ask / distill / doctor) -----------------------------------------


def _install_all(monkeypatch) -> None:
    installed = {"claude", "codex", "agy", "opencode"}
    monkeypatch.setattr(
        providers.shutil, "which", lambda exe: exe if exe in installed else None
    )


def test_subcommands_registered() -> None:
    # The CLI is a set of verbs now, not a single command.
    runner = CliRunner()
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for verb in ("ask", "distill", "debate", "doctor"):
        assert verb in result.stdout


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _help(args: list[str]) -> str:
    """Invoke `--help` and return the ANSI-stripped stdout.

    Rich colours each dash of an option separately (e.g. `-` then `-num`), so a
    coloured terminal - which CI is, but a captured local run often isn't -
    breaks a naive `"--num" in stdout`. Strip the escapes so the assertions hold
    regardless of whether colour is on."""
    result = CliRunner().invoke(cli.app, [*args, "--help"])
    assert result.exit_code == 0
    return _ANSI.sub("", result.stdout)


_SHARED_OPTS = (
    "--num",
    "--provider",
    "--exclude",
    "--model",
    "--timeout",
    "--file",
    "--json",
    "--yolo",
)


def test_ask_has_no_synth_flags() -> None:
    # --synth and --synthesizer were removed from ask; verbs replace them.
    out = _help(["ask"])
    assert "--synth" not in out
    assert "--synthesizer" not in out


def test_ask_help_shows_shared_options() -> None:
    out = _help(["ask"])
    for opt in _SHARED_OPTS:
        assert opt in out


def test_distill_help_shows_shared_options_and_synthesizer() -> None:
    out = _help(["distill"])
    for opt in _SHARED_OPTS:
        assert opt in out
    # --synthesizer lives only on distill.
    assert "--synthesizer" in out


def test_ask_is_council_no_synthesis(monkeypatch) -> None:
    # ask fans out and prints each answer; it never emits a synthesis/distill block.
    _install_all(monkeypatch)
    monkeypatch.setattr(
        cli, "stream", _fake_stream(_ok("claude", "A"), _ok("codex", "B"))
    )
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ask", "-p", "claude", "-p", "codex", "hi"])
    assert result.exit_code == 0
    assert "claude (m) ·" in result.stdout and "codex (m) ·" in result.stdout
    assert "synthesis" not in result.stdout


def test_distill_returns_only_the_merged_answer(monkeypatch) -> None:
    # distill returns ONLY the distilled block; the individual proposer answers
    # are intermediates and must not appear on stdout (they heartbeat to stderr).
    _install_all(monkeypatch)
    monkeypatch.setattr(
        cli, "stream", _fake_stream(_ok("claude", "A"), _ok("codex", "B"))
    )

    async def fake_run_provider(
        provider, prompt, timeout, model=None, yolo=False, effort=None
    ):
        return _ok("claude", "merged answer")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["distill", "-p", "claude", "-p", "codex", "hi"])
    assert result.exit_code == 0
    # The merged block is present...
    assert "synthesis · via claude" in result.stdout
    assert "merged answer" in result.stdout
    # ...and the proposer answer blocks are NOT on stdout.
    assert "claude (m) ·" not in result.stdout
    assert "codex (m) ·" not in result.stdout
    # Proposers still heartbeat to stderr so the wait isn't silent.
    assert "claude responded" in result.stderr and "codex responded" in result.stderr


def test_distill_json_emits_only_synthesis(monkeypatch) -> None:
    # distill --json returns only the synthesis record, never per-agent responses.
    _install_all(monkeypatch)
    monkeypatch.setattr(
        cli, "stream", _fake_stream(_ok("claude", "A"), _ok("codex", "B"))
    )

    async def fake_run_provider(
        provider, prompt, timeout, model=None, yolo=False, effort=None
    ):
        return _ok("claude", "merged")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(
        cli.app, ["distill", "-p", "claude", "-p", "codex", "--json", "hi"]
    )
    assert result.exit_code == 0
    assert '"type": "synthesis"' in result.stdout
    assert '"type": "response"' not in result.stdout


def test_distill_aggregator_input_is_blind_and_shuffled(monkeypatch) -> None:
    # The aggregator must receive anonymized + shuffled answers (item 002, no toggle).
    _install_all(monkeypatch)
    monkeypatch.setattr(
        cli, "stream", _fake_stream(_ok("claude", "ALPHA"), _ok("codex", "BETA"))
    )
    captured: dict = {}

    async def fake_run_provider(
        provider, prompt, timeout, model=None, yolo=False, effort=None
    ):
        captured["prompt"] = prompt
        return _ok("claude", "merged")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["distill", "-p", "claude", "-p", "codex", "hi"])
    assert result.exit_code == 0
    # Anonymized: real provider names never reach the aggregator prompt.
    assert "claude" not in captured["prompt"] and "codex" not in captured["prompt"]
    assert (
        "### Response A" in captured["prompt"]
        and "### Response B" in captured["prompt"]
    )
    assert "ALPHA" in captured["prompt"] and "BETA" in captured["prompt"]


def test_distill_synthesizer_selection(monkeypatch) -> None:
    # -s/--synthesizer pins who distills; the chosen provider runs the merge.
    _install_all(monkeypatch)
    monkeypatch.setattr(
        cli, "stream", _fake_stream(_ok("claude", "A"), _ok("codex", "B"))
    )
    captured: dict = {}

    async def fake_run_provider(
        provider, prompt, timeout, model=None, yolo=False, effort=None
    ):
        captured["provider"] = provider.name
        return _ok(provider.name, "merged")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(
        cli.app, ["distill", "-p", "claude", "-p", "codex", "-s", "codex", "hi"]
    )
    assert result.exit_code == 0
    assert captured["provider"] == "codex"
    assert "synthesis · via codex" in result.stdout


def test_distill_rejects_unselected_synthesizer_without_spawning(monkeypatch) -> None:
    _install_all(monkeypatch)
    monkeypatch.setattr(
        cli, "stream", _fake_stream(_ok("claude", "A"), _ok("codex", "B"))
    )

    async def fake_run_provider(
        provider, prompt, timeout, model=None, yolo=False, effort=None
    ):
        raise AssertionError("unselected synthesizer must not spawn")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    result = CliRunner().invoke(
        cli.app,
        [
            "distill",
            "-p",
            "claude",
            "-p",
            "codex",
            "-s",
            "agy",
            "hi",
        ],
    )
    assert result.exit_code == 1
    assert "agy" in result.stderr
    assert "not among the selected providers" in result.stderr
    assert "synthesis" not in result.stdout


def test_distill_skips_with_fewer_than_two_successes(monkeypatch) -> None:
    # With a single successful proposer there is nothing to distill.
    _install_all(monkeypatch)
    monkeypatch.setattr(cli, "stream", _fake_stream(_ok("claude", "A")))

    async def fake_run_provider(
        provider, prompt, timeout, model=None, yolo=False, effort=None
    ):
        raise AssertionError("aggregator must not run with <2 successes")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["distill", "-p", "claude", "hi"])
    assert result.exit_code == 1
    assert "Distill skipped" in result.stderr
    assert "synthesis" not in result.stdout


def test_distill_failed_synthesizer_exits_nonzero(monkeypatch) -> None:
    _install_all(monkeypatch)
    monkeypatch.setattr(
        cli, "stream", _fake_stream(_ok("claude", "A"), _ok("codex", "B"))
    )

    async def fake_run_provider(
        provider, prompt, timeout, model=None, yolo=False, effort=None
    ):
        return _failed(provider.name, "synthesis failed")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    result = CliRunner().invoke(
        cli.app, ["distill", "-p", "claude", "-p", "codex", "hi"]
    )
    assert result.exit_code == 1
    assert "synthesis · via claude" in result.stdout
    assert "synthesis failed" in result.stdout


def test_distill_aggregator_is_read_only_by_default(monkeypatch) -> None:
    # Regression (009 follow-up): the distill aggregator run must be read-only
    # unless --yolo is passed. yolo defaults to False on the aggregator call.
    _install_all(monkeypatch)
    monkeypatch.setattr(
        cli, "stream", _fake_stream(_ok("claude", "A"), _ok("codex", "B"))
    )
    captured: dict = {}

    async def fake_run_provider(
        provider, prompt, timeout, model=None, yolo=False, effort=None
    ):
        captured["yolo"] = yolo
        return _ok("claude", "merged")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["distill", "-p", "claude", "-p", "codex", "hi"])
    assert result.exit_code == 0
    assert captured["yolo"] is False


def test_distill_aggregator_yolo_propagates(monkeypatch) -> None:
    _install_all(monkeypatch)
    monkeypatch.setattr(
        cli, "stream", _fake_stream(_ok("claude", "A"), _ok("codex", "B"))
    )
    captured: dict = {}

    async def fake_run_provider(
        provider, prompt, timeout, model=None, yolo=False, effort=None
    ):
        captured["yolo"] = yolo
        return _ok("claude", "merged")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(
        cli.app, ["distill", "-p", "claude", "-p", "codex", "--yolo", "hi"]
    )
    assert result.exit_code == 0
    assert captured["yolo"] is True


def test_distill_emits_agy_partial_protection_note(monkeypatch) -> None:
    # Shared resolver: distill surfaces agy's honest note exactly like ask.
    _install_all(monkeypatch)
    monkeypatch.setattr(cli, "stream", _fake_stream(_ok("agy", "OK")))
    runner = CliRunner()
    result = runner.invoke(cli.app, ["distill", "-p", "agy", "hi"])
    assert result.exit_code == 1
    assert (
        "agy is shell-sandboxed but can still edit files (no true read-only mode)"
        in result.stderr
    )


def test_synthesizer_prompt_keeps_load_bearing_clauses() -> None:
    # The aggregator prompt must keep the MoA load-bearing instructions.
    text = SYNTHESIZER_PROMPT
    assert "biased or incorrect" in text
    assert "not simply replicate" in text
    assert "refined, accurate, comprehensive" in text
    # Adapted away from "open-source models".
    assert "AI coding assistants" in text
    assert "open-source" not in text


# --- debate: roles ----------------------------------------------------------


def _provs(*names: str) -> list[Provider]:
    return [PROVIDERS[n] for n in names]


def test_debate_default_roles_two_debaters_moderator_first() -> None:
    # With only 2 selected agents, the top-priority one moderates its own debate.
    for moderator in (None, "auto"):
        debaters, mod = assign_debate_roles(_provs("claude", "codex"), moderator)
        assert [p.name for p in debaters] == ["claude", "codex"]
        assert mod.name == "claude"
        assert mod.name in [p.name for p in debaters]  # the moderator may debate


def test_debate_default_moderator_is_neutral_when_third_available() -> None:
    # With >=3 selected agents, the default moderator is the 3rd (neutral - not a
    # debater), so the verdict isn't written by someone who also argued for a side.
    for moderator in (None, "auto"):
        debaters, mod = assign_debate_roles(
            _provs("claude", "codex", "agy"), moderator
        )
        assert [p.name for p in debaters] == ["claude", "codex"]
        assert mod.name == "agy"
        assert mod.name not in [p.name for p in debaters]


def test_debate_moderator_pinned_to_nondebater() -> None:
    # Pin a 3rd selected agent as moderator for a neutral (non-debating) overseer.
    debaters, mod = assign_debate_roles(_provs("claude", "codex", "agy"), "agy")
    assert [p.name for p in debaters] == ["claude", "codex"]
    assert mod.name == "agy"
    assert mod.name not in [p.name for p in debaters]


def test_debate_needs_at_least_two_providers() -> None:
    # Two is enough now (the moderator may also be a debater); one is not.
    debaters, mod = assign_debate_roles(_provs("claude", "codex"), None)
    assert [p.name for p in debaters] == ["claude", "codex"]
    assert mod.name == "claude"
    with pytest.raises(ValueError):
        assign_debate_roles(_provs("claude"), None)


def test_debate_moderator_must_be_selected() -> None:
    # A moderator that isn't among the selected providers is an error, not a silent add.
    with pytest.raises(ValueError):
        assign_debate_roles(_provs("claude", "codex", "agy"), "opencode")


# --- debate: rounds clamp ---------------------------------------------------


def test_clamp_rounds_in_range() -> None:
    assert clamp_rounds(2) == (2, None)
    assert clamp_rounds(1)[0] == 1
    assert clamp_rounds(4)[0] == 4


def test_clamp_rounds_over_cap_warns() -> None:
    rounds, warning = clamp_rounds(9)
    assert rounds == 4
    assert warning is not None and "capped" in warning


def test_clamp_rounds_below_one_warns() -> None:
    rounds, warning = clamp_rounds(0)
    assert rounds == 1
    assert warning is not None


# --- debate: orchestration & safety -----------------------------------------


def test_debate_help_shows_rounds_moderator_and_shared_options() -> None:
    out = _help(["debate"])
    for opt in _SHARED_OPTS:
        assert opt in out
    # Verb-specific options live only on debate.
    assert "--rounds" in out
    assert "--moderator" in out
    assert "--judge" not in out  # renamed


def test_debate_defaults_to_two_agents(monkeypatch) -> None:
    # debate's built-in default selection is 2 (not the usual 3): it only needs
    # two debaters, since the moderator may be one of them.
    _install_all(monkeypatch)

    async def fake_run_provider(
        provider, prompt, timeout, model=None, yolo=False, effort=None
    ):
        return _ok(provider.name, "answer")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["debate", "-r", "1", "hi"])
    assert result.exit_code == 0
    # Top 2 only (claude, codex) debate; agy/opencode are not selected.
    assert "round 1 · claude" in result.stdout
    assert "round 1 · codex" in result.stdout
    assert "· agy" not in result.stdout
    assert "· opencode" not in result.stdout


def test_debate_runs_rounds_then_verdict(monkeypatch) -> None:
    # Debaters run sequentially across rounds; a pinned neutral moderator (agy)
    # checks convergence between rounds and writes the verdict last.
    _install_all(monkeypatch)
    calls: list[str] = []

    async def fake_run_provider(
        provider, prompt, timeout, model=None, yolo=False, effort=None
    ):
        calls.append(provider.name)
        if "Your decision" in prompt:  # moderator convergence check
            return _ok(provider.name, "CONTINUE")
        return _ok(provider.name, f"{provider.name} answer")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "debate",
            "-p",
            "claude",
            "-p",
            "codex",
            "-p",
            "agy",
            "--moderator",
            "agy",
            "-r",
            "2",
            "hi",
        ],
    )
    assert result.exit_code == 0
    # 2 debaters x 2 rounds = 4 debater turns; agy moderates (1 check + 1 verdict).
    assert [c for c in calls if c in ("claude", "codex")] == [
        "claude",
        "codex",
        "claude",
        "codex",
    ]
    assert "round 1 · claude" in result.stdout
    assert "round 2 · codex" in result.stdout
    assert "verdict · moderator agy" in result.stdout
    # The verdict comes last.
    assert result.stdout.index("verdict") > result.stdout.index("round 2")


def test_debate_default_moderator_is_a_debater(monkeypatch) -> None:
    # With just 2 agents the default moderator is the first one (also a debater).
    _install_all(monkeypatch)

    async def fake_run_provider(
        provider, prompt, timeout, model=None, yolo=False, effort=None
    ):
        return _ok(provider.name, "answer")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(
        cli.app, ["debate", "-p", "claude", "-p", "codex", "-r", "1", "hi"]
    )
    assert result.exit_code == 0
    assert "verdict · moderator claude" in result.stdout


def test_debate_failed_verdict_exits_nonzero(monkeypatch) -> None:
    _install_all(monkeypatch)
    calls = {"count": 0}

    async def fake_run_provider(
        provider, prompt, timeout, model=None, yolo=False, effort=None
    ):
        calls["count"] += 1
        if calls["count"] == 3:
            return _failed(provider.name, "verdict failed")
        return _ok(provider.name, "answer")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    result = CliRunner().invoke(
        cli.app, ["debate", "-p", "claude", "-p", "codex", "-r", "1", "hi"]
    )
    assert result.exit_code == 1
    assert "verdict · moderator claude" in result.stdout
    assert "verdict failed" in result.stdout


def test_debate_debaters_and_moderator_read_only_by_default(monkeypatch) -> None:
    # Default mode: every debater turn AND the moderator run read-only (yolo=False).
    _install_all(monkeypatch)
    yolos: list[bool] = []

    async def fake_run_provider(
        provider, prompt, timeout, model=None, yolo=False, effort=None
    ):
        yolos.append(yolo)
        return _ok(provider.name, "answer")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(
        cli.app, ["debate", "-p", "claude", "-p", "codex", "-p", "agy", "hi"]
    )
    assert result.exit_code == 0
    assert yolos and all(y is False for y in yolos)


class _FakeProc:
    """Minimal stand-in for asyncio.subprocess.Process that returns a fixed answer."""

    returncode = 0
    pid = 0

    async def communicate(self):
        return (b"ok answer", b"")


def test_debate_inherits_readonly_argv(monkeypatch) -> None:
    # End-to-end through the real run_provider: every spawned debater AND the
    # moderator argv must carry the read-only permission flags by default.
    _install_all(monkeypatch)
    argvs: list[list[str]] = []

    async def fake_exec(*args, **kwargs):
        argvs.append(list(args))
        return _FakeProc()

    monkeypatch.setattr(execution.asyncio, "create_subprocess_exec", fake_exec)
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "debate",
            "-p",
            "claude",
            "-p",
            "codex",
            "-p",
            "agy",
            "--moderator",
            "agy",
            "-r",
            "1",
            "hi",
        ],
    )
    assert result.exit_code == 0
    # claude (debater) carries read-only flags.
    claude_argvs = [a for a in argvs if a and a[0] == "claude"]
    assert claude_argvs and all(
        "--permission-mode" in a and a[a.index("--permission-mode") + 1] == "default"
        for a in claude_argvs
    )
    # codex (debater) carries read-only flags.
    codex_argvs = [a for a in argvs if a and a[0] == "codex"]
    assert codex_argvs and all("read-only" in a for a in codex_argvs)
    # The moderator (agy) ran read-only too: its argv has --sandbox, not full access.
    agy_argvs = [a for a in argvs if a and a[0] == "agy"]
    assert agy_argvs and all("--sandbox" in a for a in agy_argvs)


def test_debate_yolo_propagates(monkeypatch) -> None:
    # --yolo flows to every debater and the moderator.
    _install_all(monkeypatch)
    yolos: list[bool] = []

    async def fake_run_provider(
        provider, prompt, timeout, model=None, yolo=False, effort=None
    ):
        yolos.append(yolo)
        return _ok(provider.name, "answer")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(
        cli.app, ["debate", "-p", "claude", "-p", "codex", "-p", "agy", "--yolo", "hi"]
    )
    assert result.exit_code == 0
    assert yolos and all(y is True for y in yolos)


def test_debate_round_cap_clamped_in_run(monkeypatch) -> None:
    # -r above the hard cap is clamped (with a warning) before the loop runs.
    _install_all(monkeypatch)
    calls: list[str] = []

    async def fake_run_provider(
        provider, prompt, timeout, model=None, yolo=False, effort=None
    ):
        calls.append(provider.name)
        if "Your decision" in prompt:  # moderator: never converge, run all rounds
            return _ok(provider.name, "CONTINUE")
        return _ok(provider.name, "answer")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "debate",
            "-p",
            "claude",
            "-p",
            "codex",
            "-p",
            "agy",
            "--moderator",
            "agy",
            "-r",
            "9",
            "hi",
        ],
    )
    assert result.exit_code == 0
    assert "capped" in result.stderr
    # 2 debaters x 4 (capped) rounds = 8 debater turns, not 18.
    debater_calls = [c for c in calls if c in ("claude", "codex")]
    assert len(debater_calls) == 8


def test_debate_too_few_providers_exits(monkeypatch) -> None:
    # Only 1 provider installed: can't field 2 debaters, clean exit (no silent degrade).
    installed = {"claude"}
    monkeypatch.setattr(
        providers.shutil, "which", lambda exe: exe if exe in installed else None
    )

    async def fake_run_provider(
        provider, prompt, timeout, model=None, yolo=False, effort=None
    ):
        raise AssertionError("debate must not run with too few providers")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["debate", "-n", "3", "hi"])
    assert result.exit_code == 1
    assert "at least 2 providers" in result.stderr


def test_debate_moderator_converges_early(monkeypatch) -> None:
    # When the moderator replies DONE after a round, the debate stops before the
    # cap even though -r 3 was requested.
    _install_all(monkeypatch)
    calls: list[str] = []

    async def fake_run_provider(
        provider, prompt, timeout, model=None, yolo=False, effort=None
    ):
        calls.append(provider.name)
        if "Your decision" in prompt:  # moderator converges immediately
            return _ok(provider.name, "DONE")
        return _ok(provider.name, "answer")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "debate",
            "-p",
            "claude",
            "-p",
            "codex",
            "-p",
            "agy",
            "--moderator",
            "agy",
            "-r",
            "3",
            "hi",
        ],
    )
    assert result.exit_code == 0
    assert "converged" in result.stderr
    # Round 1 (claude, codex) only; the moderator stops it, so rounds 2-3 never run.
    assert [c for c in calls if c in ("claude", "codex")] == ["claude", "codex"]
    assert "verdict · moderator agy" in result.stdout


# --- debate: verdict prompt is anonymized + shuffled (moderator-blindness) --


def test_build_verdict_prompt_anonymizes_transcript() -> None:
    # The moderator must never see provider/model attribution: the structure
    # relabels each turn "Participant N" with no provider/model name attached, so a
    # brand can't leak via the labels - this matters even when the moderator was a
    # debater. Answer bodies are neutral here so a name could only appear in a label.
    transcript = [
        RunResult("claude", "opus", "ok", "The answer is four.", "", 1.0, 0),
        RunResult(
            "codex", "gpt-5.5", "ok", "Four, after carrying the one.", "", 1.0, 0
        ),
    ]
    prompt, label_map = build_verdict_prompt("Q?", transcript, rng=random.Random(0))
    for brand in ("claude", "codex", "opus", "gpt-5.5"):
        assert brand not in prompt
    assert "Participant 1" in prompt and "Participant 2" in prompt
    # The label_map still maps the (anonymized) labels back to real providers.
    assert set(label_map.values()) == {"claude", "codex"}
    assert set(label_map) == {"Participant 1", "Participant 2"}


def test_build_verdict_prompt_shuffles_with_seeded_rng() -> None:
    # Order is shuffled (a seeded RNG makes this deterministic to assert). With
    # this seed the two participants come out in reversed provider order.
    transcript = [
        RunResult("claude", "opus", "ok", "alpha", "", 1.0, 0),
        RunResult("codex", "gpt-5.5", "ok", "beta", "", 1.0, 0),
    ]
    _, label_map = build_verdict_prompt("Q?", transcript, rng=random.Random(1))
    # Seed 1 reverses the pair, so Participant 1 is the second provider (codex).
    assert label_map["Participant 1"] == "codex"
    assert label_map["Participant 2"] == "claude"


def test_build_verdict_prompt_ignores_failed_turns() -> None:
    # A failed/errored turn never reaches the moderator's verdict transcript.
    transcript = [
        RunResult("claude", "opus", "ok", "good answer", "", 1.0, 0),
        RunResult("codex", "gpt-5.5", "timeout", "", "boom", 1.0, None),
    ]
    prompt, label_map = build_verdict_prompt("Q?", transcript, rng=random.Random(0))
    assert list(label_map) == ["Participant 1"]
    assert label_map == {"Participant 1": "claude"}
    assert "boom" not in prompt


# --- debate: turn prompt (cold round-1 vs adversarial later turns) ----------


def test_build_debate_turn_prompt_round1_first_turn_is_cold() -> None:
    # Round 1, first debater: no prior answers, so no adversarial critique of another
    # participant's answer - but the opening turn carries a stance instruction so it
    # isn't a contentless one-word reply.
    prompt = build_debate_turn_prompt("What is 2+2?", prior=[])
    assert "What is 2+2?" in prompt
    assert ADVERSARIAL_INSTRUCTION not in prompt
    assert "The other participant's latest answer" not in prompt
    assert DEBATER_OPENING_INSTRUCTION in prompt


def test_build_debate_turn_prompt_later_turn_is_adversarial() -> None:
    # A later turn sees the prior answer AND the adversarial-stance instruction.
    prior = [("the other participant", "Their prior answer is 5.")]
    prompt = build_debate_turn_prompt("What is 2+2?", prior=prior)
    assert "Their prior answer is 5." in prompt
    assert ADVERSARIAL_INSTRUCTION in prompt
    assert "the other participant" in prompt


# --- debate: moderator convergence prompt -----------------------------------


def test_build_convergence_prompt_anonymizes_and_asks_for_decision() -> None:
    # The convergence check shows the debaters' latest answers anonymized and asks
    # for a one-word DONE/CONTINUE decision.
    latest = [
        RunResult("claude", "opus", "ok", "Alpha argument.", "", 1.0, 0),
        RunResult("codex", "gpt-5.5", "ok", "Beta argument.", "", 1.0, 0),
    ]
    prompt = build_convergence_prompt("Q?", latest)
    for brand in ("claude", "codex", "opus", "gpt-5.5"):
        assert brand not in prompt
    assert "Participant 1" in prompt and "Participant 2" in prompt
    assert "Alpha argument." in prompt and "Beta argument." in prompt
    assert "DONE" in prompt and "CONTINUE" in prompt


def test_debate_moderator_continue_runs_all_rounds(monkeypatch) -> None:
    # When the moderator keeps replying CONTINUE, the debate runs the full cap and
    # the moderator is consulted after each non-final round.
    _install_all(monkeypatch)
    checks = {"n": 0}

    async def fake_run_provider(
        provider, prompt, timeout, model=None, yolo=False, effort=None
    ):
        if "Your decision" in prompt:
            checks["n"] += 1
            return _ok(provider.name, "CONTINUE")
        return _ok(provider.name, "answer")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "debate",
            "-p",
            "claude",
            "-p",
            "codex",
            "-p",
            "agy",
            "--moderator",
            "agy",
            "-r",
            "3",
            "hi",
        ],
    )
    assert result.exit_code == 0
    # 3 rounds -> a check after rounds 1 and 2 (never after the final round 3).
    assert checks["n"] == 2
    assert "converged" not in result.stderr

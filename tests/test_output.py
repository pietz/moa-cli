import random

import pytest
from typer.testing import CliRunner

from moa_cli import cli, providers
from moa_cli.execution import RunResult
from moa_cli.output import render_block, render_synthesis_block, result_record
from moa_cli.workflows import build_synthesis_prompt, choose_synthesizer


# --- synthesis --------------------------------------------------------------


def _ok(provider: str, text: str) -> RunResult:
    return RunResult(provider, "m", "ok", text, "", 1.0, 0)


def test_choose_synthesizer_modes() -> None:
    assert choose_synthesizer("auto", ["claude", "codex"]) == "claude"
    assert choose_synthesizer("first", ["codex", "claude"]) == "codex"
    assert choose_synthesizer("random", ["agy"], rng=random.Random(0)) == "agy"
    assert choose_synthesizer("codex", ["claude", "codex"]) == "codex"


def test_choose_synthesizer_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        choose_synthesizer("nope", ["claude"])


def test_choose_synthesizer_rejects_unselected_provider() -> None:
    with pytest.raises(ValueError, match="agy.*not among the selected providers"):
        choose_synthesizer("agy", ["claude", "codex"])


def test_build_synthesis_prompt_named() -> None:
    prompt, label_map = build_synthesis_prompt(
        "Q?", [_ok("claude", "A"), _ok("codex", "B")], blind=False
    )
    assert "### claude" in prompt and "### codex" in prompt and "Q?" in prompt
    assert label_map == {"claude": "claude", "codex": "codex"}


def test_build_synthesis_prompt_blind_hides_names() -> None:
    prompt, label_map = build_synthesis_prompt(
        "Q?", [_ok("claude", "A"), _ok("codex", "B")], blind=True, rng=random.Random(0)
    )
    assert "claude" not in prompt and "codex" not in prompt
    assert "### Response A" in prompt and "### Response B" in prompt
    assert set(label_map.values()) == {"claude", "codex"}


def test_build_synthesis_prompt_ignores_failed() -> None:
    results = [
        _ok("claude", "good"),
        RunResult("codex", "m", "timeout", "", "boom", 1.0, None),
    ]
    prompt, label_map = build_synthesis_prompt("Q?", results, blind=False)
    assert "### codex" not in prompt
    assert label_map == {"claude": "claude"}


# --- render -----------------------------------------------------------------


def test_render_block_ok_terminal() -> None:
    # In a terminal (plain=False): a centered box-drawing rule, no markdown heading.
    output = render_block(_ok("claude", "Claude says yes."), plain=False)
    assert "─── claude (m) · OK · 1.0s ───" in output
    assert "## " not in output
    assert "Claude says yes." in output


def test_render_block_ok_piped_is_plain() -> None:
    # Piped (plain=True): a plain `## label` heading, no box-drawing noise.
    output = render_block(_ok("claude", "Claude says yes."), plain=True)
    assert "## claude (m) · OK · 1.0s" in output
    assert "─" not in output
    assert "Claude says yes." in output


def test_render_block_omits_model_when_empty() -> None:
    result = RunResult("opencode", "", "ok", "Hi.", "", 2.0, 0)
    assert "opencode · OK" in render_block(result, plain=True)


def test_render_block_failure_detail() -> None:
    result = RunResult("agy", "g", "timeout", "", "Timed out after 1s.", 1.0, None)
    output = render_block(result, plain=True)
    assert "agy (g) · TIMEOUT" in output
    assert "Timed out after 1s." in output


def test_render_block_blank_line_separation() -> None:
    # One leading blank line before the header, body immediately after (no gap).
    terminal = render_block(_ok("claude", "hi"), plain=False)
    assert terminal.startswith("\n─")
    assert not terminal.startswith("\n\n")
    assert "\n─" in terminal and "─\nhi" in terminal  # body right under the rule
    piped = render_block(_ok("claude", "hi"), plain=True)
    assert piped.startswith("\n## ")
    assert piped.startswith("\n## claude (m) · OK · 1.0s\nhi")  # no blank after heading


def test_render_synthesis_block_no_mode_tag() -> None:
    output = render_synthesis_block(
        _ok("synthesis", "merged"), synthesizer="codex", plain=True
    )
    assert "synthesis · via codex · OK" in output
    assert "(blind)" not in output and "(named)" not in output


def test_result_record_shape() -> None:
    record = result_record(_ok("claude", "Claude says yes."))
    assert record["type"] == "response"
    assert record["provider"] == "claude"
    assert record["text"] == "Claude says yes."


# --- doctor -----------------------------------------------------------------


def test_doctor_shows_default_models(monkeypatch) -> None:
    # doctor lists each provider's default model, not its executable.
    installed = {"claude", "codex", "agy", "opencode"}
    monkeypatch.setattr(
        providers.shutil, "which", lambda exe: exe if exe in installed else None
    )
    runner = CliRunner()
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0
    assert "claude (opus)" in result.stdout
    assert "codex (gpt-5.5)" in result.stdout
    assert "opencode (configured default)" in result.stdout
    # agy shows its model and the partial-sandbox marker (shell only; still edits).
    assert "agy (Gemini 3.5 Flash (High))" in result.stdout
    assert "partial sandbox - shell only; can still edit files" in result.stdout

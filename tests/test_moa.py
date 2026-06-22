import asyncio
import random

import pytest

from moa_cli import cli
from moa_cli.cli import (
    PROVIDERS,
    Provider,
    RunResult,
    build_synthesis_prompt,
    choose_synthesizer,
    render_block,
    render_synthesis_block,
    result_record,
    run_provider,
    select_for_run,
)


# --- providers --------------------------------------------------------------


def test_claude_env_unsets_claudecode(monkeypatch) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    env = PROVIDERS["claude"].env()
    assert "CLAUDECODE" not in env
    assert env["NO_COLOR"] == "1"


def test_codex_command_uses_output_file_and_skip_git() -> None:
    cmd = PROVIDERS["codex"].build("hello", "gpt-5.5", "/tmp/out.txt")
    assert cmd[:4] == ["codex", "exec", "-m", "gpt-5.5"]
    assert "--skip-git-repo-check" in cmd
    assert cmd[cmd.index("-o") + 1] == "/tmp/out.txt"
    assert cmd[-1] == "hello"


def test_antigravity_command_pins_gemini_model() -> None:
    # Regression: agy must get an explicit --model or it defaults to Gemini Flash.
    cmd = PROVIDERS["antigravity"].build("hi", "Gemini 3.1 Pro (High)", None)
    assert cmd == ["agy", "--model", "Gemini 3.1 Pro (High)", "-p", "hi"]


def test_select_for_run_takes_first_n_installed(monkeypatch) -> None:
    installed = {"claude", "codex", "gemini", "agy"}
    monkeypatch.setattr(cli.shutil, "which", lambda exe: exe if exe in installed else None)
    chosen, skipped = select_for_run(2, None)
    assert [p.name for p in chosen] == ["claude", "codex"]
    assert skipped == []


def test_select_for_run_skips_uninstalled_explicit(monkeypatch) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda exe: exe if exe == "claude" else None)
    chosen, skipped = select_for_run(3, ("claude", "gemini"))
    assert [p.name for p in chosen] == ["claude"]
    assert skipped == ["gemini"]


def test_select_for_run_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        select_for_run(3, ("claude", "nope"))


# --- runner -----------------------------------------------------------------


def _slow_provider(sleep_seconds: int) -> Provider:
    return Provider(
        name="slow",
        executable="uv",
        default_model="test",
        build=lambda _p, _m, _o: ["uv", "run", "python", "-c", f"import time; time.sleep({sleep_seconds})"],
    )


def test_run_provider_times_out() -> None:
    result = asyncio.run(run_provider(_slow_provider(5), "hello", timeout=0.1))
    assert result.status == "timeout"
    assert result.returncode is None


def test_run_provider_missing_executable() -> None:
    provider = Provider("ghost", "definitely-not-a-real-binary", "x", lambda _p, _m, _o: ["definitely-not-a-real-binary"])
    result = asyncio.run(run_provider(provider, "hello", timeout=5))
    assert result.status == "missing"


def test_run_provider_passes_devnull_stdin(monkeypatch) -> None:
    # Regression for the hang bug: codex/agy block forever on an inherited TTY
    # stdin, so every spawn must explicitly use DEVNULL.
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured.update(kwargs)
        raise FileNotFoundError  # bail out early; we only care about kwargs

    monkeypatch.setattr(cli.asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(run_provider(PROVIDERS["claude"], "hi", timeout=5))
    assert captured["stdin"] == asyncio.subprocess.DEVNULL


# --- synthesis --------------------------------------------------------------


def _ok(provider: str, text: str) -> RunResult:
    return RunResult(provider, "m", "ok", text, "", 1.0, 0)


def test_choose_synthesizer_modes() -> None:
    assert choose_synthesizer("auto", ["claude", "codex"]) == "claude"
    assert choose_synthesizer("first", ["codex", "claude"]) == "codex"
    assert choose_synthesizer("random", ["gemini"], rng=random.Random(0)) == "gemini"
    assert choose_synthesizer("codex", ["claude", "codex"]) == "codex"


def test_choose_synthesizer_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        choose_synthesizer("nope", ["claude"])


def test_build_synthesis_prompt_named() -> None:
    prompt, label_map = build_synthesis_prompt("Q?", [_ok("claude", "A"), _ok("codex", "B")], blind=False)
    assert "### claude" in prompt and "### codex" in prompt and "Q?" in prompt
    assert label_map == {"claude": "claude", "codex": "codex"}


def test_build_synthesis_prompt_blind_hides_names() -> None:
    prompt, label_map = build_synthesis_prompt("Q?", [_ok("claude", "A"), _ok("codex", "B")], blind=True, rng=random.Random(0))
    assert "claude" not in prompt and "codex" not in prompt
    assert "### Response A" in prompt and "### Response B" in prompt
    assert set(label_map.values()) == {"claude", "codex"}


def test_build_synthesis_prompt_ignores_failed() -> None:
    results = [_ok("claude", "good"), RunResult("codex", "m", "timeout", "", "boom", 1.0, None)]
    prompt, label_map = build_synthesis_prompt("Q?", results, blind=False)
    assert "### codex" not in prompt
    assert label_map == {"claude": "claude"}


# --- render -----------------------------------------------------------------


def test_render_block_ok() -> None:
    output = render_block(_ok("claude", "Claude says yes."))
    assert "## claude (m) - OK - 1.0s" in output
    assert "Claude says yes." in output


def test_render_block_omits_model_when_empty() -> None:
    result = RunResult("antigravity", "", "ok", "Hi.", "", 2.0, 0)
    assert "## antigravity - OK" in render_block(result)


def test_render_block_failure_detail() -> None:
    result = RunResult("gemini", "g", "timeout", "", "Timed out after 1s.", 1.0, None)
    output = render_block(result)
    assert "## gemini (g) - TIMEOUT" in output
    assert "Timed out after 1s." in output


def test_render_synthesis_block_marks_mode() -> None:
    output = render_synthesis_block(_ok("synthesis", "merged"), synthesizer="codex", blind=True)
    assert "## synthesis · via codex (blind) - OK" in output


def test_result_record_shape() -> None:
    record = result_record(_ok("claude", "Claude says yes."))
    assert record["type"] == "response"
    assert record["provider"] == "claude"
    assert record["text"] == "Claude says yes."

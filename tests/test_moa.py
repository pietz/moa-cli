import asyncio
import random

import pytest
from typer.testing import CliRunner

from moa_cli import cli
from moa_cli.cli import (
    PROVIDERS,
    Provider,
    RunResult,
    build_synthesis_prompt,
    choose_synthesizer,
    parse_model_overrides,
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
    cmd = PROVIDERS["codex"].build("hello", "gpt-5.5", "/tmp/out.txt", ())
    assert cmd[:4] == ["codex", "exec", "-m", "gpt-5.5"]
    assert "--skip-git-repo-check" in cmd
    assert cmd[cmd.index("-o") + 1] == "/tmp/out.txt"
    assert cmd[-1] == "hello"


def test_agy_command_pins_gemini_model() -> None:
    # Regression: agy must get an explicit --model or it defaults to Gemini Flash.
    cmd = PROVIDERS["agy"].build("hi", "Gemini 3.1 Pro (High)", None, ())
    assert cmd == ["agy", "--model", "Gemini 3.1 Pro (High)", "-p", "hi"]


def test_opencode_command_omits_model_when_empty() -> None:
    # opencode has no universal default; an empty model means "skip -m".
    assert PROVIDERS["opencode"].build("hi", "", None, ()) == ["opencode", "run", "hi"]
    cmd = PROVIDERS["opencode"].build("hi", "prov/model", None, ())
    assert cmd == ["opencode", "run", "-m", "prov/model", "hi"]


# --- permission map (read-only by default, --yolo opt-in) -------------------


def test_perm_args_readonly_vs_yolo_per_provider() -> None:
    # The permission argv is selected by mode, as data.
    assert PROVIDERS["claude"].perm_args(yolo=False) == ("--permission-mode", "plan")
    assert PROVIDERS["claude"].perm_args(yolo=True) == ("--permission-mode", "bypassPermissions")
    assert PROVIDERS["codex"].perm_args(yolo=False) == ("-s", "read-only")
    assert PROVIDERS["codex"].perm_args(yolo=True) == ("-s", "danger-full-access")
    assert PROVIDERS["opencode"].perm_args(yolo=False) == ("--agent", "plan")
    assert PROVIDERS["opencode"].perm_args(yolo=True) == ()
    # agy has no read-only mode; default run is unscoped (no perm args) and
    # under --yolo it gets full access (also no extra flag).
    assert PROVIDERS["agy"].readonly is None
    assert PROVIDERS["agy"].perm_args(yolo=False) == ()
    assert PROVIDERS["agy"].perm_args(yolo=True) == ()


def test_build_splices_readonly_before_prompt() -> None:
    # Read-only flags land before the positional prompt for each tool.
    p = PROVIDERS
    assert p["claude"].build("hi", "opus", None, ("--permission-mode", "plan")) == [
        "claude", "--model", "opus", "--permission-mode", "plan", "-p", "hi",
    ]
    codex_cmd = p["codex"].build("hi", "gpt-5.5", "/tmp/o.txt", ("-s", "read-only"))
    assert codex_cmd[codex_cmd.index("-s") + 1] == "read-only"
    assert codex_cmd.index("-s") < codex_cmd.index("-o")  # perm flags before output flag
    assert codex_cmd[-1] == "hi"
    assert p["opencode"].build("hi", "", None, ("--agent", "plan")) == [
        "opencode", "run", "--agent", "plan", "hi",
    ]
    assert p["agy"].build("hi", "g", None, ()) == ["agy", "--model", "g", "-p", "hi"]


def test_build_splices_yolo_flags() -> None:
    assert PROVIDERS["claude"].build("hi", "opus", None, ("--permission-mode", "bypassPermissions")) == [
        "claude", "--model", "opus", "--permission-mode", "bypassPermissions", "-p", "hi",
    ]
    codex_cmd = PROVIDERS["codex"].build("hi", "gpt-5.5", None, ("-s", "danger-full-access"))
    assert codex_cmd[codex_cmd.index("-s") + 1] == "danger-full-access"


def test_select_for_run_takes_first_n_installed(monkeypatch) -> None:
    installed = {"claude", "codex", "agy", "opencode"}
    monkeypatch.setattr(cli.shutil, "which", lambda exe: exe if exe in installed else None)
    # agy stays in the default panel at priority #3 (it runs unscoped).
    assert [p.name for p in select_for_run(2, None)[0]] == ["claude", "codex"]
    assert [p.name for p in select_for_run(3, None)[0]] == ["claude", "codex", "agy"]
    assert [p.name for p in select_for_run(4, None)[0]] == [
        "claude", "codex", "agy", "opencode",
    ]


def test_select_for_run_pins_agy_without_yolo(monkeypatch) -> None:
    # agy has no read-only mode but is still selectable - it runs unscoped, no error.
    installed = {"claude", "codex", "agy", "opencode"}
    monkeypatch.setattr(cli.shutil, "which", lambda exe: exe if exe in installed else None)
    chosen, skipped = select_for_run(3, ("agy",))
    assert [p.name for p in chosen] == ["agy"]
    assert skipped == []


def test_select_for_run_skips_uninstalled_explicit(monkeypatch) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda exe: exe if exe == "claude" else None)
    chosen, skipped = select_for_run(3, ("claude", "opencode"))
    assert [p.name for p in chosen] == ["claude"]
    assert skipped == ["opencode"]


def test_select_for_run_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        select_for_run(3, ("claude", "nope"))


def test_select_for_run_excludes_before_taking_n(monkeypatch) -> None:
    installed = {"claude", "codex", "agy", "opencode"}
    monkeypatch.setattr(cli.shutil, "which", lambda exe: exe if exe in installed else None)
    chosen, skipped = select_for_run(3, None, exclude=("claude",))
    assert [p.name for p in chosen] == ["codex", "agy", "opencode"]
    assert skipped == []


def test_select_for_run_excludes_from_explicit(monkeypatch) -> None:
    installed = {"claude", "codex", "agy", "opencode"}
    monkeypatch.setattr(cli.shutil, "which", lambda exe: exe if exe in installed else None)
    chosen, _ = select_for_run(3, ("claude", "codex"), exclude=("claude",))
    assert [p.name for p in chosen] == ["codex"]


def test_select_for_run_rejects_unknown_exclude() -> None:
    with pytest.raises(ValueError):
        select_for_run(3, None, exclude=("nope",))


# --- runner -----------------------------------------------------------------


def _slow_provider(sleep_seconds: int) -> Provider:
    return Provider(
        name="slow",
        executable="uv",
        default_model="test",
        build=lambda _p, _m, _o, _perm: ["uv", "run", "python", "-c", f"import time; time.sleep({sleep_seconds})"],
    )


def test_run_provider_times_out() -> None:
    result = asyncio.run(run_provider(_slow_provider(5), "hello", timeout=0.1))
    assert result.status == "timeout"
    assert result.returncode is None


def test_run_provider_missing_executable() -> None:
    provider = Provider("ghost", "definitely-not-a-real-binary", "x", lambda _p, _m, _o, _perm: ["definitely-not-a-real-binary"])
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


# --- model overrides --------------------------------------------------------


def test_parse_model_overrides_applies_one_keeps_defaults() -> None:
    # Override only claude; other providers keep their PROVIDERS defaults.
    models = parse_model_overrides(["claude=sonnet"])
    assert models == {"claude": "sonnet"}
    assert models.get("claude") == "sonnet"
    assert models.get("codex") is None  # codex falls back to default_model


def test_parse_model_overrides_multiple_with_spaces() -> None:
    models = parse_model_overrides(["claude=sonnet", "agy=Gemini 3.1 Pro (Low)"])
    assert models == {"claude": "sonnet", "agy": "Gemini 3.1 Pro (Low)"}


def test_parse_model_overrides_none_is_empty() -> None:
    assert parse_model_overrides(None) == {}


def test_parse_model_overrides_rejects_missing_equals() -> None:
    with pytest.raises(cli.typer.BadParameter):
        parse_model_overrides(["claude"])


def test_parse_model_overrides_rejects_unknown_provider() -> None:
    with pytest.raises(cli.typer.BadParameter):
        parse_model_overrides(["nope=x"])


def test_run_provider_uses_override_model(monkeypatch) -> None:
    # The override model must reach the spawned argv, not provider.default_model.
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["argv"] = list(args)
        raise FileNotFoundError  # bail out early; we only care about argv

    monkeypatch.setattr(cli.asyncio, "create_subprocess_exec", fake_exec)
    result = asyncio.run(run_provider(PROVIDERS["claude"], "hi", timeout=5, model="sonnet"))
    # Default mode is read-only, so the plan permission flags are spliced in.
    assert captured["argv"] == ["claude", "--model", "sonnet", "--permission-mode", "plan", "-p", "hi"]
    assert result.model == "sonnet"


def test_run_provider_defaults_model_when_no_override(monkeypatch) -> None:
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["argv"] = list(args)
        raise FileNotFoundError

    monkeypatch.setattr(cli.asyncio, "create_subprocess_exec", fake_exec)
    result = asyncio.run(run_provider(PROVIDERS["claude"], "hi", timeout=5))
    assert captured["argv"] == ["claude", "--model", "opus", "--permission-mode", "plan", "-p", "hi"]
    assert result.model == "opus"


def test_run_provider_readonly_by_default_argv(monkeypatch) -> None:
    # Default run carries each sandboxable provider's read-only flag.
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["argv"] = list(args)
        raise FileNotFoundError

    monkeypatch.setattr(cli.asyncio, "create_subprocess_exec", fake_exec)

    asyncio.run(run_provider(PROVIDERS["claude"], "hi", timeout=5))
    assert "--permission-mode" in captured["argv"]
    assert captured["argv"][captured["argv"].index("--permission-mode") + 1] == "plan"

    asyncio.run(run_provider(PROVIDERS["codex"], "hi", timeout=5, model="gpt-5.5"))
    assert "-s" in captured["argv"]
    assert captured["argv"][captured["argv"].index("-s") + 1] == "read-only"

    asyncio.run(run_provider(PROVIDERS["opencode"], "hi", timeout=5, model="prov/model"))
    assert "--agent" in captured["argv"]
    assert captured["argv"][captured["argv"].index("--agent") + 1] == "plan"


def test_run_provider_agy_default_argv_has_no_readonly_flag(monkeypatch) -> None:
    # agy has no read-only mode, so its default argv runs unscoped (no perm flag).
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["argv"] = list(args)
        raise FileNotFoundError

    monkeypatch.setattr(cli.asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(run_provider(PROVIDERS["agy"], "hi", timeout=5, model="g"))
    assert captured["argv"] == ["agy", "--model", "g", "-p", "hi"]


def test_run_provider_yolo_argv(monkeypatch) -> None:
    # --yolo swaps in the full-access permission flags.
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["argv"] = list(args)
        raise FileNotFoundError

    monkeypatch.setattr(cli.asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(run_provider(PROVIDERS["claude"], "hi", timeout=5, yolo=True))
    assert captured["argv"] == [
        "claude", "--model", "opus", "--permission-mode", "bypassPermissions", "-p", "hi",
    ]
    asyncio.run(run_provider(PROVIDERS["codex"], "hi", timeout=5, yolo=True))
    assert captured["argv"][captured["argv"].index("-s") + 1] == "danger-full-access"


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
    result = RunResult("opencode", "", "ok", "Hi.", "", 2.0, 0)
    assert "## opencode - OK" in render_block(result)


def test_render_block_failure_detail() -> None:
    result = RunResult("agy", "g", "timeout", "", "Timed out after 1s.", 1.0, None)
    output = render_block(result)
    assert "## agy (g) - TIMEOUT" in output
    assert "Timed out after 1s." in output


def test_render_synthesis_block_no_mode_tag() -> None:
    output = render_synthesis_block(_ok("synthesis", "merged"), synthesizer="codex")
    assert "## synthesis · via codex - OK" in output
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
    monkeypatch.setattr(cli.shutil, "which", lambda exe: exe if exe in installed else None)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0
    assert "claude (opus)" in result.stdout
    assert "codex (gpt-5.5)" in result.stdout
    assert "opencode (configured default)" in result.stdout
    # agy shows its model and the no-read-only / unsandboxed marker.
    assert "agy (Gemini 3.1 Pro (High))" in result.stdout
    assert "no read-only mode (runs unsandboxed)" in result.stdout

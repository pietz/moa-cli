import asyncio
import random

import pytest
from typer.testing import CliRunner

from moa_cli import cli
from moa_cli.cli import (
    PROVIDERS,
    Provider,
    RunResult,
    assign_debate_roles,
    build_debate_turn_prompt,
    build_judge_prompt,
    build_synthesis_prompt,
    choose_synthesizer,
    clamp_rounds,
    load_config,
    parse_model_overrides,
    render_block,
    render_synthesis_block,
    result_record,
    run_provider,
    select_for_run,
    serialize_config,
)
from moa_cli.cli import (
    _signals_convergence as signals_convergence,
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
    # agy's --sandbox is only PARTIAL protection (shell vector; it can still edit
    # files), so the default run carries --sandbox plus an honest readonly_note,
    # and under --yolo it drops --sandbox for full access.
    assert PROVIDERS["agy"].readonly == ("--sandbox",)
    assert PROVIDERS["agy"].readonly_note is not None
    assert PROVIDERS["agy"].perm_args(yolo=False) == ("--sandbox",)
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


def test_run_provider_agy_default_argv_has_sandbox(monkeypatch) -> None:
    # agy's default argv includes --sandbox (partial: shell only - it can still
    # edit files). Under --yolo (below) --sandbox is dropped for full access.
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["argv"] = list(args)
        raise FileNotFoundError

    monkeypatch.setattr(cli.asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(run_provider(PROVIDERS["agy"], "hi", timeout=5, model="g"))
    assert captured["argv"] == ["agy", "--sandbox", "--model", "g", "-p", "hi"]

    asyncio.run(run_provider(PROVIDERS["agy"], "hi", timeout=5, model="g", yolo=True))
    assert captured["argv"] == ["agy", "--model", "g", "-p", "hi"]
    assert "--sandbox" not in captured["argv"]


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
    # agy shows its model and the partial-sandbox marker (shell only; still edits).
    assert "agy (Gemini 3.1 Pro (High))" in result.stdout
    assert "partial sandbox - shell only; can still edit files" in result.stdout


# --- ask selection note -----------------------------------------------------


def _fake_stream(*results: RunResult):
    async def stream(providers, prompt, timeout, models=None, yolo=False):
        for r in results:
            yield r

    return stream


def test_ask_emits_agy_partial_protection_note(monkeypatch) -> None:
    # When agy runs in the default (non-yolo) mode, the stderr selection note
    # must honestly state agy is shell-sandboxed but can still edit files.
    installed = {"claude", "codex", "agy", "opencode"}
    monkeypatch.setattr(cli.shutil, "which", lambda exe: exe if exe in installed else None)
    monkeypatch.setattr(cli, "stream", _fake_stream(_ok("agy", "OK")))
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ask", "-p", "agy", "hi"])
    assert result.exit_code == 0
    assert "agy is shell-sandboxed but can still edit files (no true read-only mode)" in result.stderr


def test_ask_omits_agy_note_under_yolo(monkeypatch) -> None:
    # Under --yolo agy drops --sandbox (full access), so no partial-protection note.
    installed = {"claude", "codex", "agy", "opencode"}
    monkeypatch.setattr(cli.shutil, "which", lambda exe: exe if exe in installed else None)
    monkeypatch.setattr(cli, "stream", _fake_stream(_ok("agy", "OK")))
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ask", "-p", "agy", "--yolo", "hi"])
    assert result.exit_code == 0
    assert "can still edit files" not in result.stderr


# --- verbs (ask / distill / doctor) -----------------------------------------


def _install_all(monkeypatch) -> None:
    installed = {"claude", "codex", "agy", "opencode"}
    monkeypatch.setattr(cli.shutil, "which", lambda exe: exe if exe in installed else None)


def test_subcommands_registered() -> None:
    # The CLI is a set of verbs now, not a single command.
    runner = CliRunner()
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for verb in ("ask", "distill", "debate", "doctor"):
        assert verb in result.stdout


def test_ask_has_no_synth_flags() -> None:
    # --synth and --synthesizer were removed from ask; verbs replace them.
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ask", "--help"])
    assert result.exit_code == 0
    assert "--synth" not in result.stdout
    assert "--synthesizer" not in result.stdout


def test_ask_help_shows_shared_options() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ask", "--help"])
    assert result.exit_code == 0
    for opt in ("--num", "--provider", "--exclude", "--model", "--timeout", "--file", "--json", "--yolo"):
        assert opt in result.stdout


def test_distill_help_shows_shared_options_and_synthesizer() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["distill", "--help"])
    assert result.exit_code == 0
    for opt in ("--num", "--provider", "--exclude", "--model", "--timeout", "--file", "--json", "--yolo"):
        assert opt in result.stdout
    # --synthesizer lives only on distill.
    assert "--synthesizer" in result.stdout


def test_ask_is_council_no_synthesis(monkeypatch) -> None:
    # ask fans out and prints each answer; it never emits a synthesis/distill block.
    _install_all(monkeypatch)
    monkeypatch.setattr(cli, "stream", _fake_stream(_ok("claude", "A"), _ok("codex", "B")))
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ask", "-p", "claude", "-p", "codex", "hi"])
    assert result.exit_code == 0
    assert "## claude" in result.stdout and "## codex" in result.stdout
    assert "synthesis" not in result.stdout


def test_distill_runs_council_then_merges(monkeypatch) -> None:
    # distill streams the proposer answers, then a single merged block last.
    _install_all(monkeypatch)
    monkeypatch.setattr(cli, "stream", _fake_stream(_ok("claude", "A"), _ok("codex", "B")))

    async def fake_run_provider(provider, prompt, timeout, model=None, yolo=False):
        return _ok("claude", "merged answer")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["distill", "-p", "claude", "-p", "codex", "hi"])
    assert result.exit_code == 0
    assert "## claude" in result.stdout and "## codex" in result.stdout
    assert "## synthesis · via claude" in result.stdout
    assert "merged answer" in result.stdout
    # The merged block comes after both proposer blocks.
    assert result.stdout.index("synthesis") > result.stdout.index("## codex")


def test_distill_aggregator_input_is_blind_and_shuffled(monkeypatch) -> None:
    # The aggregator must receive anonymized + shuffled answers (item 002, no toggle).
    _install_all(monkeypatch)
    monkeypatch.setattr(cli, "stream", _fake_stream(_ok("claude", "ALPHA"), _ok("codex", "BETA")))
    captured: dict = {}

    async def fake_run_provider(provider, prompt, timeout, model=None, yolo=False):
        captured["prompt"] = prompt
        return _ok("claude", "merged")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["distill", "-p", "claude", "-p", "codex", "hi"])
    assert result.exit_code == 0
    # Anonymized: real provider names never reach the aggregator prompt.
    assert "claude" not in captured["prompt"] and "codex" not in captured["prompt"]
    assert "### Response A" in captured["prompt"] and "### Response B" in captured["prompt"]
    assert "ALPHA" in captured["prompt"] and "BETA" in captured["prompt"]


def test_distill_synthesizer_selection(monkeypatch) -> None:
    # -s/--synthesizer pins who distills; the chosen provider runs the merge.
    _install_all(monkeypatch)
    monkeypatch.setattr(cli, "stream", _fake_stream(_ok("claude", "A"), _ok("codex", "B")))
    captured: dict = {}

    async def fake_run_provider(provider, prompt, timeout, model=None, yolo=False):
        captured["provider"] = provider.name
        return _ok(provider.name, "merged")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["distill", "-p", "claude", "-p", "codex", "-s", "codex", "hi"])
    assert result.exit_code == 0
    assert captured["provider"] == "codex"
    assert "## synthesis · via codex" in result.stdout


def test_distill_skips_with_fewer_than_two_successes(monkeypatch) -> None:
    # With a single successful proposer there is nothing to distill.
    _install_all(monkeypatch)
    monkeypatch.setattr(cli, "stream", _fake_stream(_ok("claude", "A")))

    async def fake_run_provider(provider, prompt, timeout, model=None, yolo=False):
        raise AssertionError("aggregator must not run with <2 successes")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["distill", "-p", "claude", "hi"])
    assert result.exit_code == 0
    assert "Distill skipped" in result.stderr
    assert "synthesis" not in result.stdout


def test_distill_aggregator_is_read_only_by_default(monkeypatch) -> None:
    # Regression (009 follow-up): the distill aggregator run must be read-only
    # unless --yolo is passed. yolo defaults to False on the aggregator call.
    _install_all(monkeypatch)
    monkeypatch.setattr(cli, "stream", _fake_stream(_ok("claude", "A"), _ok("codex", "B")))
    captured: dict = {}

    async def fake_run_provider(provider, prompt, timeout, model=None, yolo=False):
        captured["yolo"] = yolo
        return _ok("claude", "merged")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["distill", "-p", "claude", "-p", "codex", "hi"])
    assert result.exit_code == 0
    assert captured["yolo"] is False


def test_distill_aggregator_yolo_propagates(monkeypatch) -> None:
    _install_all(monkeypatch)
    monkeypatch.setattr(cli, "stream", _fake_stream(_ok("claude", "A"), _ok("codex", "B")))
    captured: dict = {}

    async def fake_run_provider(provider, prompt, timeout, model=None, yolo=False):
        captured["yolo"] = yolo
        return _ok("claude", "merged")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["distill", "-p", "claude", "-p", "codex", "--yolo", "hi"])
    assert result.exit_code == 0
    assert captured["yolo"] is True


def test_distill_emits_agy_partial_protection_note(monkeypatch) -> None:
    # Shared resolver: distill surfaces agy's honest note exactly like ask.
    _install_all(monkeypatch)
    monkeypatch.setattr(cli, "stream", _fake_stream(_ok("agy", "OK")))
    runner = CliRunner()
    result = runner.invoke(cli.app, ["distill", "-p", "agy", "hi"])
    assert result.exit_code == 0
    assert "agy is shell-sandboxed but can still edit files (no true read-only mode)" in result.stderr


def test_synthesizer_prompt_keeps_load_bearing_clauses() -> None:
    # The aggregator prompt must keep the MoA load-bearing instructions.
    text = cli.SYNTHESIZER_PROMPT
    assert "biased or incorrect" in text
    assert "not simply replicate" in text
    assert "refined, accurate, comprehensive" in text
    # Adapted away from "open-source models".
    assert "AI coding assistants" in text
    assert "open-source" not in text


# --- debate: roles ----------------------------------------------------------


def _provs(*names: str) -> list[Provider]:
    return [PROVIDERS[n] for n in names]


def test_debate_default_roles_two_debaters_one_judge() -> None:
    # n=3 default: top 2 selected debate, the 3rd judges.
    debaters, judge = assign_debate_roles(_provs("claude", "codex", "agy"), judge=None)
    assert [p.name for p in debaters] == ["claude", "codex"]
    assert judge.name == "agy"


def test_debate_judge_override_excludes_judge_from_debaters() -> None:
    # -j pins the judge; it must not also debate.
    debaters, judge = assign_debate_roles(_provs("claude", "codex", "agy"), judge="claude")
    assert judge.name == "claude"
    assert "claude" not in [p.name for p in debaters]
    assert [p.name for p in debaters] == ["codex", "agy"]


def test_debate_judge_must_not_be_a_debater_distinct() -> None:
    # The judge is always distinct from every debater (default and override).
    debaters, judge = assign_debate_roles(_provs("claude", "codex", "agy", "opencode"), judge="codex")
    assert judge.name not in [p.name for p in debaters]


def test_debate_too_few_providers_errors() -> None:
    # Fewer than 3 providers cannot give 2 debaters + 1 distinct judge.
    with pytest.raises(ValueError):
        assign_debate_roles(_provs("claude", "codex"), judge=None)


def test_debate_judge_override_needs_two_other_debaters() -> None:
    # With -j claude and only one other provider there aren't 2 debaters.
    with pytest.raises(ValueError):
        assign_debate_roles(_provs("claude", "codex"), judge="claude")


def test_debate_judge_must_be_selected() -> None:
    # A judge that isn't among the selected providers is an error, not a silent add.
    with pytest.raises(ValueError):
        assign_debate_roles(_provs("claude", "codex", "agy"), judge="opencode")


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


def test_debate_help_shows_rounds_judge_and_shared_options() -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["debate", "--help"])
    assert result.exit_code == 0
    for opt in ("--num", "--provider", "--exclude", "--model", "--timeout", "--file", "--json", "--yolo"):
        assert opt in result.stdout
    # Verb-specific options live only on debate.
    assert "--rounds" in result.stdout
    assert "--judge" in result.stdout


def test_debate_runs_rounds_then_judge(monkeypatch) -> None:
    # Debaters run sequentially across rounds, then the judge writes the verdict last.
    _install_all(monkeypatch)
    calls: list[str] = []

    async def fake_run_provider(provider, prompt, timeout, model=None, yolo=False):
        calls.append(provider.name)
        return _ok(provider.name, f"{provider.name} answer")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(
        cli.app, ["debate", "-p", "claude", "-p", "codex", "-p", "agy", "-r", "2", "hi"]
    )
    assert result.exit_code == 0
    # 2 debaters (claude, codex) x 2 rounds + 1 judge (agy) = 5 calls.
    assert calls == ["claude", "codex", "claude", "codex", "agy"]
    assert "## round 1 · claude" in result.stdout
    assert "## round 2 · codex" in result.stdout
    assert "## verdict · judge agy" in result.stdout
    # The verdict comes last.
    assert result.stdout.index("verdict") > result.stdout.index("round 2")


def test_debate_debaters_and_judge_read_only_by_default(monkeypatch) -> None:
    # Default mode: every debater turn AND the judge run read-only (yolo=False).
    _install_all(monkeypatch)
    yolos: list[bool] = []

    async def fake_run_provider(provider, prompt, timeout, model=None, yolo=False):
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
    # judge argv must carry the read-only permission flags by default (no bypass).
    _install_all(monkeypatch)
    argvs: list[list[str]] = []

    async def fake_exec(*args, **kwargs):
        argvs.append(list(args))
        return _FakeProc()

    monkeypatch.setattr(cli.asyncio, "create_subprocess_exec", fake_exec)
    runner = CliRunner()
    result = runner.invoke(
        cli.app, ["debate", "-p", "claude", "-p", "codex", "-p", "agy", "-r", "1", "hi"]
    )
    assert result.exit_code == 0
    # claude (debater) carries read-only flags.
    claude_argvs = [a for a in argvs if a and a[0] == "claude"]
    assert claude_argvs and all("--permission-mode" in a and a[a.index("--permission-mode") + 1] == "plan" for a in claude_argvs)
    # codex (debater) carries read-only flags.
    codex_argvs = [a for a in argvs if a and a[0] == "codex"]
    assert codex_argvs and all("read-only" in a for a in codex_argvs)
    # The judge (agy) ran read-only too: its argv has --sandbox, not full access.
    agy_argvs = [a for a in argvs if a and a[0] == "agy"]
    assert agy_argvs and all("--sandbox" in a for a in agy_argvs)


def test_debate_yolo_propagates(monkeypatch) -> None:
    # --yolo flows to every debater and the judge.
    _install_all(monkeypatch)
    yolos: list[bool] = []

    async def fake_run_provider(provider, prompt, timeout, model=None, yolo=False):
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

    async def fake_run_provider(provider, prompt, timeout, model=None, yolo=False):
        calls.append(provider.name)
        return _ok(provider.name, "answer")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(
        cli.app, ["debate", "-p", "claude", "-p", "codex", "-p", "agy", "-r", "9", "hi"]
    )
    assert result.exit_code == 0
    assert "capped" in result.stderr
    # 2 debaters x 4 (capped) rounds + 1 judge = 9 calls, not 19.
    debater_calls = [c for c in calls if c in ("claude", "codex")]
    assert len(debater_calls) == 8


def test_debate_too_few_providers_exits(monkeypatch) -> None:
    # Only 2 providers installed: no distinct judge, clean exit (no silent degrade).
    installed = {"claude", "codex"}
    monkeypatch.setattr(cli.shutil, "which", lambda exe: exe if exe in installed else None)

    async def fake_run_provider(provider, prompt, timeout, model=None, yolo=False):
        raise AssertionError("debate must not run with too few providers")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["debate", "-n", "3", "hi"])
    assert result.exit_code == 1
    assert "at least 3 providers" in result.stderr


def test_debate_converges_early(monkeypatch) -> None:
    # When both debaters signal "NO SUBSTANTIVE CHANGE" in round 2, the debate
    # stops before round 3 even though -r 3 was requested.
    _install_all(monkeypatch)
    round_state = {"n": 0}

    async def fake_run_provider(provider, prompt, timeout, model=None, yolo=False):
        round_state["n"] += 1
        # First 2 calls (round 1) are normal; from round 2 on, both concede.
        if round_state["n"] <= 2:
            return _ok(provider.name, "initial answer")
        return _ok(provider.name, "NO SUBSTANTIVE CHANGE I agree.")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(
        cli.app, ["debate", "-p", "claude", "-p", "codex", "-p", "agy", "-r", "3", "hi"]
    )
    assert result.exit_code == 0
    assert "converged" in result.stderr
    # 2 rounds x 2 debaters + 1 judge = 5 calls (round 3 never runs).
    assert round_state["n"] == 5


# --- debate: judge prompt is anonymized + shuffled (judge-blindness) --------


def test_build_judge_prompt_anonymizes_transcript() -> None:
    # The judge must never see provider/model attribution: the structure relabels
    # each turn "Participant N" with no provider/model name attached, so a brand
    # can't leak via the labels (guards judge-blindness from regression). Answer
    # bodies are neutral here so the only place a name could appear is a label.
    transcript = [
        RunResult("claude", "opus", "ok", "The answer is four.", "", 1.0, 0),
        RunResult("codex", "gpt-5.5", "ok", "Four, after carrying the one.", "", 1.0, 0),
    ]
    prompt, label_map = build_judge_prompt("Q?", transcript, rng=random.Random(0))
    for brand in ("claude", "codex", "opus", "gpt-5.5"):
        assert brand not in prompt
    assert "Participant 1" in prompt and "Participant 2" in prompt
    # The label_map still maps the (anonymized) labels back to real providers.
    assert set(label_map.values()) == {"claude", "codex"}
    assert set(label_map) == {"Participant 1", "Participant 2"}


def test_build_judge_prompt_shuffles_with_seeded_rng() -> None:
    # Order is shuffled (a seeded RNG makes this deterministic to assert). With
    # this seed the two participants come out in reversed provider order.
    transcript = [
        RunResult("claude", "opus", "ok", "alpha", "", 1.0, 0),
        RunResult("codex", "gpt-5.5", "ok", "beta", "", 1.0, 0),
    ]
    _, label_map = build_judge_prompt("Q?", transcript, rng=random.Random(1))
    # Seed 1 reverses the pair, so Participant 1 is the second provider (codex).
    assert label_map["Participant 1"] == "codex"
    assert label_map["Participant 2"] == "claude"


def test_build_judge_prompt_ignores_failed_turns() -> None:
    # A failed/errored turn never reaches the judge transcript.
    transcript = [
        RunResult("claude", "opus", "ok", "good answer", "", 1.0, 0),
        RunResult("codex", "gpt-5.5", "timeout", "", "boom", 1.0, None),
    ]
    prompt, label_map = build_judge_prompt("Q?", transcript, rng=random.Random(0))
    assert list(label_map) == ["Participant 1"]
    assert label_map == {"Participant 1": "claude"}
    assert "boom" not in prompt


# --- debate: turn prompt (cold round-1 vs adversarial later turns) ----------


def test_build_debate_turn_prompt_round1_first_turn_is_cold() -> None:
    # Round 1, first debater: no prior answers, so no adversarial instruction.
    prompt = build_debate_turn_prompt("What is 2+2?", prior=[])
    assert "What is 2+2?" in prompt
    assert cli.ADVERSARIAL_INSTRUCTION not in prompt
    assert "other participant" not in prompt


def test_build_debate_turn_prompt_later_turn_is_adversarial() -> None:
    # A later turn sees the prior answer AND the adversarial-stance instruction.
    prior = [("the other participant", "Their prior answer is 5.")]
    prompt = build_debate_turn_prompt("What is 2+2?", prior=prior)
    assert "Their prior answer is 5." in prompt
    assert cli.ADVERSARIAL_INSTRUCTION in prompt
    assert "the other participant" in prompt


# --- debate: convergence signal (round >= 2 only, never on failed turns) -----


def test_signals_convergence_marker_detected() -> None:
    # An OK turn opening with the marker (case-insensitive) signals convergence.
    assert signals_convergence(_ok("claude", "NO SUBSTANTIVE CHANGE, I agree."))
    assert signals_convergence(_ok("claude", "no substantive change - same as before"))


def test_signals_convergence_ignores_failed_turn() -> None:
    # A failed/errored turn never counts as convergence even if its text matches.
    failed = RunResult("claude", "opus", "timeout", "NO SUBSTANTIVE CHANGE", "boom", 1.0, None)
    assert not signals_convergence(failed)
    assert not signals_convergence(_ok("claude", "Here is a brand new argument."))


def test_debate_round1_marker_does_not_stop_debate(monkeypatch) -> None:
    # A round-1 convergence marker must NOT end the debate: early-stop is only
    # meaningful from round 2 onward (round 1 always has a cold answer).
    _install_all(monkeypatch)
    round_state = {"n": 0}

    async def fake_run_provider(provider, prompt, timeout, model=None, yolo=False):
        round_state["n"] += 1
        # Every debater turn opens with the marker, including round 1.
        return _ok(provider.name, "NO SUBSTANTIVE CHANGE I have nothing to add.")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(
        cli.app, ["debate", "-p", "claude", "-p", "codex", "-p", "agy", "-r", "2", "hi"]
    )
    assert result.exit_code == 0
    # Round 1 markers are ignored; round 2 markers then converge and stop after 2
    # rounds. 2 debaters x 2 rounds + 1 judge = 5 calls (round 1 did NOT stop it).
    assert round_state["n"] == 5
    # The debate only reports convergence at round 2, never round 1.
    assert "converged after round 2" in result.stderr


# --- config: location, precedence, set/unset round-trip ---------------------


def _config_env(monkeypatch, tmp_path):
    """Point the whole config layer at a temp dir via $MOA_CONFIG_DIR."""
    monkeypatch.setenv("MOA_CONFIG_DIR", str(tmp_path))
    return tmp_path / "config.toml"


def test_config_dir_honors_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MOA_CONFIG_DIR", str(tmp_path))
    assert cli.config_dir() == tmp_path
    assert cli.config_path() == tmp_path / "config.toml"


def test_config_absent_is_empty(monkeypatch, tmp_path) -> None:
    # No file == empty config == today's built-in behaviour.
    _config_env(monkeypatch, tmp_path)
    assert load_config() == {}


def test_config_set_creates_dir_and_file(monkeypatch, tmp_path) -> None:
    # `set` creates the dir/file on first write.
    nested = tmp_path / "fresh"
    monkeypatch.setenv("MOA_CONFIG_DIR", str(nested))
    runner = CliRunner()
    result = runner.invoke(cli.app, ["config", "set", "num", "2"])
    assert result.exit_code == 0
    assert (nested / "config.toml").exists()
    assert load_config() == {"num": 2}


def test_config_set_scalars_and_roundtrip(monkeypatch, tmp_path) -> None:
    _config_env(monkeypatch, tmp_path)
    runner = CliRunner()
    assert runner.invoke(cli.app, ["config", "set", "num", "2"]).exit_code == 0
    assert runner.invoke(cli.app, ["config", "set", "timeout", "120"]).exit_code == 0
    assert runner.invoke(cli.app, ["config", "set", "synthesizer", "codex"]).exit_code == 0
    config = load_config()
    assert config == {"num": 2, "timeout": 120.0, "synthesizer": "codex"}


def test_config_set_exclude_comma_separated(monkeypatch, tmp_path) -> None:
    _config_env(monkeypatch, tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["config", "set", "exclude", "claude,codex"])
    assert result.exit_code == 0
    assert load_config()["exclude"] == ["claude", "codex"]


def test_config_set_model_table(monkeypatch, tmp_path) -> None:
    _config_env(monkeypatch, tmp_path)
    runner = CliRunner()
    assert runner.invoke(cli.app, ["config", "set", "model", "claude=sonnet"]).exit_code == 0
    assert runner.invoke(cli.app, ["config", "set", "model", "agy=Gemini 3.1 Pro (Low)"]).exit_code == 0
    assert load_config()["models"] == {"claude": "sonnet", "agy": "Gemini 3.1 Pro (Low)"}


def test_config_set_rejects_unknown_key(monkeypatch, tmp_path) -> None:
    _config_env(monkeypatch, tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["config", "set", "nope", "1"])
    assert result.exit_code != 0


def test_config_set_rejects_bad_provider(monkeypatch, tmp_path) -> None:
    _config_env(monkeypatch, tmp_path)
    runner = CliRunner()
    assert runner.invoke(cli.app, ["config", "set", "model", "nope=x"]).exit_code != 0
    assert runner.invoke(cli.app, ["config", "set", "exclude", "nope"]).exit_code != 0


def test_config_set_rejects_bad_scalar(monkeypatch, tmp_path) -> None:
    _config_env(monkeypatch, tmp_path)
    runner = CliRunner()
    assert runner.invoke(cli.app, ["config", "set", "num", "0"]).exit_code != 0
    assert runner.invoke(cli.app, ["config", "set", "num", "abc"]).exit_code != 0
    assert runner.invoke(cli.app, ["config", "set", "synthesizer", "nope"]).exit_code != 0


def test_config_unset_scalar(monkeypatch, tmp_path) -> None:
    _config_env(monkeypatch, tmp_path)
    runner = CliRunner()
    runner.invoke(cli.app, ["config", "set", "num", "2"])
    assert runner.invoke(cli.app, ["config", "unset", "num"]).exit_code == 0
    assert "num" not in load_config()


def test_config_unset_single_model(monkeypatch, tmp_path) -> None:
    _config_env(monkeypatch, tmp_path)
    runner = CliRunner()
    runner.invoke(cli.app, ["config", "set", "model", "claude=sonnet"])
    runner.invoke(cli.app, ["config", "set", "model", "codex=gpt-5.5"])
    assert runner.invoke(cli.app, ["config", "unset", "model", "claude"]).exit_code == 0
    assert load_config()["models"] == {"codex": "gpt-5.5"}


def test_config_show_includes_defaults_and_path(monkeypatch, tmp_path) -> None:
    cfg_file = _config_env(monkeypatch, tmp_path)
    runner = CliRunner()
    runner.invoke(cli.app, ["config", "set", "num", "2"])
    result = runner.invoke(cli.app, ["config", "show"])
    assert result.exit_code == 0
    assert str(cfg_file) in result.stdout
    assert "num = 2" in result.stdout
    # Defaults for unset keys still show.
    assert "timeout = 180" in result.stdout
    assert 'synthesizer = "auto"' in result.stdout


def test_config_path_prints_file(monkeypatch, tmp_path) -> None:
    cfg_file = _config_env(monkeypatch, tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["config", "path"])
    assert result.exit_code == 0
    assert str(cfg_file) in result.stdout


def test_load_config_rejects_unknown_key(monkeypatch, tmp_path) -> None:
    cfg_file = _config_env(monkeypatch, tmp_path)
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text("bogus = 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config()


def test_serialize_config_roundtrips_via_load(monkeypatch, tmp_path) -> None:
    # The hand-rolled serializer's output must reload identically through tomllib.
    cfg_file = _config_env(monkeypatch, tmp_path)
    original = {
        "num": 2,
        "timeout": 90.5,
        "synthesizer": "codex",
        "exclude": ["claude"],
        "models": {"claude": "sonnet", "agy": 'has "quotes" and a\ttab'},
    }
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(serialize_config(original), encoding="utf-8")
    assert load_config() == original


# --- config: precedence through resolve_run (flag > config > default) -------


def test_config_default_used_when_flag_omitted(monkeypatch, tmp_path) -> None:
    # config num=2 is honoured by `ask` when -n is omitted (the verb picks the
    # config default through resolve_run, like every verb).
    _install_all(monkeypatch)
    _config_env(monkeypatch, tmp_path)
    (tmp_path / "config.toml").write_text("num = 2\n", encoding="utf-8")
    monkeypatch.setattr(cli, "stream", _fake_stream(_ok("claude", "A"), _ok("codex", "B")))
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ask", "hi"])
    assert result.exit_code == 0
    # num=2 from config -> top 2 installed (claude, codex), not the built-in 3.
    assert "Asking claude, codex (" in result.stderr


def test_flag_overrides_config(monkeypatch, tmp_path) -> None:
    # An explicit -n always wins over the config num.
    _install_all(monkeypatch)
    _config_env(monkeypatch, tmp_path)
    (tmp_path / "config.toml").write_text("num = 2\n", encoding="utf-8")
    monkeypatch.setattr(
        cli, "stream", _fake_stream(_ok("claude", "A"), _ok("codex", "B"), _ok("agy", "C"))
    )
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ask", "-n", "3", "hi"])
    assert result.exit_code == 0
    # -n 3 overrides config num=2.
    assert "Asking claude, codex, agy (" in result.stderr


def test_config_exclude_default_applied(monkeypatch, tmp_path) -> None:
    # config exclude is honoured when -x is omitted.
    _install_all(monkeypatch)
    _config_env(monkeypatch, tmp_path)
    (tmp_path / "config.toml").write_text('exclude = ["claude"]\n', encoding="utf-8")
    monkeypatch.setattr(cli, "stream", _fake_stream(_ok("codex", "A")))
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ask", "-n", "1", "hi"])
    assert result.exit_code == 0
    # claude excluded by config -> top installed becomes codex.
    assert "Asking codex (" in result.stderr
    assert "excluded: claude" in result.stderr


def test_config_models_reach_run(monkeypatch, tmp_path) -> None:
    # config [models] supplies a default model, and a CLI -m override wins.
    _install_all(monkeypatch)
    _config_env(monkeypatch, tmp_path)
    (tmp_path / "config.toml").write_text(
        '[models]\nclaude = "sonnet"\n', encoding="utf-8"
    )
    captured: dict = {}

    async def fake_stream(providers, prompt, timeout, models=None, yolo=False):
        captured["models"] = models
        yield _ok("claude", "A")

    monkeypatch.setattr(cli, "stream", fake_stream)
    runner = CliRunner()
    # No -m: config model is used.
    assert runner.invoke(cli.app, ["ask", "-p", "claude", "hi"]).exit_code == 0
    assert captured["models"] == {"claude": "sonnet"}
    # -m overrides the config model for that provider.
    assert runner.invoke(cli.app, ["ask", "-p", "claude", "-m", "claude=opus", "hi"]).exit_code == 0
    assert captured["models"]["claude"] == "opus"


def test_config_synthesizer_default_in_distill(monkeypatch, tmp_path) -> None:
    # distill's verb-specific -s/--synthesizer merges from config when omitted.
    _install_all(monkeypatch)
    _config_env(monkeypatch, tmp_path)
    (tmp_path / "config.toml").write_text('synthesizer = "codex"\n', encoding="utf-8")
    monkeypatch.setattr(cli, "stream", _fake_stream(_ok("claude", "A"), _ok("codex", "B")))

    async def fake_run_provider(provider, prompt, timeout, model=None, yolo=False):
        return _ok(provider.name, "merged")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["distill", "-p", "claude", "-p", "codex", "hi"])
    assert result.exit_code == 0
    # synthesizer=codex from config -> codex distills (not the auto default claude).
    assert "## synthesis · via codex" in result.stdout

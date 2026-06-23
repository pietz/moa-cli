import asyncio

import pytest
import typer

from moa_cli import execution, providers
from moa_cli.cli import parse_model_overrides
from moa_cli.execution import run_provider
from moa_cli.providers import PROVIDERS, Provider, select_for_run


# --- providers --------------------------------------------------------------


def test_claude_env_unsets_claudecode(monkeypatch) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    env = PROVIDERS["claude"].env()
    assert "CLAUDECODE" not in env
    assert env["NO_COLOR"] == "1"


def test_codex_command_uses_output_file_and_skip_git() -> None:
    cmd = PROVIDERS["codex"].build("hello", "gpt-5.5", "/tmp/out.txt", (), ())
    assert cmd[:4] == ["codex", "exec", "-m", "gpt-5.5"]
    assert "--skip-git-repo-check" in cmd
    assert cmd[cmd.index("-o") + 1] == "/tmp/out.txt"
    assert cmd[-1] == "hello"


def test_agy_command_pins_gemini_model() -> None:
    # Regression: agy must get an explicit --model or it defaults to Gemini Flash.
    cmd = PROVIDERS["agy"].build("hi", "Gemini 3.1 Pro (High)", None, (), ())
    assert cmd == ["agy", "--model", "Gemini 3.1 Pro (High)", "-p", "hi"]


def test_opencode_command_omits_model_when_empty() -> None:
    # opencode has no universal default; an empty model means "skip -m".
    assert PROVIDERS["opencode"].build("hi", "", None, (), ()) == [
        "opencode",
        "run",
        "hi",
    ]
    cmd = PROVIDERS["opencode"].build("hi", "prov/model", None, (), ())
    assert cmd == ["opencode", "run", "-m", "prov/model", "hi"]


# --- permission map (read-only by default, --yolo opt-in) -------------------


def test_perm_args_readonly_vs_yolo_per_provider() -> None:
    # The permission argv is selected by mode, as data.
    assert PROVIDERS["claude"].perm_args(yolo=False) == ("--permission-mode", "default")
    assert PROVIDERS["claude"].perm_args(yolo=True) == (
        "--permission-mode",
        "bypassPermissions",
    )
    assert PROVIDERS["codex"].perm_args(yolo=False) == ("-s", "read-only")
    assert PROVIDERS["codex"].perm_args(yolo=True) == ("-s", "danger-full-access")
    assert PROVIDERS["opencode"].perm_args(yolo=False) == ("--agent", "plan")
    assert PROVIDERS["opencode"].perm_args(yolo=True) == (
        "--dangerously-skip-permissions",
    )
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
    assert p["claude"].build(
        "hi", "opus", None, ("--permission-mode", "default"), ()
    ) == [
        "claude",
        "--model",
        "opus",
        "--permission-mode",
        "default",
        "-p",
        "hi",
    ]
    codex_cmd = p["codex"].build("hi", "gpt-5.5", "/tmp/o.txt", ("-s", "read-only"), ())
    assert codex_cmd[codex_cmd.index("-s") + 1] == "read-only"
    assert codex_cmd.index("-s") < codex_cmd.index(
        "-o"
    )  # perm flags before output flag
    assert codex_cmd[-1] == "hi"
    assert p["opencode"].build("hi", "", None, ("--agent", "plan"), ()) == [
        "opencode",
        "run",
        "--agent",
        "plan",
        "hi",
    ]
    assert p["agy"].build("hi", "g", None, (), ()) == [
        "agy",
        "--model",
        "g",
        "-p",
        "hi",
    ]


def test_build_splices_yolo_flags() -> None:
    assert PROVIDERS["claude"].build(
        "hi", "opus", None, ("--permission-mode", "bypassPermissions"), ()
    ) == [
        "claude",
        "--model",
        "opus",
        "--permission-mode",
        "bypassPermissions",
        "-p",
        "hi",
    ]
    codex_cmd = PROVIDERS["codex"].build(
        "hi", "gpt-5.5", None, ("-s", "danger-full-access"), ()
    )
    assert codex_cmd[codex_cmd.index("-s") + 1] == "danger-full-access"


# --- reasoning / effort (config-only, raw pass-through) ---------------------


def test_effort_args_maps_per_provider_verbatim() -> None:
    # The ONLY mapping moa knows: variable -> flag location. The value is pasted
    # verbatim, never normalized. codex -> -c model_reasoning_effort=<v>,
    # opencode -> --variant <v>; agy/claude have no flag, so always ().
    assert PROVIDERS["codex"].effort_args("high") == (
        "-c",
        "model_reasoning_effort=high",
    )
    assert PROVIDERS["codex"].effort_args("xl") == ("-c", "model_reasoning_effort=xl")
    assert PROVIDERS["opencode"].effort_args("high") == ("--variant", "high")
    assert PROVIDERS["opencode"].effort_args("max") == ("--variant", "max")
    # agy carries reasoning in the model name; claude has no per-call flag.
    assert PROVIDERS["agy"].effort_args("high") == ()
    assert PROVIDERS["claude"].effort_args("high") == ()


def test_effort_args_unset_is_empty_for_every_provider() -> None:
    # No effort configured (None or empty) => no flag for ANY provider.
    for name in PROVIDERS:
        assert PROVIDERS[name].effort_args(None) == ()
        assert PROVIDERS[name].effort_args("") == ()


def test_build_splices_effort_after_perm_before_prompt() -> None:
    # Effort flags land after the permission flags and before the prompt/output.
    codex_cmd = PROVIDERS["codex"].build(
        "hi",
        "gpt-5.5",
        "/tmp/o.txt",
        ("-s", "read-only"),
        ("-c", "model_reasoning_effort=high"),
    )
    assert "model_reasoning_effort=high" in codex_cmd
    assert codex_cmd.index("-s") < codex_cmd.index("-c")  # perm before effort
    assert codex_cmd.index("-c") < codex_cmd.index("-o")  # effort before output
    assert codex_cmd[-1] == "hi"
    opencode_cmd = PROVIDERS["opencode"].build(
        "hi", "prov/model", None, ("--agent", "plan"), ("--variant", "high")
    )
    assert opencode_cmd == [
        "opencode",
        "run",
        "--agent",
        "plan",
        "--variant",
        "high",
        "-m",
        "prov/model",
        "hi",
    ]


def test_build_omits_effort_when_unset() -> None:
    # An empty effort tuple leaves the argv exactly as before (no stray flag).
    assert PROVIDERS["codex"].build("hi", "gpt-5.5", None, ("-s", "read-only"), ()) == [
        "codex",
        "exec",
        "-m",
        "gpt-5.5",
        "--skip-git-repo-check",
        "--color",
        "never",
        "-s",
        "read-only",
        "hi",
    ]
    assert PROVIDERS["opencode"].build("hi", "", None, (), ()) == [
        "opencode",
        "run",
        "hi",
    ]


def test_run_provider_threads_effort_into_argv_codex(monkeypatch) -> None:
    # effort set on codex => -c model_reasoning_effort=<v> in the spawned argv.
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["argv"] = list(args)
        raise FileNotFoundError

    monkeypatch.setattr(execution.asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(
        run_provider(
            PROVIDERS["codex"], "hi", timeout=5, model="gpt-5.5", effort="high"
        )
    )
    assert "-c" in captured["argv"]
    assert (
        captured["argv"][captured["argv"].index("-c") + 1]
        == "model_reasoning_effort=high"
    )


def test_run_provider_threads_effort_into_argv_opencode(monkeypatch) -> None:
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["argv"] = list(args)
        raise FileNotFoundError

    monkeypatch.setattr(execution.asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(
        run_provider(PROVIDERS["opencode"], "hi", timeout=5, model="p/m", effort="max")
    )
    assert "--variant" in captured["argv"]
    assert captured["argv"][captured["argv"].index("--variant") + 1] == "max"


def test_run_provider_omits_effort_when_unset(monkeypatch) -> None:
    # No effort => no effort flag in argv for codex or opencode.
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["argv"] = list(args)
        raise FileNotFoundError

    monkeypatch.setattr(execution.asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(run_provider(PROVIDERS["codex"], "hi", timeout=5, model="gpt-5.5"))
    assert "model_reasoning_effort" not in " ".join(captured["argv"])
    asyncio.run(run_provider(PROVIDERS["opencode"], "hi", timeout=5, model="p/m"))
    assert "--variant" not in captured["argv"]


def test_run_provider_agy_claude_emit_no_effort_flag(monkeypatch) -> None:
    # Even with an effort value, agy and claude spawn NO effort flag (they have
    # no mapping); the value is silently inert.
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["argv"] = list(args)
        raise FileNotFoundError

    monkeypatch.setattr(execution.asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(
        run_provider(PROVIDERS["agy"], "hi", timeout=5, model="g", effort="high")
    )
    assert captured["argv"] == ["agy", "--sandbox", "--model", "g", "-p", "hi"]
    asyncio.run(
        run_provider(PROVIDERS["claude"], "hi", timeout=5, model="opus", effort="high")
    )
    assert captured["argv"] == [
        "claude",
        "--model",
        "opus",
        "--permission-mode",
        "default",
        "-p",
        "hi",
    ]


def test_select_for_run_takes_first_n_installed(monkeypatch) -> None:
    installed = {"claude", "codex", "agy", "opencode"}
    monkeypatch.setattr(
        providers.shutil, "which", lambda exe: exe if exe in installed else None
    )
    # agy stays in the default panel at priority #3 (it runs unscoped).
    assert [p.name for p in select_for_run(2, None)[0]] == ["claude", "codex"]
    assert [p.name for p in select_for_run(3, None)[0]] == ["claude", "codex", "agy"]
    assert [p.name for p in select_for_run(4, None)[0]] == [
        "claude",
        "codex",
        "agy",
        "opencode",
    ]


def test_select_for_run_pins_agy_without_yolo(monkeypatch) -> None:
    # agy has no read-only mode but is still selectable - it runs unscoped, no error.
    installed = {"claude", "codex", "agy", "opencode"}
    monkeypatch.setattr(
        providers.shutil, "which", lambda exe: exe if exe in installed else None
    )
    chosen, skipped = select_for_run(3, ("agy",))
    assert [p.name for p in chosen] == ["agy"]
    assert skipped == []


def test_select_for_run_skips_uninstalled_explicit(monkeypatch) -> None:
    monkeypatch.setattr(
        providers.shutil, "which", lambda exe: exe if exe == "claude" else None
    )
    chosen, skipped = select_for_run(3, ("claude", "opencode"))
    assert [p.name for p in chosen] == ["claude"]
    assert skipped == ["opencode"]


def test_select_for_run_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        select_for_run(3, ("claude", "nope"))


def test_select_for_run_excludes_before_taking_n(monkeypatch) -> None:
    installed = {"claude", "codex", "agy", "opencode"}
    monkeypatch.setattr(
        providers.shutil, "which", lambda exe: exe if exe in installed else None
    )
    chosen, skipped = select_for_run(3, None, exclude=("claude",))
    assert [p.name for p in chosen] == ["codex", "agy", "opencode"]
    assert skipped == []


def test_select_for_run_excludes_from_explicit(monkeypatch) -> None:
    installed = {"claude", "codex", "agy", "opencode"}
    monkeypatch.setattr(
        providers.shutil, "which", lambda exe: exe if exe in installed else None
    )
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
        build=lambda _p, _m, _o, _perm, _e: [
            "uv",
            "run",
            "python",
            "-c",
            f"import time; time.sleep({sleep_seconds})",
        ],
    )


def test_run_provider_times_out() -> None:
    result = asyncio.run(run_provider(_slow_provider(5), "hello", timeout=0.1))
    assert result.status == "timeout"
    assert result.returncode is None


def test_run_provider_cancellation_terminates_and_reraises(monkeypatch) -> None:
    class FakeProcess:
        returncode = None
        pid = 123

        async def communicate(self):
            await asyncio.Future()

    process = FakeProcess()
    terminated: list[FakeProcess] = []

    async def fake_exec(*args, **kwargs):
        return process

    async def fake_terminate(candidate):
        terminated.append(candidate)

    monkeypatch.setattr(execution.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("moa_cli.execution._terminate", fake_terminate)

    async def cancel_run() -> None:
        task = asyncio.create_task(
            run_provider(PROVIDERS["claude"], "hello", timeout=5)
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_run())
    assert terminated == [process]


def test_run_provider_missing_executable() -> None:
    provider = Provider(
        "ghost",
        "definitely-not-a-real-binary",
        "x",
        lambda _p, _m, _o, _perm, _e: ["definitely-not-a-real-binary"],
    )
    result = asyncio.run(run_provider(provider, "hello", timeout=5))
    assert result.status == "missing"


def test_run_provider_passes_devnull_stdin(monkeypatch) -> None:
    # Regression for the hang bug: codex/agy block forever on an inherited TTY
    # stdin, so every spawn must explicitly use DEVNULL.
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured.update(kwargs)
        raise FileNotFoundError  # bail out early; we only care about kwargs

    monkeypatch.setattr(execution.asyncio, "create_subprocess_exec", fake_exec)
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
    with pytest.raises(typer.BadParameter):
        parse_model_overrides(["claude"])


def test_parse_model_overrides_rejects_unknown_provider() -> None:
    with pytest.raises(typer.BadParameter):
        parse_model_overrides(["nope=x"])


def test_run_provider_uses_override_model(monkeypatch) -> None:
    # The override model must reach the spawned argv, not provider.default_model.
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["argv"] = list(args)
        raise FileNotFoundError  # bail out early; we only care about argv

    monkeypatch.setattr(execution.asyncio, "create_subprocess_exec", fake_exec)
    result = asyncio.run(
        run_provider(PROVIDERS["claude"], "hi", timeout=5, model="sonnet")
    )
    # moa's default run is read-only, so claude's --permission-mode flag is spliced in.
    assert captured["argv"] == [
        "claude",
        "--model",
        "sonnet",
        "--permission-mode",
        "default",
        "-p",
        "hi",
    ]
    assert result.model == "sonnet"


def test_run_provider_defaults_model_when_no_override(monkeypatch) -> None:
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["argv"] = list(args)
        raise FileNotFoundError

    monkeypatch.setattr(execution.asyncio, "create_subprocess_exec", fake_exec)
    result = asyncio.run(run_provider(PROVIDERS["claude"], "hi", timeout=5))
    assert captured["argv"] == [
        "claude",
        "--model",
        "opus",
        "--permission-mode",
        "default",
        "-p",
        "hi",
    ]
    assert result.model == "opus"


def test_run_provider_readonly_by_default_argv(monkeypatch) -> None:
    # Default run carries each sandboxable provider's read-only flag.
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["argv"] = list(args)
        raise FileNotFoundError

    monkeypatch.setattr(execution.asyncio, "create_subprocess_exec", fake_exec)

    asyncio.run(run_provider(PROVIDERS["claude"], "hi", timeout=5))
    assert "--permission-mode" in captured["argv"]
    assert (
        captured["argv"][captured["argv"].index("--permission-mode") + 1] == "default"
    )

    asyncio.run(run_provider(PROVIDERS["codex"], "hi", timeout=5, model="gpt-5.5"))
    assert "-s" in captured["argv"]
    assert captured["argv"][captured["argv"].index("-s") + 1] == "read-only"

    asyncio.run(
        run_provider(PROVIDERS["opencode"], "hi", timeout=5, model="prov/model")
    )
    assert "--agent" in captured["argv"]
    assert captured["argv"][captured["argv"].index("--agent") + 1] == "plan"


def test_run_provider_agy_default_argv_has_sandbox(monkeypatch) -> None:
    # agy's default argv includes --sandbox (partial: shell only - it can still
    # edit files). Under --yolo (below) --sandbox is dropped for full access.
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["argv"] = list(args)
        raise FileNotFoundError

    monkeypatch.setattr(execution.asyncio, "create_subprocess_exec", fake_exec)
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

    monkeypatch.setattr(execution.asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(run_provider(PROVIDERS["claude"], "hi", timeout=5, yolo=True))
    assert captured["argv"] == [
        "claude",
        "--model",
        "opus",
        "--permission-mode",
        "bypassPermissions",
        "-p",
        "hi",
    ]
    asyncio.run(run_provider(PROVIDERS["codex"], "hi", timeout=5, yolo=True))
    assert captured["argv"][captured["argv"].index("-s") + 1] == "danger-full-access"
    asyncio.run(run_provider(PROVIDERS["opencode"], "hi", timeout=5, yolo=True))
    assert captured["argv"] == [
        "opencode",
        "run",
        "--dangerously-skip-permissions",
        "hi",
    ]

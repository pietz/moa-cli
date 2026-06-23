import pytest
from typer.testing import CliRunner

from moa_cli import cli, execution, providers
from moa_cli.config import config_dir, config_path, load_config, serialize_config
from moa_cli.execution import RunResult


def _ok(provider: str, text: str) -> RunResult:
    return RunResult(provider, "m", "ok", text, "", 1.0, 0)


def _fake_stream(*results: RunResult):
    async def generate(*args, **kwargs):
        for result in results:
            yield result

    return generate


def _install_all(monkeypatch) -> None:
    monkeypatch.setattr(providers.shutil, "which", lambda executable: executable)


# --- config: location, precedence, set/unset round-trip ---------------------


def _config_env(monkeypatch, tmp_path):
    """Point the whole config layer at a temp dir via $MOA_CONFIG_DIR."""
    monkeypatch.setenv("MOA_CONFIG_DIR", str(tmp_path))
    return tmp_path / "config.toml"


def test_config_dir_honors_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MOA_CONFIG_DIR", str(tmp_path))
    assert config_dir() == tmp_path
    assert config_path() == tmp_path / "config.toml"


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
    assert (
        runner.invoke(cli.app, ["config", "set", "synthesizer", "codex"]).exit_code == 0
    )
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
    assert (
        runner.invoke(cli.app, ["config", "set", "model", "claude=sonnet"]).exit_code
        == 0
    )
    assert (
        runner.invoke(
            cli.app, ["config", "set", "model", "agy=Gemini 3.1 Pro (Low)"]
        ).exit_code
        == 0
    )
    assert load_config()["models"] == {
        "claude": "sonnet",
        "agy": "Gemini 3.1 Pro (Low)",
    }


# --- config: per-provider effort + [models] deprecated alias ----------------


def test_config_providers_block_parses_model_and_effort(monkeypatch, tmp_path) -> None:
    # [providers.<name>] blocks supply model + effort; load normalizes them into
    # the models/efforts maps.
    cfg_file = _config_env(monkeypatch, tmp_path)
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(
        '[providers.codex]\nmodel = "gpt-5.5"\neffort = "high"\n\n'
        '[providers.opencode]\neffort = "max"\n',
        encoding="utf-8",
    )
    config = load_config()
    assert config["models"] == {"codex": "gpt-5.5"}
    assert config["efforts"] == {"codex": "high", "opencode": "max"}


def test_config_models_alias_still_parses(monkeypatch, tmp_path) -> None:
    # The deprecated flat [models] table still works as an alias for
    # [providers.<name>].model.
    cfg_file = _config_env(monkeypatch, tmp_path)
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text('[models]\nclaude = "sonnet"\n', encoding="utf-8")
    assert load_config()["models"] == {"claude": "sonnet"}


def test_config_provider_block_model_wins_over_models_alias(
    monkeypatch, tmp_path
) -> None:
    # On conflict the [providers.<name>].model wins over the deprecated [models]
    # entry, and a one-line note is surfaced (not an error).
    cfg_file = _config_env(monkeypatch, tmp_path)
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(
        '[models]\ncodex = "old-model"\n\n[providers.codex]\nmodel = "gpt-5.5"\n',
        encoding="utf-8",
    )
    # load succeeds (no raise) with the provider-block model winning.
    assert load_config()["models"] == {"codex": "gpt-5.5"}
    # The note surfaces through a verb that loads config (stderr, not a crash).
    _install_all(monkeypatch)
    monkeypatch.setattr(cli, "stream", _fake_stream(_ok("codex", "A")))
    result = CliRunner().invoke(cli.app, ["ask", "-p", "codex", "hi"])
    assert result.exit_code == 0
    assert "overrides the deprecated [models]" in result.stderr


def test_config_set_effort_roundtrip(monkeypatch, tmp_path) -> None:
    _config_env(monkeypatch, tmp_path)
    runner = CliRunner()
    assert (
        runner.invoke(cli.app, ["config", "set", "effort", "codex=high"]).exit_code == 0
    )
    assert (
        runner.invoke(cli.app, ["config", "set", "effort", "opencode=max"]).exit_code
        == 0
    )
    assert load_config()["efforts"] == {"codex": "high", "opencode": "max"}
    # unset one effort, the other survives.
    assert runner.invoke(cli.app, ["config", "unset", "effort", "codex"]).exit_code == 0
    assert load_config()["efforts"] == {"opencode": "max"}
    # unset the last one drops the map entirely.
    assert (
        runner.invoke(cli.app, ["config", "unset", "effort", "opencode"]).exit_code == 0
    )
    assert "efforts" not in load_config()


def test_config_set_effort_value_passed_through_verbatim(monkeypatch, tmp_path) -> None:
    # moa does not normalize the value space: any non-empty string round-trips.
    _config_env(monkeypatch, tmp_path)
    runner = CliRunner()
    assert (
        runner.invoke(
            cli.app, ["config", "set", "effort", "codex=ultra-deep-think"]
        ).exit_code
        == 0
    )
    assert load_config()["efforts"]["codex"] == "ultra-deep-think"


def test_config_set_effort_rejects_bad_format_and_empty(monkeypatch, tmp_path) -> None:
    _config_env(monkeypatch, tmp_path)
    runner = CliRunner()
    assert (
        runner.invoke(cli.app, ["config", "set", "effort", "codex"]).exit_code != 0
    )  # no '='
    assert (
        runner.invoke(cli.app, ["config", "set", "effort", "nope=high"]).exit_code != 0
    )  # bad provider
    assert (
        runner.invoke(cli.app, ["config", "set", "effort", "codex="]).exit_code != 0
    )  # empty value


def test_config_set_effort_noflag_provider_notes_but_stores(
    monkeypatch, tmp_path
) -> None:
    # agy/claude have no effort flag: setting it stores the value but warns it's
    # inert (a note, not an error).
    _config_env(monkeypatch, tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["config", "set", "effort", "agy=high"])
    assert result.exit_code == 0
    assert "no effort flag" in result.stderr
    assert load_config()["efforts"] == {"agy": "high"}


def test_config_unset_effort_not_set_is_noop(monkeypatch, tmp_path) -> None:
    _config_env(monkeypatch, tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["config", "unset", "effort", "codex"])
    assert result.exit_code == 0
    assert "was not set" in result.stdout


def test_config_show_displays_effort_per_provider(monkeypatch, tmp_path) -> None:
    _config_env(monkeypatch, tmp_path)
    runner = CliRunner()
    runner.invoke(cli.app, ["config", "set", "model", "codex=gpt-5.5"])
    runner.invoke(cli.app, ["config", "set", "effort", "codex=high"])
    result = runner.invoke(cli.app, ["config", "show"])
    assert result.exit_code == 0
    assert "[providers.codex]" in result.stdout
    assert 'model = "gpt-5.5"' in result.stdout
    assert 'effort = "high"' in result.stdout


def test_config_effort_reaches_run(monkeypatch, tmp_path) -> None:
    # A configured effort flows through resolve_run into run_provider's argv.
    _install_all(monkeypatch)
    cfg_file = _config_env(monkeypatch, tmp_path)
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text('[providers.codex]\neffort = "high"\n', encoding="utf-8")
    argvs: list[list[str]] = []

    async def fake_exec(*args, **kwargs):
        argvs.append(list(args))
        raise FileNotFoundError

    monkeypatch.setattr(execution.asyncio, "create_subprocess_exec", fake_exec)
    result = CliRunner().invoke(cli.app, ["ask", "-p", "codex", "hi"])
    assert result.exit_code == 1  # FileNotFoundError -> missing, but argv captured
    codex_argv = next(a for a in argvs if a and a[0] == "codex")
    assert "model_reasoning_effort=high" in codex_argv


def test_config_effort_omitted_when_unset_reaches_run(monkeypatch, tmp_path) -> None:
    # No effort in config => no effort flag in the spawned argv (tool default).
    _install_all(monkeypatch)
    _config_env(monkeypatch, tmp_path)
    argvs: list[list[str]] = []

    async def fake_exec(*args, **kwargs):
        argvs.append(list(args))
        raise FileNotFoundError

    monkeypatch.setattr(execution.asyncio, "create_subprocess_exec", fake_exec)
    result = CliRunner().invoke(cli.app, ["ask", "-p", "codex", "hi"])
    assert result.exit_code == 1
    codex_argv = next(a for a in argvs if a and a[0] == "codex")
    assert "model_reasoning_effort" not in " ".join(codex_argv)


def test_serialize_config_effort_roundtrips_via_load(monkeypatch, tmp_path) -> None:
    # model + effort grouped under [providers.<name>] must reload identically.
    cfg_file = _config_env(monkeypatch, tmp_path)
    original = {
        "models": {"codex": "gpt-5.5"},
        "efforts": {"codex": "high", "opencode": "max"},
    }
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(serialize_config(original), encoding="utf-8")
    assert load_config() == original


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
    assert (
        runner.invoke(cli.app, ["config", "set", "synthesizer", "nope"]).exit_code != 0
    )
    assert runner.invoke(cli.app, ["config", "set", "moderator", "nope"]).exit_code != 0


def test_config_set_moderator_roundtrip(monkeypatch, tmp_path) -> None:
    _config_env(monkeypatch, tmp_path)
    runner = CliRunner()
    assert (
        runner.invoke(cli.app, ["config", "set", "moderator", "claude"]).exit_code == 0
    )
    assert load_config()["moderator"] == "claude"
    assert runner.invoke(cli.app, ["config", "set", "moderator", "auto"]).exit_code == 0
    assert load_config()["moderator"] == "auto"
    assert runner.invoke(cli.app, ["config", "unset", "moderator"]).exit_code == 0
    assert "moderator" not in load_config()


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
    assert "timeout = 900" in result.stdout
    assert 'synthesizer = "auto"' in result.stdout
    assert 'moderator = "auto"' in result.stdout


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
    monkeypatch.setattr(
        cli, "stream", _fake_stream(_ok("claude", "A"), _ok("codex", "B"))
    )
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
        cli,
        "stream",
        _fake_stream(_ok("claude", "A"), _ok("codex", "B"), _ok("agy", "C")),
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

    async def fake_stream(
        providers, prompt, timeout, models=None, yolo=False, efforts=None
    ):
        captured["models"] = models
        captured["efforts"] = efforts
        yield _ok("claude", "A")

    monkeypatch.setattr(cli, "stream", fake_stream)
    runner = CliRunner()
    # No -m: config model is used.
    assert runner.invoke(cli.app, ["ask", "-p", "claude", "hi"]).exit_code == 0
    assert captured["models"] == {"claude": "sonnet"}
    # -m overrides the config model for that provider.
    assert (
        runner.invoke(
            cli.app, ["ask", "-p", "claude", "-m", "claude=opus", "hi"]
        ).exit_code
        == 0
    )
    assert captured["models"]["claude"] == "opus"


def test_config_synthesizer_default_in_distill(monkeypatch, tmp_path) -> None:
    # distill's verb-specific -s/--synthesizer merges from config when omitted.
    _install_all(monkeypatch)
    _config_env(monkeypatch, tmp_path)
    (tmp_path / "config.toml").write_text('synthesizer = "codex"\n', encoding="utf-8")
    monkeypatch.setattr(
        cli, "stream", _fake_stream(_ok("claude", "A"), _ok("codex", "B"))
    )

    async def fake_run_provider(
        provider, prompt, timeout, model=None, yolo=False, effort=None
    ):
        return _ok(provider.name, "merged")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["distill", "-p", "claude", "-p", "codex", "hi"])
    assert result.exit_code == 0
    # synthesizer=codex from config -> codex distills (not the auto default claude).
    assert "synthesis · via codex" in result.stdout


def test_config_moderator_default_in_debate(monkeypatch, tmp_path) -> None:
    # debate's verb-specific --moderator merges from config when omitted, and a
    # CLI flag still wins over it.
    _install_all(monkeypatch)
    _config_env(monkeypatch, tmp_path)
    (tmp_path / "config.toml").write_text('moderator = "agy"\n', encoding="utf-8")

    async def fake_run_provider(
        provider, prompt, timeout, model=None, yolo=False, effort=None
    ):
        return _ok(provider.name, "answer")

    monkeypatch.setattr(cli, "run_provider", fake_run_provider)
    runner = CliRunner()
    # No --moderator: config's agy moderates.
    result = runner.invoke(
        cli.app, ["debate", "-p", "claude", "-p", "codex", "-p", "agy", "-r", "1", "hi"]
    )
    assert result.exit_code == 0
    assert "verdict · moderator agy" in result.stdout
    # --moderator wins over config.
    result = runner.invoke(
        cli.app,
        [
            "debate",
            "-p",
            "claude",
            "-p",
            "codex",
            "--moderator",
            "codex",
            "-r",
            "1",
            "hi",
        ],
    )
    assert result.exit_code == 0
    assert "verdict · moderator codex" in result.stdout


def test_flag_equal_to_default_still_beats_config(monkeypatch, tmp_path) -> None:
    # The Typer trap: an explicit flag whose value equals the built-in default
    # must still override the config. Options default to None when omitted, so
    # `--timeout 900` (==default 900) is distinguishable from "omitted" and wins.
    _install_all(monkeypatch)
    _config_env(monkeypatch, tmp_path)
    (tmp_path / "config.toml").write_text("timeout = 120\n", encoding="utf-8")
    monkeypatch.setattr(cli, "stream", _fake_stream(_ok("claude", "A")))
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ask", "-n", "1", "--timeout", "900", "hi"])
    assert result.exit_code == 0
    # The explicit 900 wins; config's 120 must not leak into the run.
    assert "timeout 900s" in result.stderr
    assert "timeout 120s" not in result.stderr


def test_malformed_config_fails_cleanly_but_path_survives(
    monkeypatch, tmp_path
) -> None:
    # A broken file must not crash with a traceback. `config path` never loads,
    # so it still works; commands/verbs that read it fail cleanly via a handled
    # BadParameter (SystemExit), not an unhandled TOMLDecodeError escaping.
    _install_all(monkeypatch)
    cfg_file = _config_env(monkeypatch, tmp_path)
    cfg_file.write_text("this is not valid toml\n", encoding="utf-8")
    runner = CliRunner()

    # `path` never loads the file, so a broken file can't break it.
    path_result = runner.invoke(cli.app, ["config", "path"])
    assert path_result.exit_code == 0
    assert str(cfg_file) in path_result.stdout

    # `show` and the verbs read the file but fail cleanly (handled SystemExit
    # from BadParameter), never letting the TOMLDecodeError surface raw.
    for args in (["config", "show"], ["ask", "hi"]):
        result = runner.invoke(cli.app, args)
        assert result.exit_code != 0
        assert isinstance(result.exception, SystemExit)


def test_config_show_rejects_out_of_range_value(monkeypatch, tmp_path) -> None:
    # A hand-edited but out-of-range value (num=0) must fail cleanly through the
    # `show` command path, not print the invalid value as if it were usable.
    cfg_file = _config_env(monkeypatch, tmp_path)
    cfg_file.write_text("num = 0\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(cli.app, ["config", "show"])
    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)

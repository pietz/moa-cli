"""Persistent configuration loading, validation, and serialization."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Iterable
from pathlib import Path

from .output import note
from .providers import PRIORITY, PROVIDERS

CONFIG_SCALARS: dict[str, type] = {
    "num": int,
    "timeout": float,
    "synthesizer": str,
    "moderator": str,
}
CONFIG_KEYS: tuple[str, ...] = (
    *CONFIG_SCALARS,
    "exclude",
    "providers",
    "models",
)
PROVIDER_KEYS: tuple[str, ...] = ("model", "effort")
SYNTHESIZER_MODES: tuple[str, ...] = ("auto", "first", "random")
MODERATOR_MODES: tuple[str, ...] = ("auto",)
CONFIG_DEFAULTS: dict = {
    "num": 3,
    "timeout": 900.0,
    "synthesizer": "auto",
    "moderator": "auto",
    "exclude": [],
    "models": {},
    "efforts": {},
}


def config_dir() -> Path:
    override = os.environ.get("MOA_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".moa"


def config_path() -> Path:
    return config_dir() / "config.toml"


def validate_providers(names: Iterable[str], where: str) -> None:
    unknown = [name for name in names if name not in PROVIDERS]
    if unknown:
        raise ValueError(
            f"Unknown provider(s) in {where}: {', '.join(unknown)}. "
            f"Known: {', '.join(PROVIDERS)}."
        )


def validate_scalar(key: str, value) -> None:
    if key == "num" and value < 1:
        raise ValueError("num must be at least 1.")
    if key == "timeout" and value <= 0:
        raise ValueError("timeout must be greater than 0.")
    if key == "synthesizer" and value not in (
        *SYNTHESIZER_MODES,
        *PROVIDERS,
    ):
        allowed = ", ".join((*SYNTHESIZER_MODES, *PROVIDERS))
        raise ValueError(f"synthesizer must be one of: {allowed}.")
    if key == "moderator" and value not in (
        *MODERATOR_MODES,
        *PROVIDERS,
    ):
        allowed = ", ".join((*MODERATOR_MODES, *PROVIDERS))
        raise ValueError(f"moderator must be one of: {allowed}.")


def load_config() -> dict:
    """Read and validate the config file; return an empty dict if absent."""
    path = config_path()
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    unknown = [key for key in raw if key not in CONFIG_KEYS]
    if unknown:
        raise ValueError(
            f"Unknown config key(s): {', '.join(unknown)}. "
            f"Known: {', '.join(CONFIG_KEYS)}."
        )

    config: dict = {}
    for key, kind in CONFIG_SCALARS.items():
        if key not in raw:
            continue
        try:
            config[key] = kind(raw[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Config key {key!r} must be {kind.__name__}.") from exc
        validate_scalar(key, config[key])

    if "exclude" in raw:
        value = raw["exclude"]
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise ValueError("Config key 'exclude' must be a list of provider names.")
        validate_providers(value, "exclude")
        config["exclude"] = value

    models: dict[str, str] = {}
    efforts: dict[str, str] = {}

    if "models" in raw:
        legacy = raw["models"]
        if not isinstance(legacy, dict) or not all(
            isinstance(value, str) for value in legacy.values()
        ):
            raise ValueError(
                "Config table '[models]' must map provider names to model strings."
            )
        validate_providers(legacy, "[models]")
        models.update(legacy)

    if "providers" in raw:
        providers = raw["providers"]
        if not isinstance(providers, dict):
            raise ValueError(
                "Config table '[providers]' must map provider names "
                "to {model, effort} blocks."
            )
        validate_providers(providers, "[providers]")
        shadowed: list[str] = []
        for name, block in providers.items():
            if not isinstance(block, dict):
                raise ValueError(
                    f"Config table '[providers.{name}]' must be a "
                    "{model, effort} block."
                )
            unknown_provider_keys = [key for key in block if key not in PROVIDER_KEYS]
            if unknown_provider_keys:
                raise ValueError(
                    f"Unknown key(s) in [providers.{name}]: "
                    f"{', '.join(unknown_provider_keys)}. "
                    f"Known: {', '.join(PROVIDER_KEYS)}."
                )
            if "model" in block:
                model = block["model"]
                if not isinstance(model, str):
                    raise ValueError(f"[providers.{name}].model must be a string.")
                if name in raw.get("models", {}) and raw["models"][name] != model:
                    shadowed.append(name)
                models[name] = model
            if "effort" in block:
                effort = block["effort"]
                if not isinstance(effort, str) or not effort:
                    raise ValueError(
                        f"[providers.{name}].effort must be a non-empty string."
                    )
                efforts[name] = effort
        if shadowed:
            note(
                f"Note: [providers.{shadowed[0]}].model overrides the "
                f"deprecated [models] entry for {', '.join(shadowed)}."
            )

    if models:
        config["models"] = models
    if efforts:
        config["efforts"] = efforts
    return config


def _toml_str(value: str) -> str:
    out: list[str] = []
    named = {
        "\b": "\\b",
        "\t": "\\t",
        "\n": "\\n",
        "\f": "\\f",
        "\r": "\\r",
        '"': '\\"',
        "\\": "\\\\",
    }
    for char in value:
        if char in named:
            out.append(named[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def serialize_config(config: dict) -> str:
    lines: list[str] = []
    if "num" in config:
        lines.append(f"num = {int(config['num'])}")
    if "timeout" in config:
        timeout = float(config["timeout"])
        rendered = int(timeout) if timeout.is_integer() else repr(timeout)
        lines.append(f"timeout = {rendered}")
    if "synthesizer" in config:
        lines.append(f"synthesizer = {_toml_str(config['synthesizer'])}")
    if "moderator" in config:
        lines.append(f"moderator = {_toml_str(config['moderator'])}")
    if "exclude" in config:
        items = ", ".join(_toml_str(value) for value in config["exclude"])
        lines.append(f"exclude = [{items}]")

    models = config.get("models") or {}
    efforts = config.get("efforts") or {}
    names = [name for name in PRIORITY if name in models or name in efforts]
    names += [name for name in (*models, *efforts) if name not in names]
    for name in names:
        lines.append("")
        lines.append(f"[providers.{name}]")
        if name in models:
            lines.append(f"model = {_toml_str(models[name])}")
        if name in efforts:
            lines.append(f"effort = {_toml_str(efforts[name])}")
    return "\n".join(lines) + "\n" if lines else ""


def write_config(config: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_config(config), encoding="utf-8")


def read_config_or_empty() -> dict:
    try:
        return load_config()
    except ValueError:
        return {}


def resolve_option(flag, config_key: str, config: dict, default):
    if flag is not None:
        return flag
    if config_key in config:
        return config[config_key]
    return default

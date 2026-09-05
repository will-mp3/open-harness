from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

GLOBAL_CONFIG_PATH = Path.home() / ".config" / "open-harness" / "config.json"
PROJECT_CONFIG_RELATIVE = Path(".open-harness") / "config.json"


class ConfigError(Exception):
    """Raised when configuration cannot be loaded"""


class ToolOutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_lines: int = 2000
    max_bytes: int = 51200


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = "anthropic/claude-sonnet-5"
    api_key: str | None = Field(default=None, repr=False)
    max_steps: int = 100
    max_tokens: int | None = None
    temperature: float | None = None
    reasoning_effort: str | None = None
    header_timeout: float = 300.0
    chunk_timeout: float = 300.0
    bash_timeout: float = 120.0
    max_retries: int = 5
    tool_output: ToolOutputConfig = ToolOutputConfig()
    referer: str = "https://github.com/open-harness"
    title: str = "open-harness"


def _validate_config(
    data: Mapping[str, Any],
    *,
    source: str | Path,
) -> Config:
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise ConfigError(f"{source}: invalid configuration: {details}") from exc


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except UnicodeError as exc:
        raise ConfigError(f"{path}: configuration file must contain valid UTF-8") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: unable to read configuration file: {exc}") from exc

    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{path}: invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}"
        ) from exc

    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a JSON object")

    layer = cast(dict[str, Any], data)
    _validate_config(layer, source=path)

    return layer


def _deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)

    for key, value in overlay.items():
        existing = merged.get(key)

        if isinstance(existing, dict) and isinstance(value, Mapping):
            merged[key] = _deep_merge(
                cast(dict[str, Any], existing),
                cast(Mapping[str, Any], value),
            )
        else:
            merged[key] = value

    return merged


def _from_env(env: Mapping[str, str]) -> dict[str, Any]:
    layer: dict[str, Any] = {}

    if api_key := env.get("OPENROUTER_API_KEY"):
        layer["api_key"] = api_key

    if model := env.get("OPEN_HARNESS_MODEL"):
        layer["model"] = model

    return layer


def load_config(
    cwd: Path,
    *,
    env: Mapping[str, str] | None = None,
    overrides: dict[str, Any] | None = None,
    global_path: Path | None = None,
) -> Config:
    resolved_env = os.environ if env is None else env
    resolved_global = GLOBAL_CONFIG_PATH if global_path is None else global_path
    resolved_overrides = {
        key: value for key, value in (overrides or {}).items() if value is not None
    }

    merged: dict[str, Any] = {}
    merged = _deep_merge(merged, _read_json_object(resolved_global))
    merged = _deep_merge(merged, _read_json_object(cwd / PROJECT_CONFIG_RELATIVE))
    merged = _deep_merge(merged, _from_env(resolved_env))
    merged = _deep_merge(merged, resolved_overrides)

    return _validate_config(merged, source="merged configuration")

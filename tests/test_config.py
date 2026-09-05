from __future__ import annotations

import json
from pathlib import Path

import pytest

from open_harness.cli.config import ConfigError, load_config


def test_defaults_when_nothing_configured(tmp_path: Path) -> None:
    cfg = load_config(tmp_path, env={}, global_path=tmp_path / "absent.json")

    assert cfg.max_steps == 100
    assert cfg.tool_output.max_lines == 2000
    assert cfg.tool_output.max_bytes == 51200
    assert cfg.api_key is None


def test_precedence_global_then_project_then_env_then_overrides(tmp_path: Path) -> None:
    global_path = tmp_path / "global.json"
    global_path.write_text(json.dumps({"model": "from/global", "max_steps": 7}))

    project_dir = tmp_path / "proj" / ".open-harness"
    project_dir.mkdir(parents=True)
    (project_dir / "config.json").write_text(json.dumps({"model": "from/project"}))

    cfg = load_config(
        tmp_path / "proj",
        env={
            "OPEN_HARNESS_MODEL": "from/env",
            "OPENROUTER_API_KEY": "sk-test",
        },
        overrides={"model": "from/flag"},
        global_path=global_path,
    )

    assert cfg.model == "from/flag"
    assert cfg.api_key == "sk-test"
    assert cfg.max_steps == 7


def test_env_beats_project(tmp_path: Path) -> None:
    project_dir = tmp_path / ".open-harness"
    project_dir.mkdir(parents=True)
    (project_dir / "config.json").write_text(json.dumps({"model": "from/project"}))

    cfg = load_config(
        tmp_path,
        env={"OPEN_HARNESS_MODEL": "from/env"},
        global_path=tmp_path / "absent.json",
    )

    assert cfg.model == "from/env"


def test_nested_tool_output_is_deep_merged(tmp_path: Path) -> None:
    global_path = tmp_path / "global.json"
    global_path.write_text(json.dumps({"tool_output": {"max_lines": 10}}))

    project_dir = tmp_path / ".open-harness"
    project_dir.mkdir(parents=True)
    (project_dir / "config.json").write_text(json.dumps({"tool_output": {"max_bytes": 20}}))

    cfg = load_config(tmp_path, env={}, global_path=global_path)

    assert cfg.tool_output.max_lines == 10
    assert cfg.tool_output.max_bytes == 20


def test_invalid_json_names_the_file_and_position(tmp_path: Path) -> None:
    global_path = tmp_path / "global.json"
    global_path.write_text('{"model": }')

    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path, env={}, global_path=global_path)

    assert "global.json" in str(excinfo.value)
    assert "line" in str(excinfo.value)


def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    global_path = tmp_path / "global.json"
    global_path.write_text(json.dumps({"nonsense_key": 1}))

    with pytest.raises(ConfigError):
        load_config(tmp_path, env={}, global_path=global_path)


def test_api_key_is_not_exposed_in_config_representation(tmp_path: Path) -> None:
    api_key = "sk-sensitive-test-value"

    cfg = load_config(
        tmp_path,
        env={"OPENROUTER_API_KEY": api_key},
        global_path=tmp_path / "absent.json",
    )

    rendered = f"{cfg!r}\n{cfg}"

    assert api_key not in rendered


def test_invalid_field_value_names_source_file(tmp_path: Path) -> None:
    global_path = tmp_path / "global.json"
    global_path.write_text(json.dumps({"max_steps": "not-an-integer"}))

    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path, env={}, global_path=global_path)

    message = str(excinfo.value)

    assert str(global_path) in message
    assert "max_steps" in message


def test_invalid_global_value_is_not_masked_by_project(tmp_path: Path) -> None:
    global_path = tmp_path / "global.json"
    global_path.write_text(json.dumps({"max_steps": "not-an-integer"}))

    project_dir = tmp_path / ".open-harness"
    project_dir.mkdir()
    (project_dir / "config.json").write_text(json.dumps({"max_steps": 10}))

    with pytest.raises(ConfigError):
        load_config(tmp_path, env={}, global_path=global_path)


def test_config_path_that_is_a_directory_is_rejected(
    tmp_path: Path,
) -> None:
    global_path = tmp_path / "global.json"
    global_path.mkdir()

    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path, env={}, global_path=global_path)

    assert str(global_path) in str(excinfo.value)


def test_non_utf8_config_names_source_file(tmp_path: Path) -> None:
    global_path = tmp_path / "global.json"
    global_path.write_bytes(b"\xff")

    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path, env={}, global_path=global_path)

    assert str(global_path) in str(excinfo.value)

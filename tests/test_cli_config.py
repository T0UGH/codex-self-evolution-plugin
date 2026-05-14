"""Integration tests for retained ``codex-self-evolution config`` subcommands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_self_evolution import cli


def _invoke(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    """Run cli.main with argv and capture stdout JSON plus exit code."""
    exit_code = cli.main(argv)
    captured = capsys.readouterr()
    try:
        result = json.loads(captured.out)
    except json.JSONDecodeError:
        result = {"_raw_stdout": captured.out, "_raw_stderr": captured.err}
    return exit_code, result


def test_config_path_prints_absolute_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    code, result = _invoke(["config", "path"], capsys)
    assert code == 0
    assert result["config_path"] == str(tmp_path / "config.toml")


def test_config_init_creates_new_system_template(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    code, result = _invoke(["config", "init"], capsys)
    assert code == 0
    assert result["status"] == "created"
    content = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "schema_version = 2" in content
    assert "[session_reflection]" in content
    assert "[session_reflection.trigger]" in content
    assert "[session_recall]" in content
    assert "[log]" in content


def test_config_init_refuses_overwrite_without_force(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "config.toml").write_text("# existing user config\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    code, result = _invoke(["config", "init"], capsys)
    assert code == 1
    assert result["status"] == "exists"
    assert (tmp_path / "config.toml").read_text(encoding="utf-8") == "# existing user config\n"


def test_config_init_force_overwrites(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "config.toml").write_text("# old\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    code, result = _invoke(["config", "init", "--force"], capsys)
    assert code == 0
    content = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "# old" not in content
    assert "schema_version = 2" in content


def test_config_show_returns_new_system_resolved_tree(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "config.toml").write_text("""
schema_version = 2

[session_reflection]
enabled = false
sandbox = "workspace-write"

[session_recall]
enabled = false
""", encoding="utf-8")
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    code, result = _invoke(["config", "show"], capsys)
    assert code == 0
    assert result["config_exists"] is True
    assert result["resolved"]["session_reflection"]["enabled"] is False
    assert result["resolved"]["session_reflection"]["sandbox"] == "workspace-write"
    assert result["resolved"]["session_recall"]["enabled"] is False
    assert "reviewer" not in result["resolved"]
    assert "compile" not in result["resolved"]
    assert result["sources"]["session_reflection.enabled"] == "config.toml"
    assert "env_provider" in result
    assert "keys_set" in result["env_provider"]


def test_config_show_raw_returns_file_contents(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_toml = "[session_recall]\nenabled = false\n"
    (tmp_path / "config.toml").write_text(raw_toml, encoding="utf-8")
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    code, result = _invoke(["config", "show", "--raw"], capsys)
    assert code == 0
    assert result["raw"] == raw_toml
    assert result["config_exists"] is True


def test_config_show_surface_api_key_presence_without_values(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_provider = tmp_path / ".env.provider"
    env_provider.write_text(
        "MINIMAX_API_KEY=sk-this-value-must-not-leak\n"
        "OPENAI_API_KEY=\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    code, result = _invoke(["config", "show"], capsys)
    assert code == 0
    ep = result["env_provider"]
    assert "MINIMAX_API_KEY" in ep["keys_set"]
    assert "OPENAI_API_KEY" in ep["keys_unset"]
    assert "sk-this-value-must-not-leak" not in json.dumps(result)


def test_config_validate_exits_zero_for_clean_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "config.toml").write_text(
        "schema_version = 2\n[session_recall]\nenabled = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    code, result = _invoke(["config", "validate"], capsys)
    assert code == 0
    assert result["status"] == "ok"
    assert result["warnings"] == []


def test_config_validate_exits_one_for_unknown_legacy_section(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "config.toml").write_text("[reviewer]\nprovider = \"minimax\"\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    code, result = _invoke(["config", "validate"], capsys)
    assert code == 1
    assert result["status"] == "warnings"
    assert any("unknown top-level config key: reviewer" in w for w in result["warnings"])


def test_config_validate_exits_two_for_parse_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "config.toml").write_text("[session_recall\nenabled = true", encoding="utf-8")
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    code, result = _invoke(["config", "validate"], capsys)
    assert code == 2
    assert result["status"] == "parse_error"


def test_config_show_surfaces_toml_warnings(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "config.toml").write_text("""
[session_recall]
enabled = true
api_key = "sk-should-not-be-here"
""", encoding="utf-8")
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    code, result = _invoke(["config", "show"], capsys)
    assert code == 0
    assert any("looks like an API key" in w for w in result["warnings"])

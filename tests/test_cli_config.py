"""Integration tests for ``codex-self-evolution config`` subcommands.

These tests drive the CLI main() function directly (without subprocess)
so they exercise argparse wiring, dispatch, and exit-code mapping. They
also lock in the JSON output shape — any downstream dashboard / readme
example relies on these fields existing under these names.
"""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

import pytest

from codex_self_evolution import cli


def _invoke(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    """Run cli.main with argv and capture stdout JSON + exit code."""
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


def test_config_init_creates_template_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    code, result = _invoke(["config", "init"], capsys)
    assert code == 0
    assert result["status"] == "created"
    config_path = tmp_path / "config.toml"
    assert config_path.is_file()
    content = config_path.read_text(encoding="utf-8")
    # Template includes the schema_version and at least one section.
    assert "schema_version" in content
    assert "[reviewer]" in content


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
    # User's file untouched.
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
    assert "schema_version" in content


def test_config_show_returns_resolved_tree(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "config.toml").write_text("""
[reviewer]
provider = "openai-compatible"
model = "deepseek-chat"
base_url = "https://api.deepseek.com/v1"
""", encoding="utf-8")
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    code, result = _invoke(["config", "show"], capsys)
    assert code == 0
    assert result["config_exists"] is True
    assert result["resolved"]["reviewer"]["provider"] == "openai-compatible"
    assert result["resolved"]["reviewer"]["base_url"] == "https://api.deepseek.com/v1"
    assert result["sources"]["reviewer.provider"] == "config.toml"
    # API key summary appears even when no keys set.
    assert "env_provider" in result
    assert "keys_set" in result["env_provider"]


def test_config_show_raw_returns_file_contents(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_toml = "[reviewer]\nprovider = \"minimax\"\n"
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
    """Q2 decision: show whether each well-known key is set, never the value."""
    env_provider = tmp_path / ".env.provider"
    env_provider.write_text(
        "MINIMAX_API_KEY=sk-this-value-must-not-leak\n"
        "OPENAI_API_KEY=\n",  # explicitly empty
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    code, result = _invoke(["config", "show"], capsys)
    assert code == 0
    ep = result["env_provider"]
    assert "MINIMAX_API_KEY" in ep["keys_set"]
    assert "OPENAI_API_KEY" in ep["keys_unset"]
    # Value should appear nowhere in the output.
    serialized = json.dumps(result)
    assert "sk-this-value-must-not-leak" not in serialized


def test_config_validate_exits_zero_for_clean_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "config.toml").write_text("[reviewer]\nprovider = \"minimax\"\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    code, result = _invoke(["config", "validate"], capsys)
    assert code == 0
    assert result["status"] == "ok"
    assert result["warnings"] == []


def test_config_validate_exits_one_for_warnings(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Typo in a field name should produce exit code 1 so CI can fail early."""
    (tmp_path / "config.toml").write_text("[reviewer]\nmodle = \"x\"\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    code, result = _invoke(["config", "validate"], capsys)
    assert code == 1
    assert result["status"] == "warnings"
    assert any("unknown key" in w for w in result["warnings"])


def test_config_validate_exits_two_for_parse_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "config.toml").write_text("[reviewer\nprovider = unterminated", encoding="utf-8")
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    code, result = _invoke(["config", "validate"], capsys)
    assert code == 2
    assert result["status"] == "parse_error"


def test_config_migrate_from_env_creates_file_from_legacy_vars(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Q3 decision: legacy env vars get captured into config.toml explicitly."""
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    monkeypatch.setenv("MINIMAX_REVIEW_MODEL", "MiniMax-Text-01")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/anthropic/v1/messages")
    code, result = _invoke(["config", "migrate-from-env"], capsys)
    assert code == 0
    assert result["status"] == "migrated"
    # The migrated file mentions both captured fields.
    content = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "MiniMax-Text-01" in content
    assert "api.minimaxi.com" in content
    # migrated_fields list surfaces which paths came from env.
    assert "reviewer.model" in result["migrated_fields"]
    assert "reviewer.base_url" in result["migrated_fields"]


def test_config_migrate_refuses_overwrite_without_force(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "config.toml").write_text("# hand-written\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    monkeypatch.setenv("MINIMAX_REVIEW_MODEL", "MiniMax-Text-01")
    code, result = _invoke(["config", "migrate-from-env"], capsys)
    assert code == 1
    assert result["status"] == "exists"
    # Hand-written file untouched.
    assert (tmp_path / "config.toml").read_text(encoding="utf-8") == "# hand-written\n"


def test_config_migrate_with_no_env_vars_still_writes_scaffold(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No env vars set → still generates a valid empty-scaffold file with a hint."""
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    # Make sure no legacy env vars are set.
    for name in ["MINIMAX_REVIEW_MODEL", "MINIMAX_BASE_URL",
                 "OPENAI_REVIEW_MODEL", "OPENAI_BASE_URL",
                 "ANTHROPIC_REVIEW_MODEL", "ANTHROPIC_BASE_URL",
                 "CODEX_SELF_EVOLUTION_REVIEWER_PROVIDER",
                 "CODEX_SELF_EVOLUTION_REVIEWER_MODEL",
                 "CODEX_SELF_EVOLUTION_REVIEWER_BASE_URL"]:
        monkeypatch.delenv(name, raising=False)
    code, result = _invoke(["config", "migrate-from-env"], capsys)
    assert code == 0
    content = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "schema_version = 1" in content
    assert "No legacy env-driven overrides found" in content


def test_config_show_surfaces_toml_warnings(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Warnings from the loader must reach the user via ``config show``."""
    (tmp_path / "config.toml").write_text("""
[reviewer]
provider = "minimax"
api_key = "sk-should-not-be-here"
""", encoding="utf-8")
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    code, result = _invoke(["config", "show"], capsys)
    assert code == 0
    assert any("looks like an API key" in w for w in result["warnings"])

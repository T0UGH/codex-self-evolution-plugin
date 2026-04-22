"""Tests for env_loader: parse .env.provider, apply to os.environ safely."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from codex_self_evolution.env_loader import (
    apply_to_environ,
    hydrate_env_for_subprocesses,
    load_env_provider,
    parse_env_file,
)


def test_parse_env_file_extracts_key_equals_value(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.provider"
    env_file.write_text(
        "\n".join(
            [
                "# comment line",
                "",
                "MINIMAX_API_KEY=sk-abc123",
                "export MINIMAX_REGION=cn",
                "EMPTY_KEY=",
                'QUOTED_KEY="has spaces"',
                "SINGLE_QUOTED='also has'",
            ]
        )
    )
    parsed = parse_env_file(env_file)
    assert parsed == {
        "MINIMAX_API_KEY": "sk-abc123",
        "MINIMAX_REGION": "cn",
        "QUOTED_KEY": "has spaces",
        "SINGLE_QUOTED": "also has",
    }


def test_parse_env_file_handles_missing_file(tmp_path: Path) -> None:
    assert parse_env_file(tmp_path / "nonexistent") == {}


def test_parse_env_file_ignores_malformed_lines(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.provider"
    env_file.write_text("not_key_equals\nBAD KEY=value\n== random\n")
    assert parse_env_file(env_file) == {}


def test_load_env_provider_respects_override_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env.provider").write_text("TEST_KEY=abc\n")
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    assert load_env_provider() == {"TEST_KEY": "abc"}


def test_apply_to_environ_does_not_overwrite_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_EXISTING", "original_shell_value")
    applied = apply_to_environ(
        {"TEST_EXISTING": "provider_value", "TEST_NEW": "new_value"},
        overwrite=False,
    )
    # Existing shell var wins — explicit user override beats .env.provider.
    assert os.environ["TEST_EXISTING"] == "original_shell_value"
    assert os.environ["TEST_NEW"] == "new_value"
    assert applied == ["TEST_NEW"]


def test_apply_to_environ_overwrites_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_OVERRIDE", "old")
    applied = apply_to_environ({"TEST_OVERRIDE": "new"}, overwrite=True)
    assert os.environ["TEST_OVERRIDE"] == "new"
    assert applied == ["TEST_OVERRIDE"]


def test_hydrate_for_subprocesses_loads_provider_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".env.provider").write_text("FAKE_PROVIDER_KEY=abc123\n")
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    monkeypatch.delenv("FAKE_PROVIDER_KEY", raising=False)

    applied = hydrate_env_for_subprocesses()
    assert "FAKE_PROVIDER_KEY" in applied
    assert os.environ["FAKE_PROVIDER_KEY"] == "abc123"


def test_hydrate_noop_when_key_already_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".env.provider").write_text("SHELL_WINS_KEY=from_provider\n")
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    monkeypatch.setenv("SHELL_WINS_KEY", "from_shell")

    applied = hydrate_env_for_subprocesses()
    # Shell export wins; hydration skips the already-set key.
    assert "SHELL_WINS_KEY" not in applied
    assert os.environ["SHELL_WINS_KEY"] == "from_shell"

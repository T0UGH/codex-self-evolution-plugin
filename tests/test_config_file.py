from pathlib import Path

import pytest

from codex_self_evolution.config_file import ConfigError, config_to_dict, get_config_path, load_config


def _write_config(home: Path, toml: str) -> Path:
    """Write a config.toml fixture under a temporary CSEP home."""
    home.mkdir(parents=True, exist_ok=True)
    path = home / "config.toml"
    path.write_text(toml, encoding="utf-8")
    return path


def test_missing_config_returns_new_system_defaults(tmp_path: Path) -> None:
    loaded = load_config(home=tmp_path, env={})
    assert loaded.config_exists is False
    assert loaded.config.schema_version == 2
    assert loaded.config.session_reflection.enabled is True
    assert loaded.config.session_reflection.backend == "codex-app-server"
    assert loaded.config.session_reflection.skill_prefix == "csep-reflect-"
    assert loaded.config.session_recall.enabled is True
    assert loaded.config.session_recall.stop_hook_archive is True
    assert loaded.sources["session_reflection.enabled"] == "default"
    assert loaded.sources["session_recall.enabled"] == "default"
    assert loaded.warnings == []


def test_toml_values_apply_to_new_system_sections(tmp_path: Path) -> None:
    _write_config(tmp_path, """
schema_version = 2

[session_reflection]
enabled = false
backend = "codex-app-server"
model = "gpt-5.3-codex-spark"
ephemeral = false
sandbox = "workspace-write"
approval_policy = "on-request"
skill_prefix = "csep-reflect-"
timeout_seconds = 120
max_concurrent_jobs = 1

[session_reflection.trigger]
enabled = false
memory_stop_interval = 5
memory_context_chars = 12000
skill_tool_call_interval = 20
high_signal_immediate = false
skill_generation_mode = "one_shot_active"
active_job_stale_seconds = 600

[session_recall]
enabled = false
stop_hook_archive = false

[log]
retention_days = 3
""")
    loaded = load_config(home=tmp_path, env={})
    cfg = loaded.config
    assert cfg.session_reflection.enabled is False
    assert cfg.session_reflection.ephemeral is False
    assert cfg.session_reflection.sandbox == "workspace-write"
    assert cfg.session_reflection.approval_policy == "on-request"
    assert cfg.session_reflection.timeout_seconds == 120.0
    assert cfg.session_reflection.trigger.enabled is False
    assert cfg.session_reflection.trigger.memory_stop_interval == 5
    assert cfg.session_reflection.trigger.skill_tool_call_interval == 20
    assert cfg.session_recall.enabled is False
    assert cfg.session_recall.stop_hook_archive is False
    assert cfg.log.retention_days == 3


def test_unknown_sections_warn_and_do_not_appear_in_resolved_config(tmp_path: Path) -> None:
    _write_config(tmp_path, """
[unknown_section]
enabled = true
""")
    loaded = load_config(home=tmp_path, env={})
    resolved = config_to_dict(loaded.config)
    assert "unknown_section" not in resolved
    assert any("unknown top-level config key: unknown_section" in warning for warning in loaded.warnings)


def test_invalid_session_reflection_skill_prefix_warns(tmp_path: Path) -> None:
    _write_config(tmp_path, """
[session_reflection]
skill_prefix = "bad-prefix-"
""")
    loaded = load_config(home=tmp_path, env={})
    assert loaded.config.session_reflection.skill_prefix == "bad-prefix-"
    assert any("session_reflection.skill_prefix" in warning for warning in loaded.warnings)


def test_schema_version_must_be_current_v2(tmp_path: Path) -> None:
    _write_config(tmp_path, "schema_version = 1\n")
    with pytest.raises(ConfigError):
        load_config(home=tmp_path, env={})


def test_get_config_path_uses_override(tmp_path: Path) -> None:
    assert get_config_path(tmp_path) == tmp_path / "config.toml"

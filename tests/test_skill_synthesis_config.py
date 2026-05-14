from __future__ import annotations

from pathlib import Path

from codex_self_evolution.config_file import config_to_dict, load_config
from codex_self_evolution.config_file_template import CONFIG_TEMPLATE


def _write_config(home: Path, text: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")


def test_skill_synthesis_defaults_are_enabled_and_independent(tmp_path: Path) -> None:
    loaded = load_config(home=tmp_path, env={})
    cfg = loaded.config.skill_synthesis

    assert cfg.enabled is True
    assert cfg.default_mode == "incremental"
    assert cfg.lookback_hours == 24
    assert cfg.lookback_days == 30
    assert cfg.skills_prefix == "csep-synth-"
    assert cfg.agent.backend == "agent:pi"
    assert cfg.agent.provider == "minimax"
    assert cfg.agent.model == "MiniMax-M2.7"
    assert cfg.agent.timeout_seconds == 1800.0
    assert loaded.sources["skill_synthesis.agent.provider"] == "default"
    assert loaded.config.compile.pi.provider == "kimi"


def test_skill_synthesis_toml_values_apply_without_compile_fallback(tmp_path: Path) -> None:
    _write_config(tmp_path, """
[compile.pi]
provider = "kimi"
model = "kimi-k2.6"

[skill_synthesis]
enabled = true
default_mode = "full"
lookback_hours = 12
lookback_days = 14
skills_prefix = "csep-synth-"

[skill_synthesis.agent]
backend = "agent:pi"
provider = "minimax"
model = "MiniMax-M2.7"
timeout_seconds = 2400
""")

    loaded = load_config(home=tmp_path, env={})
    cfg = loaded.config.skill_synthesis

    assert cfg.default_mode == "full"
    assert cfg.lookback_hours == 12
    assert cfg.lookback_days == 14
    assert cfg.agent.provider == "minimax"
    assert cfg.agent.timeout_seconds == 2400.0
    assert loaded.sources["skill_synthesis.agent.provider"] == "config.toml"


def test_skill_synthesis_unsupported_backend_warns_when_enabled(tmp_path: Path) -> None:
    _write_config(tmp_path, """
[skill_synthesis]
enabled = true

[skill_synthesis.agent]
backend = "agent:opencode"
provider = "minimax"
model = "MiniMax-M2.7"
""")

    loaded = load_config(home=tmp_path, env={})

    assert loaded.config.skill_synthesis.agent.backend == "agent:opencode"
    assert any("skill_synthesis.agent.backend" in warning for warning in loaded.warnings)


def test_skill_synthesis_template_contains_default_minimax_config() -> None:
    assert "[skill_synthesis]" in CONFIG_TEMPLATE
    assert "enabled = true" in CONFIG_TEMPLATE
    assert "[skill_synthesis.agent]" in CONFIG_TEMPLATE
    assert 'provider = "minimax"' in CONFIG_TEMPLATE
    assert 'model = "MiniMax-M2.7"' in CONFIG_TEMPLATE


def test_config_to_dict_includes_skill_synthesis(tmp_path: Path) -> None:
    loaded = load_config(home=tmp_path, env={})
    data = config_to_dict(loaded.config)

    assert data["skill_synthesis"]["enabled"] is True
    assert data["skill_synthesis"]["agent"]["backend"] == "agent:pi"

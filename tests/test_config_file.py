"""Tests for ``config_file.load_config`` — the unified config loader.

These tests pin the precedence rules (env > toml > default) and the
forgiveness rules (missing file / partial toml / unknown keys all OK).
Breaking any of these would silently change which provider an upgrading
user's reviewer hits, so the coverage is intentionally dense.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codex_self_evolution.config_file import (
    ALLOWED_PROVIDERS,
    ConfigError,
    config_to_dict,
    get_config_path,
    load_config,
)


# ---- fixture helpers ----------------------------------------------------


def _write_config(home: Path, toml: str) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    path = home / "config.toml"
    path.write_text(toml, encoding="utf-8")
    return path


# ---- defaults / empty state --------------------------------------------


def test_missing_config_returns_defaults(tmp_path: Path) -> None:
    loaded = load_config(home=tmp_path, env={})
    assert loaded.config_exists is False
    assert loaded.config.schema_version == 2
    assert loaded.config.active_profile == ""
    assert loaded.config.profile_names == []
    assert loaded.config.reviewer.provider == "minimax"
    assert loaded.config.reviewer.model == ""
    assert loaded.config.compile.backend == "agent:opencode"
    assert loaded.sources["reviewer.provider"] == "default"
    assert loaded.warnings == []


def test_empty_toml_uses_defaults(tmp_path: Path) -> None:
    _write_config(tmp_path, "")
    loaded = load_config(home=tmp_path, env={})
    assert loaded.config_exists is True
    assert loaded.config.reviewer.provider == "minimax"
    assert loaded.sources["reviewer.provider"] == "default"


# ---- reading values from config.toml -----------------------------------


def test_toml_values_applied(tmp_path: Path) -> None:
    _write_config(tmp_path, """
schema_version = 1

[reviewer]
provider = "openai-compatible"
model = "gpt-4.1-mini"
base_url = "https://api.openai.com/v1/chat/completions"
timeout_seconds = 60
max_retries = 3

[compile]
backend = "script"
allow_fallback = false

[compile.opencode]
model = "gpt-5"

[scheduler]
backend = "script"
interval_seconds = 600

[log]
retention_days = 7
""")
    loaded = load_config(home=tmp_path, env={})
    c = loaded.config
    assert c.reviewer.provider == "openai-compatible"
    assert c.reviewer.model == "gpt-4.1-mini"
    assert c.reviewer.timeout_seconds == 60.0
    assert c.reviewer.max_retries == 3
    assert c.compile.backend == "script"
    assert c.compile.allow_fallback is False
    assert c.compile.opencode.model == "gpt-5"
    assert c.scheduler.backend == "script"
    assert c.scheduler.interval_seconds == 600
    assert c.log.retention_days == 7
    # Sources reflect toml
    assert loaded.sources["reviewer.provider"] == "config.toml"
    assert loaded.sources["compile.allow_fallback"] == "config.toml"


# ---- env var precedence -------------------------------------------------


def test_new_env_var_overrides_toml(tmp_path: Path) -> None:
    _write_config(tmp_path, """
[reviewer]
provider = "minimax"
model = "MiniMax-M2.7"
""")
    env = {"CODEX_SELF_EVOLUTION_REVIEWER_MODEL": "MiniMax-Text-01"}
    loaded = load_config(home=tmp_path, env=env)
    assert loaded.config.reviewer.model == "MiniMax-Text-01"
    assert loaded.sources["reviewer.model"] == "env:CODEX_SELF_EVOLUTION_REVIEWER_MODEL"


def test_legacy_provider_scoped_env_var_overrides_toml(tmp_path: Path) -> None:
    """``MINIMAX_REVIEW_MODEL`` applies only when provider is minimax."""
    _write_config(tmp_path, """
[reviewer]
provider = "minimax"
model = "MiniMax-M2.7"
""")
    env = {"MINIMAX_REVIEW_MODEL": "MiniMax-Text-01"}
    loaded = load_config(home=tmp_path, env=env)
    assert loaded.config.reviewer.model == "MiniMax-Text-01"
    assert loaded.sources["reviewer.model"] == "env:MINIMAX_REVIEW_MODEL (legacy)"


def test_legacy_env_var_does_not_bleed_across_providers(tmp_path: Path) -> None:
    """``OPENAI_REVIEW_MODEL`` must not leak into the minimax path."""
    _write_config(tmp_path, """
[reviewer]
provider = "minimax"
model = "MiniMax-default"
""")
    env = {"OPENAI_REVIEW_MODEL": "gpt-4"}  # irrelevant for provider=minimax
    loaded = load_config(home=tmp_path, env=env)
    assert loaded.config.reviewer.model == "MiniMax-default"
    assert loaded.sources["reviewer.model"] == "config.toml"


def test_new_env_beats_legacy_env(tmp_path: Path) -> None:
    """If both new and legacy env vars are set, new wins."""
    _write_config(tmp_path, "[reviewer]\nprovider = \"minimax\"\n")
    env = {
        "CODEX_SELF_EVOLUTION_REVIEWER_MODEL": "new",
        "MINIMAX_REVIEW_MODEL": "legacy",
    }
    loaded = load_config(home=tmp_path, env=env)
    assert loaded.config.reviewer.model == "new"
    assert loaded.sources["reviewer.model"] == "env:CODEX_SELF_EVOLUTION_REVIEWER_MODEL"


def test_legacy_base_url_resolves_per_provider(tmp_path: Path) -> None:
    _write_config(tmp_path, "[reviewer]\nprovider = \"openai-compatible\"\n")
    env = {"OPENAI_BASE_URL": "https://api.deepseek.com/v1"}
    loaded = load_config(home=tmp_path, env=env)
    assert loaded.config.reviewer.base_url == "https://api.deepseek.com/v1"
    assert loaded.sources["reviewer.base_url"] == "env:OPENAI_BASE_URL (legacy)"


# ---- validator enforcement ---------------------------------------------


def test_invalid_provider_falls_back_to_default(tmp_path: Path) -> None:
    """A bogus provider in TOML must not crash the loader; defaults take over
    so the rest of config.toml still loads and the user can fix via
    ``config validate``."""
    _write_config(tmp_path, "[reviewer]\nprovider = \"not-a-real-provider\"\n")
    loaded = load_config(home=tmp_path, env={})
    assert loaded.config.reviewer.provider == "minimax"  # default
    assert "default" in loaded.sources["reviewer.provider"]


def test_invalid_payload_mode_falls_back(tmp_path: Path) -> None:
    _write_config(tmp_path, """
[reviewer.subprocess]
payload_mode = "magic"
""")
    loaded = load_config(home=tmp_path, env={})
    assert loaded.config.reviewer.subprocess.payload_mode == "stdin"  # default


# ---- schema version handling -------------------------------------------


def test_future_schema_version_raises(tmp_path: Path) -> None:
    _write_config(tmp_path, "schema_version = 99\n")
    with pytest.raises(ConfigError) as exc:
        load_config(home=tmp_path, env={})
    assert "newer" in str(exc.value).lower()


def test_missing_schema_version_assumed_current(tmp_path: Path) -> None:
    _write_config(tmp_path, "[reviewer]\nprovider = \"minimax\"\n")
    loaded = load_config(home=tmp_path, env={})
    # When schema_version is absent we assume the current major; legacy
    # [reviewer] block still works via the silent lift into a "default" profile.
    assert loaded.config.schema_version == 2
    assert loaded.config.reviewer.provider == "minimax"


# ---- toml parse errors -------------------------------------------------


def test_malformed_toml_raises(tmp_path: Path) -> None:
    _write_config(tmp_path, "[reviewer\nprovider = 'unclosed")
    with pytest.raises(ConfigError):
        load_config(home=tmp_path, env={})


# ---- linting warnings ---------------------------------------------------


def test_api_key_lookalike_field_warns(tmp_path: Path) -> None:
    """Putting ``api_key = "..."`` in config.toml should fire a warning but
    not prevent load — warnings are advisory."""
    _write_config(tmp_path, """
[reviewer]
provider = "minimax"
api_key = "sk-leaked-in-config"
""")
    loaded = load_config(home=tmp_path, env={})
    assert any("looks like an API key" in w for w in loaded.warnings)
    # Still loads the rest
    assert loaded.config.reviewer.provider == "minimax"


def test_unknown_key_warns(tmp_path: Path) -> None:
    """Typos like ``modle`` instead of ``model`` should be flagged."""
    _write_config(tmp_path, """
[reviewer]
modle = "MiniMax-M2.7"
""")
    loaded = load_config(home=tmp_path, env={})
    assert any("unknown key" in w for w in loaded.warnings)


# ---- subprocess section -------------------------------------------------


def test_subprocess_command_list_preserved(tmp_path: Path) -> None:
    _write_config(tmp_path, """
[reviewer.subprocess]
command = ["claude", "--output-format", "json"]
""")
    loaded = load_config(home=tmp_path, env={})
    assert loaded.config.reviewer.subprocess.command == ["claude", "--output-format", "json"]
    assert loaded.sources["reviewer.subprocess.command"] == "config.toml"


def test_subprocess_command_empty_array_is_provider_default(tmp_path: Path) -> None:
    _write_config(tmp_path, """
[reviewer.subprocess]
command = []
""")
    loaded = load_config(home=tmp_path, env={})
    assert loaded.config.reviewer.subprocess.command == []
    assert loaded.sources["reviewer.subprocess.command"] == "provider_default"


def test_subprocess_command_string_rejected_with_warning(tmp_path: Path) -> None:
    _write_config(tmp_path, """
[reviewer.subprocess]
command = "codex exec --json"
""")
    loaded = load_config(home=tmp_path, env={})
    assert any("must be an array" in w for w in loaded.warnings)
    assert loaded.config.reviewer.subprocess.command == []


# ---- retry_backoff validation -----------------------------------------


def test_retry_backoff_list_applied(tmp_path: Path) -> None:
    _write_config(tmp_path, """
[reviewer]
retry_backoff = [1.5, 3.0, 7.5]
""")
    loaded = load_config(home=tmp_path, env={})
    assert loaded.config.reviewer.retry_backoff == [1.5, 3.0, 7.5]


def test_retry_backoff_bad_entries_revert_to_default(tmp_path: Path) -> None:
    _write_config(tmp_path, """
[reviewer]
retry_backoff = ["not-a-number", 3.0]
""")
    loaded = load_config(home=tmp_path, env={})
    assert loaded.config.reviewer.retry_backoff == [2.0, 5.0]  # default
    assert any("retry_backoff" in w for w in loaded.warnings)


# ---- config_to_dict ----------------------------------------------------


def test_config_to_dict_serializes_whole_tree(tmp_path: Path) -> None:
    loaded = load_config(home=tmp_path, env={})
    data = config_to_dict(loaded.config)
    # Every section expected by config show is present (schema 2 adds
    # active_profile + profile_names).
    assert set(data.keys()) == {
        "schema_version", "active_profile", "profile_names",
        "reviewer", "compile", "scheduler", "log",
    }
    assert "subprocess" in data["reviewer"]
    assert "opencode" in data["compile"]


# ---- allowed providers list stays in sync ------------------------------


def test_allowed_providers_covers_design_v2() -> None:
    """Design v2 §4.1 lists these six providers. If code removes one
    without a matching design update, this test catches it."""
    assert ALLOWED_PROVIDERS == {
        "minimax",
        "openai-compatible",
        "anthropic-style",
        "codex-cli",
        "opencode-cli",
        "dummy",
    }


# ---- get_config_path respects overrides --------------------------------


def test_get_config_path_uses_override(tmp_path: Path) -> None:
    assert get_config_path(tmp_path) == tmp_path / "config.toml"


def test_get_config_path_defaults_to_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    assert get_config_path() == tmp_path / "config.toml"


# ---- schema 2 profiles -------------------------------------------------


def test_schema2_profiles_with_explicit_active(tmp_path: Path) -> None:
    _write_config(tmp_path, """
schema_version = 2
active_profile = "glm"

[profiles.glm]
provider = "anthropic-style"
model = "glm-5"
base_url = "https://open.bigmodel.cn/api/anthropic/v1/messages"

[profiles.minimax]
provider = "minimax"
""")
    loaded = load_config(home=tmp_path, env={})
    assert loaded.config.active_profile == "glm"
    assert loaded.config.profile_names == ["glm", "minimax"]
    assert loaded.config.reviewer.provider == "anthropic-style"
    assert loaded.config.reviewer.model == "glm-5"
    # Source labels point at the specific profile.
    assert loaded.sources["reviewer.provider"] == "profile:glm"
    assert loaded.sources["reviewer.model"] == "profile:glm"
    assert loaded.sources["active_profile"] == "config.toml"
    assert loaded.warnings == []


def test_schema2_profiles_switching_active_changes_reviewer(tmp_path: Path) -> None:
    """Flip active_profile and the resolved reviewer changes with zero
    other edits — the whole point of profiles."""
    base = """
schema_version = 2
active_profile = "{active}"

[profiles.glm]
provider = "anthropic-style"
model = "glm-5"

[profiles.deepseek]
provider = "openai-compatible"
model = "deepseek-chat"
base_url = "https://api.deepseek.com/v1"
"""
    _write_config(tmp_path, base.format(active="glm"))
    loaded = load_config(home=tmp_path, env={})
    assert loaded.config.reviewer.provider == "anthropic-style"

    _write_config(tmp_path, base.format(active="deepseek"))
    loaded = load_config(home=tmp_path, env={})
    assert loaded.config.reviewer.provider == "openai-compatible"
    assert loaded.config.reviewer.base_url == "https://api.deepseek.com/v1"


def test_schema2_nonexistent_active_profile_warns_and_falls_back(tmp_path: Path) -> None:
    _write_config(tmp_path, """
schema_version = 2
active_profile = "nope"

[profiles.glm]
provider = "anthropic-style"
""")
    loaded = load_config(home=tmp_path, env={})
    assert loaded.config.active_profile == ""
    assert loaded.config.reviewer.provider == "minimax"  # back to default
    assert any("does not match any" in w for w in loaded.warnings)


def test_schema2_single_profile_auto_selected_when_active_missing(tmp_path: Path) -> None:
    """If only one profile is defined and active_profile isn't set, use it.
    Spares users the typo-prone extra line when they have one provider."""
    _write_config(tmp_path, """
schema_version = 2

[profiles.glm]
provider = "anthropic-style"
model = "glm-5"
""")
    loaded = load_config(home=tmp_path, env={})
    assert loaded.config.active_profile == "glm"
    assert loaded.config.reviewer.provider == "anthropic-style"


def test_schema2_multiple_profiles_no_active_picks_default_if_present(tmp_path: Path) -> None:
    _write_config(tmp_path, """
schema_version = 2

[profiles.glm]
provider = "anthropic-style"

[profiles.default]
provider = "minimax"
""")
    loaded = load_config(home=tmp_path, env={})
    assert loaded.config.active_profile == "default"
    assert loaded.config.reviewer.provider == "minimax"


def test_schema2_profile_field_typo_warns(tmp_path: Path) -> None:
    _write_config(tmp_path, """
schema_version = 2
active_profile = "glm"

[profiles.glm]
provider = "anthropic-style"
modle = "glm-5"
""")
    loaded = load_config(home=tmp_path, env={})
    assert any("modle" in w for w in loaded.warnings)


def test_schema1_legacy_reviewer_block_still_loads_with_deprecation_warning(tmp_path: Path) -> None:
    _write_config(tmp_path, """
schema_version = 1
[reviewer]
provider = "minimax"
model = "MiniMax-M2.7"
""")
    loaded = load_config(home=tmp_path, env={})
    assert loaded.config.reviewer.provider == "minimax"
    assert loaded.config.reviewer.model == "MiniMax-M2.7"
    # Synthetic "default" profile was lifted from [reviewer].
    assert loaded.config.active_profile == "default"
    # Source should still say config.toml (not profile:default) because
    # the user didn't actually write [profiles.default].
    assert loaded.sources["reviewer.provider"] == "config.toml"
    assert any("deprecated" in w for w in loaded.warnings)


def test_env_var_overrides_active_profile_value(tmp_path: Path) -> None:
    """New-style env vars still win over the active profile's value — they
    are runtime overrides for experimentation without editing config.toml."""
    _write_config(tmp_path, """
schema_version = 2
active_profile = "glm"

[profiles.glm]
provider = "anthropic-style"
model = "glm-5"
""")
    loaded = load_config(home=tmp_path, env={"CODEX_SELF_EVOLUTION_REVIEWER_MODEL": "glm-4.5"})
    assert loaded.config.reviewer.model == "glm-4.5"
    assert loaded.sources["reviewer.model"] == "env:CODEX_SELF_EVOLUTION_REVIEWER_MODEL"

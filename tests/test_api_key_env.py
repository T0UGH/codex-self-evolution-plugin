"""Per-profile ``api_key_env`` override.

Two anthropic-style profiles (GLM + Kimi) both speak the same dialect but
need distinct keys. Without this override they'd fight over the shared
``ANTHROPIC_API_KEY`` slot and switching profiles means also rewriting
``.env.provider``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from codex_self_evolution.config_file import load_config
from codex_self_evolution.review.providers import (
    HTTPReviewProvider,
    ReviewProviderError,
    build_review_provider_from_config,
)


def _write_config(home: Path, toml: str) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    path = home / "config.toml"
    path.write_text(toml, encoding="utf-8")
    return path


def test_loader_reads_api_key_env_from_profile(tmp_path: Path) -> None:
    _write_config(tmp_path, """
schema_version = 2
active_profile = "kimi"

[profiles.kimi]
provider = "anthropic-style"
api_key_env = "KIMI_API_KEY"
""")
    loaded = load_config(home=tmp_path, env={})
    assert loaded.config.reviewer.api_key_env == "KIMI_API_KEY"
    assert loaded.sources["reviewer.api_key_env"] == "profile:kimi"


def test_loader_defaults_api_key_env_empty(tmp_path: Path) -> None:
    _write_config(tmp_path, """
schema_version = 2
active_profile = "minimax"

[profiles.minimax]
provider = "minimax"
""")
    loaded = load_config(home=tmp_path, env={})
    assert loaded.config.reviewer.api_key_env == ""
    assert loaded.sources["reviewer.api_key_env"] == "default"


def test_http_provider_reads_override_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``api_key_env`` is set, the provider reads from that env var
    rather than the dialect default."""
    monkeypatch.setenv("KIMI_API_KEY", "kimi-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    provider = HTTPReviewProvider(
        name="anthropic-style", dialect="anthropic", api_key_env="KIMI_API_KEY"
    )
    assert provider.resolve_api_key({}) == "kimi-secret"


def test_http_provider_falls_back_to_dialect_default_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``api_key_env`` = backward-compatible behaviour."""
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    provider = HTTPReviewProvider(name="anthropic-style", dialect="anthropic")
    assert provider.resolve_api_key({}) == "anthropic-secret"


def test_http_provider_missing_override_env_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit override without a value must not silently fall back to
    ``ANTHROPIC_API_KEY`` — that would mask misconfiguration."""
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    provider = HTTPReviewProvider(
        name="anthropic-style", dialect="anthropic", api_key_env="KIMI_API_KEY"
    )
    with pytest.raises(ReviewProviderError) as exc:
        provider.resolve_api_key({})
    assert "KIMI_API_KEY" in str(exc.value)


def test_build_from_config_injects_api_key_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: config.toml → load_config → build_review_provider_from_config."""
    _write_config(tmp_path, """
schema_version = 2
active_profile = "glm"

[profiles.glm]
provider = "anthropic-style"
api_key_env = "ZHIPU_API_KEY"
""")
    monkeypatch.setenv("ZHIPU_API_KEY", "zhipu-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    loaded = load_config(home=tmp_path, env={})
    provider = build_review_provider_from_config("anthropic-style", loaded.config)
    assert isinstance(provider, HTTPReviewProvider)
    assert provider.api_key_env == "ZHIPU_API_KEY"
    assert provider.resolve_api_key({}) == "zhipu-secret"


def test_explicit_options_api_key_still_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``options['api_key']`` beats both override and dialect default."""
    monkeypatch.setenv("KIMI_API_KEY", "env-secret")
    provider = HTTPReviewProvider(
        name="anthropic-style", dialect="anthropic", api_key_env="KIMI_API_KEY"
    )
    assert provider.resolve_api_key({"api_key": "inline-secret"}) == "inline-secret"

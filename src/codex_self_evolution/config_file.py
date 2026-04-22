"""Single source of truth for plugin behavior configuration.

Design doc: ``docs/design_v2.md``. In short:

- API keys live in ``.env.provider`` (environment variables). Only secrets.
- Everything else — provider selection, models, timeouts, retry policy,
  subprocess commands — lives here in ``~/.codex-self-evolution/config.toml``.
- Legacy environment variables (``MINIMAX_REVIEW_MODEL`` etc.) continue to
  work as overrides with clear precedence (see :func:`load_config`).

Why TOML: ``tomllib`` ships in the Python 3.11+ stdlib. Adding PyYAML would
have broken the plugin's "zero runtime dependencies" promise.

Why per-field ``sources`` tracking: ``config show`` has to answer the
question "where did this value come from?" for every field — otherwise
users can't debug a misconfigured install short of reading three files.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Mapping


# ---- Dataclasses --------------------------------------------------------


@dataclass
class SubprocessReviewerConfig:
    """Config for ``reviewer.provider`` = codex-cli / opencode-cli / custom."""

    command: list[str] = field(default_factory=list)
    payload_mode: str = "stdin"  # "stdin" | "file" | "inline"
    response_format: str = "codex-events"  # "codex-events" | "opencode-events" | "raw-json"
    timeout_seconds: float = 90.0


@dataclass
class ReviewerConfig:
    """Stop-hook background reviewer configuration."""

    provider: str = "minimax"
    model: str = ""
    base_url: str = ""
    timeout_seconds: float = 30.0
    max_tokens: int = 4096
    max_retries: int = 2
    retry_backoff: list[float] = field(default_factory=lambda: [2.0, 5.0])
    subprocess: SubprocessReviewerConfig = field(default_factory=SubprocessReviewerConfig)


@dataclass
class OpencodeCompileConfig:
    model: str = ""
    agent: str = ""
    timeout_seconds: float = 900.0


@dataclass
class CompileConfig:
    backend: str = "agent:opencode"
    allow_fallback: bool = True
    opencode: OpencodeCompileConfig = field(default_factory=OpencodeCompileConfig)


@dataclass
class SchedulerConfig:
    backend: str = "agent:opencode"
    interval_seconds: int = 300


@dataclass
class LogConfig:
    retention_days: int = 14


@dataclass
class PluginConfig:
    schema_version: int = 2
    # Which ``[profiles.X]`` is currently active. Empty string = no profiles
    # defined (fresh install; use ReviewerConfig defaults).
    active_profile: str = ""
    # Resolved reviewer config for the active profile (merged with env).
    reviewer: ReviewerConfig = field(default_factory=ReviewerConfig)
    # The full set of profile names defined in config.toml, so `config
    # list-profiles` can show them without re-reading the file.
    profile_names: list[str] = field(default_factory=list)
    compile: CompileConfig = field(default_factory=CompileConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    log: LogConfig = field(default_factory=LogConfig)


@dataclass
class LoadResult:
    """What :func:`load_config` returns.

    ``config`` is the resolved, typed dataclass tree.
    ``sources`` maps dotted field paths ("reviewer.model") to source labels
    ("config.toml" / "env:MINIMAX_REVIEW_MODEL (legacy)" / "default").
    ``warnings`` collects non-fatal issues the loader wants to surface
    (unknown keys, api-key-shaped fields, schema version drift).
    ``config_path`` is the file we tried to read, whether or not it exists.
    ``config_exists`` is True when we actually parsed a file.
    """

    config: PluginConfig
    sources: dict[str, str]
    warnings: list[str]
    config_path: Path
    config_exists: bool


# ---- Constants ----------------------------------------------------------


SUPPORTED_SCHEMA_VERSION = 2

# The loader still accepts schema_version = 1 (0.6.0 format with [reviewer]
# at the top level) by silently lifting it into a single "default" profile.
# Schema 1 itself is deprecated — users see a warning pointing at
# ``config migrate-to-v2``.
MIN_SUPPORTED_SCHEMA_VERSION = 1

ALLOWED_PROVIDERS = {
    "minimax",
    "openai-compatible",
    "anthropic-style",
    "codex-cli",
    "opencode-cli",
    "dummy",
}
ALLOWED_PAYLOAD_MODES = {"stdin", "file", "inline"}
ALLOWED_RESPONSE_FORMATS = {"codex-events", "opencode-events", "raw-json"}
ALLOWED_COMPILE_BACKENDS = {"script", "agent:opencode"}

# Map new-style CODEX_SELF_EVOLUTION_* env vars to dotted config paths.
_NEW_ENV_MAP: dict[str, str] = {
    "CODEX_SELF_EVOLUTION_REVIEWER_PROVIDER": "reviewer.provider",
    "CODEX_SELF_EVOLUTION_REVIEWER_MODEL": "reviewer.model",
    "CODEX_SELF_EVOLUTION_REVIEWER_BASE_URL": "reviewer.base_url",
    "CODEX_SELF_EVOLUTION_REVIEWER_TIMEOUT": "reviewer.timeout_seconds",
    "CODEX_SELF_EVOLUTION_COMPILE_BACKEND": "compile.backend",
    "CODEX_SELF_EVOLUTION_OPENCODE_MODEL": "compile.opencode.model",
    "CODEX_SELF_EVOLUTION_OPENCODE_AGENT": "compile.opencode.agent",
}

# Legacy env vars that only apply when reviewer.provider matches. Preserved
# for backward compat with 0.5.x users who set them directly.
_LEGACY_PROVIDER_SCOPED: dict[str, dict[str, str]] = {
    "minimax": {
        "MINIMAX_REVIEW_MODEL": "reviewer.model",
        "MINIMAX_BASE_URL": "reviewer.base_url",
    },
    "openai-compatible": {
        "OPENAI_REVIEW_MODEL": "reviewer.model",
        "OPENAI_BASE_URL": "reviewer.base_url",
    },
    "anthropic-style": {
        "ANTHROPIC_REVIEW_MODEL": "reviewer.model",
        "ANTHROPIC_BASE_URL": "reviewer.base_url",
    },
}

# Names that look like API keys. If found under config.toml we warn — keys
# belong in .env.provider, config.toml gets printed by `config show` and
# may be committed to dotfiles.
_KEY_LOOKALIKE_RE = re.compile(r"(?:^|_)(api[_-]?key|token|secret|password|bearer)$", re.IGNORECASE)


# ---- Public API ---------------------------------------------------------


class ConfigError(ValueError):
    """Raised for fatal config problems (unreadable TOML, unknown schema)."""


def get_config_path(home: Path | None = None) -> Path:
    from .config import get_home_dir

    home_dir = Path(home).expanduser().resolve() if home else get_home_dir()
    return home_dir / "config.toml"


def load_config(
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> LoadResult:
    """Resolve final configuration, tracking source of every value.

    Merge order (first non-empty wins):

    1. New-style ``CODEX_SELF_EVOLUTION_*`` env vars
    2. Provider-scoped legacy env vars (e.g. ``MINIMAX_REVIEW_MODEL`` when
       ``reviewer.provider == "minimax"``)
    3. ``config.toml`` values
    4. Dataclass defaults

    Missing / empty string / empty list values are skipped so a partial
    config.toml still benefits from env overrides.
    """
    env_map: Mapping[str, str] = env if env is not None else os.environ
    config_path = get_config_path(home)
    warnings: list[str] = []

    raw_toml: dict[str, Any] = {}
    config_exists = False
    if config_path.is_file():
        try:
            raw_toml = tomllib.loads(config_path.read_text(encoding="utf-8"))
            config_exists = True
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"failed to parse {config_path}: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"failed to read {config_path}: {exc}") from exc

    schema_version = raw_toml.get("schema_version", SUPPORTED_SCHEMA_VERSION)
    if not isinstance(schema_version, int):
        raise ConfigError(
            f"{config_path}: schema_version must be an integer, got {schema_version!r}"
        )
    if schema_version > SUPPORTED_SCHEMA_VERSION:
        raise ConfigError(
            f"{config_path}: schema_version={schema_version} is newer than "
            f"this plugin understands ({SUPPORTED_SCHEMA_VERSION}). Upgrade the plugin."
        )
    if schema_version < MIN_SUPPORTED_SCHEMA_VERSION:
        raise ConfigError(
            f"{config_path}: schema_version={schema_version} is too old "
            f"(min supported: {MIN_SUPPORTED_SCHEMA_VERSION})."
        )

    # Detect API-key-lookalike fields in TOML and warn (don't block).
    warnings.extend(_lint_no_keys_in_config(raw_toml))
    # Detect TOML keys we don't recognize.
    warnings.extend(_lint_unknown_keys(raw_toml))

    config = PluginConfig()
    sources: dict[str, str] = {}

    # schema_version is a constant for now; still record source.
    config.schema_version = int(schema_version) if config_exists and "schema_version" in raw_toml else SUPPORTED_SCHEMA_VERSION
    sources["schema_version"] = "config.toml" if ("schema_version" in raw_toml) else "default"

    # --- profile resolution ---
    # Schema 2 is profile-first: [profiles.X] sections + active_profile at top.
    # Schema 1 (legacy, 0.6.0) had [reviewer] at top level; we silently lift
    # it into a synthetic ``default`` profile so existing installs keep
    # loading. Users get a deprecation warning pointing to ``config
    # migrate-to-v2``.
    profiles_tree = raw_toml.get("profiles", {}) or {}
    if not isinstance(profiles_tree, dict):
        warnings.append("[profiles] must be a table of profile sections; ignored")
        profiles_tree = {}
    # Snapshot explicitly-declared profile names before we lift legacy
    # [reviewer] into a synthetic ``default`` profile — source labelling
    # uses this to distinguish "real profile" from "legacy fallback".
    explicit_profile_names = set(profiles_tree.keys())

    legacy_reviewer = raw_toml.get("reviewer", {}) or {}
    if legacy_reviewer and isinstance(legacy_reviewer, dict):
        if schema_version >= 2:
            warnings.append(
                "[reviewer] at top level is deprecated in schema_version=2; "
                "move fields into [profiles.<name>] and set active_profile"
            )
        else:
            warnings.append(
                "schema_version=1 is deprecated; run `config migrate-to-v2` to "
                "convert the [reviewer] block into a [profiles.default] section"
            )
        # If we didn't already have a "default" profile defined, create one
        # from [reviewer] so the rest of the loader has a profile to use.
        if "default" not in profiles_tree:
            profiles_tree = {"default": legacy_reviewer, **profiles_tree}

    active_profile = raw_toml.get("active_profile")
    if active_profile is not None and not isinstance(active_profile, str):
        warnings.append(
            f"active_profile must be a string, got {type(active_profile).__name__}; ignored"
        )
        active_profile = None
    active_profile = (active_profile or "").strip()

    # Auto-pick an active profile when none was declared:
    # - Exactly one profile defined → use it
    # - Otherwise, prefer "default" if present
    # - Else leave empty (fall back to dataclass defaults)
    if not active_profile:
        if len(profiles_tree) == 1:
            active_profile = next(iter(profiles_tree))
        elif "default" in profiles_tree:
            active_profile = "default"

    if active_profile and active_profile not in profiles_tree:
        warnings.append(
            f"active_profile='{active_profile}' does not match any [profiles.*] "
            "section; falling back to built-in defaults"
        )
        active_profile = ""

    config.active_profile = active_profile
    config.profile_names = sorted(profiles_tree.keys())
    sources["active_profile"] = (
        "config.toml" if "active_profile" in raw_toml else
        ("config.toml (auto)" if active_profile else "default")
    )

    reviewer_toml = profiles_tree.get(active_profile, {}) if active_profile else {}
    provider, provider_source = _resolve(
        field_path="reviewer.provider",
        new_env=_NEW_ENV_MAP.get("CODEX_SELF_EVOLUTION_REVIEWER_PROVIDER"),
        env_map=env_map,
        toml_value=reviewer_toml.get("provider"),
        default=config.reviewer.provider,
        validator=_validate_provider,
    )
    config.reviewer.provider = provider
    sources["reviewer.provider"] = provider_source

    # model / base_url: accept new-style env var first, then legacy provider-
    # scoped env var (only when provider matches), then TOML, then default.
    legacy_for_provider = _LEGACY_PROVIDER_SCOPED.get(provider, {})

    model, model_source = _resolve(
        field_path="reviewer.model",
        new_env="CODEX_SELF_EVOLUTION_REVIEWER_MODEL",
        env_map=env_map,
        legacy_env_candidates=[name for name, path in legacy_for_provider.items()
                               if path == "reviewer.model"],
        toml_value=reviewer_toml.get("model"),
        default=config.reviewer.model,
    )
    config.reviewer.model = model
    sources["reviewer.model"] = model_source

    base_url, base_url_source = _resolve(
        field_path="reviewer.base_url",
        new_env="CODEX_SELF_EVOLUTION_REVIEWER_BASE_URL",
        env_map=env_map,
        legacy_env_candidates=[name for name, path in legacy_for_provider.items()
                               if path == "reviewer.base_url"],
        toml_value=reviewer_toml.get("base_url"),
        default=config.reviewer.base_url,
    )
    config.reviewer.base_url = base_url
    sources["reviewer.base_url"] = base_url_source

    config.reviewer.timeout_seconds, sources["reviewer.timeout_seconds"] = _resolve_number(
        "reviewer.timeout_seconds",
        new_env="CODEX_SELF_EVOLUTION_REVIEWER_TIMEOUT",
        env_map=env_map,
        toml_value=reviewer_toml.get("timeout_seconds"),
        default=config.reviewer.timeout_seconds,
        cast=float,
    )
    config.reviewer.max_tokens, sources["reviewer.max_tokens"] = _resolve_number(
        "reviewer.max_tokens",
        new_env=None,
        env_map=env_map,
        toml_value=reviewer_toml.get("max_tokens"),
        default=config.reviewer.max_tokens,
        cast=int,
    )
    config.reviewer.max_retries, sources["reviewer.max_retries"] = _resolve_number(
        "reviewer.max_retries",
        new_env=None,
        env_map=env_map,
        toml_value=reviewer_toml.get("max_retries"),
        default=config.reviewer.max_retries,
        cast=int,
    )

    retry_backoff = reviewer_toml.get("retry_backoff")
    if isinstance(retry_backoff, list) and retry_backoff:
        try:
            config.reviewer.retry_backoff = [float(v) for v in retry_backoff]
            sources["reviewer.retry_backoff"] = "config.toml"
        except (TypeError, ValueError):
            warnings.append("reviewer.retry_backoff contains non-numeric entries; using default")
            sources["reviewer.retry_backoff"] = "default"
    else:
        sources["reviewer.retry_backoff"] = "default"

    # --- reviewer.subprocess ---
    sub_toml = reviewer_toml.get("subprocess", {}) or {}
    sub_command = sub_toml.get("command")
    if isinstance(sub_command, list) and sub_command:
        config.reviewer.subprocess.command = [str(x) for x in sub_command]
        sources["reviewer.subprocess.command"] = "config.toml"
    elif isinstance(sub_command, str):
        warnings.append("reviewer.subprocess.command must be an array, not a string; using default")
        sources["reviewer.subprocess.command"] = "default"
    else:
        sources["reviewer.subprocess.command"] = "provider_default"

    config.reviewer.subprocess.payload_mode, sources["reviewer.subprocess.payload_mode"] = _resolve(
        field_path="reviewer.subprocess.payload_mode",
        new_env=None,
        env_map=env_map,
        toml_value=sub_toml.get("payload_mode"),
        default=config.reviewer.subprocess.payload_mode,
        validator=lambda v: v in ALLOWED_PAYLOAD_MODES,
    )
    config.reviewer.subprocess.response_format, sources["reviewer.subprocess.response_format"] = _resolve(
        field_path="reviewer.subprocess.response_format",
        new_env=None,
        env_map=env_map,
        toml_value=sub_toml.get("response_format"),
        default=config.reviewer.subprocess.response_format,
        validator=lambda v: v in ALLOWED_RESPONSE_FORMATS,
    )
    config.reviewer.subprocess.timeout_seconds, sources["reviewer.subprocess.timeout_seconds"] = _resolve_number(
        "reviewer.subprocess.timeout_seconds",
        new_env=None,
        env_map=env_map,
        toml_value=sub_toml.get("timeout_seconds"),
        default=config.reviewer.subprocess.timeout_seconds,
        cast=float,
    )

    # --- compile ---
    compile_toml = raw_toml.get("compile", {}) or {}
    config.compile.backend, sources["compile.backend"] = _resolve(
        field_path="compile.backend",
        new_env="CODEX_SELF_EVOLUTION_COMPILE_BACKEND",
        env_map=env_map,
        toml_value=compile_toml.get("backend"),
        default=config.compile.backend,
        validator=lambda v: v in ALLOWED_COMPILE_BACKENDS,
    )
    allow_fallback = compile_toml.get("allow_fallback")
    if isinstance(allow_fallback, bool):
        config.compile.allow_fallback = allow_fallback
        sources["compile.allow_fallback"] = "config.toml"
    else:
        sources["compile.allow_fallback"] = "default"

    # --- compile.opencode ---
    opencode_toml = compile_toml.get("opencode", {}) or {}
    config.compile.opencode.model, sources["compile.opencode.model"] = _resolve(
        field_path="compile.opencode.model",
        new_env="CODEX_SELF_EVOLUTION_OPENCODE_MODEL",
        env_map=env_map,
        toml_value=opencode_toml.get("model"),
        default=config.compile.opencode.model,
    )
    config.compile.opencode.agent, sources["compile.opencode.agent"] = _resolve(
        field_path="compile.opencode.agent",
        new_env="CODEX_SELF_EVOLUTION_OPENCODE_AGENT",
        env_map=env_map,
        toml_value=opencode_toml.get("agent"),
        default=config.compile.opencode.agent,
    )
    config.compile.opencode.timeout_seconds, sources["compile.opencode.timeout_seconds"] = _resolve_number(
        "compile.opencode.timeout_seconds",
        new_env=None,
        env_map=env_map,
        toml_value=opencode_toml.get("timeout_seconds"),
        default=config.compile.opencode.timeout_seconds,
        cast=float,
    )

    # --- scheduler ---
    scheduler_toml = raw_toml.get("scheduler", {}) or {}
    config.scheduler.backend, sources["scheduler.backend"] = _resolve(
        field_path="scheduler.backend",
        new_env=None,
        env_map=env_map,
        toml_value=scheduler_toml.get("backend"),
        default=config.scheduler.backend,
        validator=lambda v: v in ALLOWED_COMPILE_BACKENDS,
    )
    config.scheduler.interval_seconds, sources["scheduler.interval_seconds"] = _resolve_number(
        "scheduler.interval_seconds",
        new_env=None,
        env_map=env_map,
        toml_value=scheduler_toml.get("interval_seconds"),
        default=config.scheduler.interval_seconds,
        cast=int,
    )

    # --- log ---
    log_toml = raw_toml.get("log", {}) or {}
    config.log.retention_days, sources["log.retention_days"] = _resolve_number(
        "log.retention_days",
        new_env=None,
        env_map=env_map,
        toml_value=log_toml.get("retention_days"),
        default=config.log.retention_days,
        cast=int,
    )

    # Source label rewrite: when the active profile was *explicitly* declared
    # in config.toml (i.e. not the synthetic "default" we lift from legacy
    # [reviewer]), rewrite source labels from "config.toml" → "profile:<name>"
    # so ``config show`` shows the user exactly which profile a value came
    # from. Synthesized defaults keep the "config.toml" label because the
    # user's file doesn't actually have a [profiles.default] section.
    if active_profile and active_profile in explicit_profile_names:
        for key, value in list(sources.items()):
            if key.startswith("reviewer.") and value == "config.toml":
                sources[key] = f"profile:{active_profile}"

    return LoadResult(
        config=config,
        sources=sources,
        warnings=warnings,
        config_path=config_path,
        config_exists=config_exists,
    )


def config_to_dict(config: PluginConfig) -> dict[str, Any]:
    """Shallow-typed dict view of a PluginConfig tree, for JSON printing."""
    return _dataclass_to_dict(config)


# ---- Internal helpers ---------------------------------------------------


def _validate_provider(value: str) -> bool:
    return value in ALLOWED_PROVIDERS


def _resolve(
    field_path: str,
    new_env: str | None,
    env_map: Mapping[str, str],
    toml_value: Any,
    default: Any,
    legacy_env_candidates: list[str] | None = None,
    validator=None,
) -> tuple[Any, str]:
    """Resolve one string-ish field with source tracking.

    A value is "set" when non-None and non-empty-string. Lists are handled
    separately (see the inline subprocess.command logic) because the
    distinction between "user wrote []" and "field missing" matters there.
    """
    if new_env:
        val = env_map.get(new_env)
        if val not in (None, ""):
            if validator is not None and not validator(val):
                # Invalid value — fall through rather than crash; loader is
                # defensive and upstream code catches impossible states.
                pass
            else:
                return val, f"env:{new_env}"
    if legacy_env_candidates:
        for name in legacy_env_candidates:
            val = env_map.get(name)
            if val not in (None, ""):
                if validator is not None and not validator(val):
                    continue
                return val, f"env:{name} (legacy)"
    if toml_value not in (None, ""):
        if validator is not None and not validator(toml_value):
            return default, "default (toml value invalid)"
        return toml_value, "config.toml"
    return default, "default"


def _resolve_number(
    field_path: str,
    new_env: str | None,
    env_map: Mapping[str, str],
    toml_value: Any,
    default: Any,
    cast,
) -> tuple[Any, str]:
    """Same as :func:`_resolve` but casts numeric types. Invalid casts fall back to default."""
    if new_env:
        val = env_map.get(new_env)
        if val not in (None, ""):
            try:
                return cast(val), f"env:{new_env}"
            except (TypeError, ValueError):
                pass
    if toml_value is not None:
        try:
            return cast(toml_value), "config.toml"
        except (TypeError, ValueError):
            pass
    return default, "default"


def _dataclass_to_dict(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _dataclass_to_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, list):
        return [_dataclass_to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _dataclass_to_dict(v) for k, v in obj.items()}
    return obj


def _lint_no_keys_in_config(toml_tree: dict[str, Any]) -> list[str]:
    warnings: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                key_str = str(key)
                if _KEY_LOOKALIKE_RE.search(key_str):
                    full = f"{path}.{key_str}" if path else key_str
                    warnings.append(
                        f"config.toml[{full}] looks like an API key; keys should "
                        "live in .env.provider, not config.toml"
                    )
                walk(value, f"{path}.{key_str}" if path else key_str)

    walk(toml_tree, "")
    return warnings


# Top-level paths the loader understands. Schema 1 kept [reviewer] here;
# schema 2 also accepts [profiles.*]. ``profiles`` itself is recognized but
# its children are validated via ``_RECOGNIZED_PROFILE_FIELDS``.
_RECOGNIZED_PATHS: frozenset[str] = frozenset([
    "schema_version",
    "active_profile",
    "profiles",
    # Legacy 0.6.0 top-level [reviewer] block, still accepted with a warning.
    "reviewer", "reviewer.provider", "reviewer.model", "reviewer.base_url",
    "reviewer.timeout_seconds", "reviewer.max_tokens", "reviewer.max_retries",
    "reviewer.retry_backoff",
    "reviewer.subprocess", "reviewer.subprocess.command",
    "reviewer.subprocess.payload_mode", "reviewer.subprocess.response_format",
    "reviewer.subprocess.timeout_seconds",
    "compile", "compile.backend", "compile.allow_fallback",
    "compile.opencode", "compile.opencode.model", "compile.opencode.agent",
    "compile.opencode.timeout_seconds",
    "scheduler", "scheduler.backend", "scheduler.interval_seconds",
    "log", "log.retention_days",
])

# Fields allowed inside [profiles.<name>]. Mirrors ReviewerConfig shape —
# the resolver uses this same schema regardless of which profile is active.
_RECOGNIZED_PROFILE_FIELDS: frozenset[str] = frozenset([
    "provider", "model", "base_url", "timeout_seconds", "max_tokens",
    "max_retries", "retry_backoff",
    "subprocess",
    "subprocess.command", "subprocess.payload_mode",
    "subprocess.response_format", "subprocess.timeout_seconds",
])


def _lint_unknown_keys(toml_tree: dict[str, Any]) -> list[str]:
    """Warn on TOML paths we don't recognize — catches typos early.

    Profiles need a special rule: ``[profiles.anything]`` is legal because
    profile names are user-chosen, but the fields *inside* each profile
    must still match the reviewer schema.
    """
    warnings: list[str] = []

    def walk_profile_children(profile_name: str, node: Any) -> None:
        if not isinstance(node, dict):
            warnings.append(
                f"config.toml[profiles.{profile_name}]: expected table, got {type(node).__name__}"
            )
            return
        for key, value in node.items():
            if key not in _RECOGNIZED_PROFILE_FIELDS and key != "subprocess":
                warnings.append(
                    f"config.toml[profiles.{profile_name}.{key}]: unknown field (typo?)"
                )
                continue
            if key == "subprocess" and isinstance(value, dict):
                for sub_key in value.keys():
                    full_sub = f"subprocess.{sub_key}"
                    if full_sub not in _RECOGNIZED_PROFILE_FIELDS:
                        warnings.append(
                            f"config.toml[profiles.{profile_name}.subprocess.{sub_key}]: "
                            "unknown field (typo?)"
                        )

    def walk(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            full = f"{path}.{key}" if path else str(key)
            if path == "" and key == "profiles":
                # Children are profile names (user-chosen); validate their
                # grandchildren against the reviewer schema.
                if isinstance(value, dict):
                    for profile_name, profile_body in value.items():
                        walk_profile_children(str(profile_name), profile_body)
                continue
            if full not in _RECOGNIZED_PATHS:
                warnings.append(f"config.toml[{full}]: unknown key (typo?)")
                continue
            walk(value, full)

    walk(toml_tree, "")
    return warnings

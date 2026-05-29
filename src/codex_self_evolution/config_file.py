"""Single source of truth for retained session-level plugin configuration."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Mapping

from .config import DEFAULT_LOCK_STALE_SECONDS, REFLECT_SKILL_PREFIX


@dataclass
class StableMemoryConfig:
    """SessionStart stable memory injection configuration."""

    # Whether SessionStart reads and injects project MEMORY.md.
    enabled: bool = True


@dataclass
class SessionReflectionTriggerConfig:
    """Deterministic Stop-hook trigger policy configuration."""

    # Whether Stop-hook trigger evaluation is active.
    enabled: bool = True
    # Whether trigger policy may request memory review.
    memory_review: bool = True
    # Whether trigger policy may request skill review.
    skill_review: bool = True
    # Number of Stop events between memory-reflection nudges.
    memory_stop_interval: int = 3
    # Maximum transcript context sent to the memory-reflection decision path.
    memory_context_chars: int = 16000
    # Number of tool calls between skill-generation nudges.
    skill_tool_call_interval: int = 15
    # Whether high-signal events may bypass interval counters.
    high_signal_immediate: bool = True
    # Skill generation policy name consumed by the trigger evaluator.
    skill_generation_mode: str = "one_shot_active"
    # Staleness window for an active reflection job before trigger deferral expires.
    active_job_stale_seconds: int = DEFAULT_LOCK_STALE_SECONDS


@dataclass
class SessionReflectionConfig:
    """Session-level reflection worker configuration."""

    # Whether session reflection can enqueue background jobs.
    enabled: bool = True
    # Reflection backend implementation name.
    backend: str = "codex-app-server"
    # Model used by the child reflection worker.
    model: str = "gpt-5.3-codex-spark"
    # Whether child reflection threads should be ephemeral.
    ephemeral: bool = True
    # Sandbox mode passed to the reflection child thread.
    sandbox: str = "danger-full-access"
    # Approval policy passed to the reflection child thread.
    approval_policy: str = "never"
    # Prefix for generated reflection skills.
    skill_prefix: str = REFLECT_SKILL_PREFIX
    # Per-job timeout in seconds.
    timeout_seconds: float = 900.0
    # Maximum concurrently active reflection jobs.
    max_concurrent_jobs: int = 1
    # Nested deterministic trigger policy.
    trigger: SessionReflectionTriggerConfig = field(default_factory=SessionReflectionTriggerConfig)


@dataclass
class SessionRecallConfig:
    """Session recall archive configuration."""

    # Whether focused recall and archive storage are enabled.
    enabled: bool = True
    # Whether SessionStart injects the recall policy pointer.
    session_start_policy: bool = True
    # Whether manual `csep recall` queries may read the recall store.
    manual_query: bool = True
    # Whether the Stop hook archives transcripts into session recall.
    stop_hook_archive: bool = True


@dataclass
class LogConfig:
    """Plugin log retention configuration."""

    # Number of days to retain plugin logs.
    retention_days: int = 14


@dataclass
class PluginConfig:
    """Resolved runtime configuration for the retained session-level system."""

    # Configuration schema version supported by this loader.
    schema_version: int = 2
    # Stable Memory startup injection configuration.
    stable_memory: StableMemoryConfig = field(default_factory=StableMemoryConfig)
    # Session reflection worker and trigger configuration.
    session_reflection: SessionReflectionConfig = field(default_factory=SessionReflectionConfig)
    # Session recall archive and recall configuration.
    session_recall: SessionRecallConfig = field(default_factory=SessionRecallConfig)
    # Plugin log retention configuration.
    log: LogConfig = field(default_factory=LogConfig)


@dataclass
class LoadResult:
    """Resolved config plus source labels, warnings, and file metadata."""

    # Typed resolved configuration tree.
    config: PluginConfig
    # Dotted field path to source label.
    sources: dict[str, str]
    # Non-fatal lint or compatibility warnings.
    warnings: list[str]
    # File path that was read or would be read.
    config_path: Path
    # Whether a config.toml file existed and parsed.
    config_exists: bool


SUPPORTED_SCHEMA_VERSION = 2

ALLOWED_SESSION_REFLECTION_BACKENDS = {"codex-app-server"}
ALLOWED_SESSION_REFLECTION_SANDBOXES = {"read-only", "workspace-write", "danger-full-access"}
ALLOWED_SESSION_REFLECTION_APPROVAL_POLICIES = {"untrusted", "on-failure", "on-request", "never"}
ALLOWED_SESSION_REFLECTION_TRIGGER_MODES = {"one_shot_active", "evidence_first"}

_KEY_LOOKALIKE_RE = re.compile(r"(?:^|_)(api[_-]?key|token|secret|password|bearer)$", re.IGNORECASE)

_KNOWN_TOP_LEVEL_KEYS = {
    "schema_version",
    "stable_memory",
    "session_reflection",
    "session_recall",
    "log",
}

_KNOWN_PATHS = {
    "schema_version",
    "stable_memory",
    "stable_memory.enabled",
    "session_reflection",
    "session_reflection.enabled",
    "session_reflection.backend",
    "session_reflection.model",
    "session_reflection.ephemeral",
    "session_reflection.sandbox",
    "session_reflection.approval_policy",
    "session_reflection.skill_prefix",
    "session_reflection.timeout_seconds",
    "session_reflection.max_concurrent_jobs",
    "session_reflection.trigger",
    "session_reflection.trigger.enabled",
    "session_reflection.trigger.memory_review",
    "session_reflection.trigger.skill_review",
    "session_reflection.trigger.memory_stop_interval",
    "session_reflection.trigger.memory_context_chars",
    "session_reflection.trigger.skill_tool_call_interval",
    "session_reflection.trigger.high_signal_immediate",
    "session_reflection.trigger.skill_generation_mode",
    "session_reflection.trigger.active_job_stale_seconds",
    "session_recall",
    "session_recall.enabled",
    "session_recall.session_start_policy",
    "session_recall.manual_query",
    "session_recall.stop_hook_archive",
    "log",
    "log.retention_days",
}


class ConfigError(ValueError):
    """Raised for fatal config problems such as unreadable TOML or schema drift."""


def get_config_path(home: Path | None = None) -> Path:
    """Return the config.toml path for a CSEP home directory."""
    from .config import get_home_dir

    home_dir = Path(home).expanduser().resolve() if home else get_home_dir()
    return home_dir / "config.toml"


def load_config(
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> LoadResult:
    """Load the v2 session-level config without legacy compatibility overlays."""
    _ = env
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
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise ConfigError(
            f"{config_path}: schema_version must be an integer, got {schema_version!r}"
        )
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise ConfigError(
            f"{config_path}: schema_version={schema_version} is not supported; "
            f"this plugin only supports schema_version={SUPPORTED_SCHEMA_VERSION}."
        )

    warnings.extend(_lint_no_keys_in_config(raw_toml))
    warnings.extend(_lint_unknown_keys(raw_toml))

    config = PluginConfig(schema_version=schema_version)
    sources = _default_sources(config)
    sources["schema_version"] = "config.toml" if "schema_version" in raw_toml else "default"

    stable_memory_toml = _table(raw_toml, "stable_memory", warnings)
    _apply_bool(
        config.stable_memory,
        "enabled",
        stable_memory_toml,
        "stable_memory.enabled",
        sources,
    )

    reflection_toml = _table(raw_toml, "session_reflection", warnings)
    _apply_bool(
        config.session_reflection,
        "enabled",
        reflection_toml,
        "session_reflection.enabled",
        sources,
    )
    _apply_str(
        config.session_reflection,
        "backend",
        reflection_toml,
        "session_reflection.backend",
        sources,
    )
    _apply_str(
        config.session_reflection,
        "model",
        reflection_toml,
        "session_reflection.model",
        sources,
    )
    _apply_bool(
        config.session_reflection,
        "ephemeral",
        reflection_toml,
        "session_reflection.ephemeral",
        sources,
    )
    _apply_str(
        config.session_reflection,
        "sandbox",
        reflection_toml,
        "session_reflection.sandbox",
        sources,
    )
    _apply_str(
        config.session_reflection,
        "approval_policy",
        reflection_toml,
        "session_reflection.approval_policy",
        sources,
    )
    _apply_str(
        config.session_reflection,
        "skill_prefix",
        reflection_toml,
        "session_reflection.skill_prefix",
        sources,
    )
    _apply_number(
        config.session_reflection,
        "timeout_seconds",
        reflection_toml,
        "session_reflection.timeout_seconds",
        sources,
        warnings,
        cast=float,
        positive=True,
    )
    _apply_number(
        config.session_reflection,
        "max_concurrent_jobs",
        reflection_toml,
        "session_reflection.max_concurrent_jobs",
        sources,
        warnings,
        cast=int,
        positive=True,
    )

    trigger_toml = _table(reflection_toml, "trigger", warnings, prefix="session_reflection")
    _apply_bool(
        config.session_reflection.trigger,
        "enabled",
        trigger_toml,
        "session_reflection.trigger.enabled",
        sources,
    )
    _apply_bool(
        config.session_reflection.trigger,
        "memory_review",
        trigger_toml,
        "session_reflection.trigger.memory_review",
        sources,
    )
    _apply_bool(
        config.session_reflection.trigger,
        "skill_review",
        trigger_toml,
        "session_reflection.trigger.skill_review",
        sources,
    )
    for field_name in (
        "memory_stop_interval",
        "memory_context_chars",
        "skill_tool_call_interval",
        "active_job_stale_seconds",
    ):
        _apply_number(
            config.session_reflection.trigger,
            field_name,
            trigger_toml,
            f"session_reflection.trigger.{field_name}",
            sources,
            warnings,
            cast=int,
            positive=True,
        )
    _apply_bool(
        config.session_reflection.trigger,
        "high_signal_immediate",
        trigger_toml,
        "session_reflection.trigger.high_signal_immediate",
        sources,
    )
    _apply_str(
        config.session_reflection.trigger,
        "skill_generation_mode",
        trigger_toml,
        "session_reflection.trigger.skill_generation_mode",
        sources,
    )

    if config.session_reflection.backend not in ALLOWED_SESSION_REFLECTION_BACKENDS:
        warnings.append(
            "session_reflection.backend must be codex-app-server in v1; "
            f"got {config.session_reflection.backend!r}"
        )
    if config.session_reflection.sandbox not in ALLOWED_SESSION_REFLECTION_SANDBOXES:
        warnings.append(
            "session_reflection.sandbox must be one of "
            f"{sorted(ALLOWED_SESSION_REFLECTION_SANDBOXES)!r}; "
            f"got {config.session_reflection.sandbox!r}"
        )
    if config.session_reflection.approval_policy not in ALLOWED_SESSION_REFLECTION_APPROVAL_POLICIES:
        warnings.append(
            "session_reflection.approval_policy must be one of "
            f"{sorted(ALLOWED_SESSION_REFLECTION_APPROVAL_POLICIES)!r}; "
            f"got {config.session_reflection.approval_policy!r}"
        )
    if not str(config.session_reflection.skill_prefix).startswith(REFLECT_SKILL_PREFIX):
        warnings.append(
            "session_reflection.skill_prefix must start with "
            f"{REFLECT_SKILL_PREFIX!r}; got {config.session_reflection.skill_prefix!r}"
        )
    if (
        config.session_reflection.trigger.skill_generation_mode
        not in ALLOWED_SESSION_REFLECTION_TRIGGER_MODES
    ):
        warnings.append(
            "session_reflection.trigger.skill_generation_mode must be "
            "'one_shot_active' or 'evidence_first'"
        )
        config.session_reflection.trigger.skill_generation_mode = (
            SessionReflectionTriggerConfig().skill_generation_mode
        )
        sources["session_reflection.trigger.skill_generation_mode"] = "default"

    recall_toml = _table(raw_toml, "session_recall", warnings)
    _apply_bool(
        config.session_recall,
        "enabled",
        recall_toml,
        "session_recall.enabled",
        sources,
    )
    _apply_bool(
        config.session_recall,
        "session_start_policy",
        recall_toml,
        "session_recall.session_start_policy",
        sources,
    )
    _apply_bool(
        config.session_recall,
        "manual_query",
        recall_toml,
        "session_recall.manual_query",
        sources,
    )
    _apply_bool(
        config.session_recall,
        "stop_hook_archive",
        recall_toml,
        "session_recall.stop_hook_archive",
        sources,
    )

    log_toml = _table(raw_toml, "log", warnings)
    _apply_number(
        config.log,
        "retention_days",
        log_toml,
        "log.retention_days",
        sources,
        warnings,
        cast=int,
        positive=True,
    )

    return LoadResult(
        config=config,
        sources=sources,
        warnings=warnings,
        config_path=config_path,
        config_exists=config_exists,
    )


def config_to_dict(config: PluginConfig) -> dict[str, Any]:
    """Return a JSON-serializable dict view of the config dataclass tree."""
    return _dataclass_to_dict(config)


def _apply_bool(
    target: Any,
    field_name: str,
    table: dict[str, Any],
    source_path: str,
    sources: dict[str, str],
) -> None:
    """Apply a boolean TOML field when present with the expected type."""
    value = table.get(field_name)
    if isinstance(value, bool):
        setattr(target, field_name, value)
        sources[source_path] = "config.toml"


def _apply_str(
    target: Any,
    field_name: str,
    table: dict[str, Any],
    source_path: str,
    sources: dict[str, str],
) -> None:
    """Apply a string TOML field when present with the expected type."""
    value = table.get(field_name)
    if isinstance(value, str) and value != "":
        setattr(target, field_name, value)
        sources[source_path] = "config.toml"


def _apply_number(
    target: Any,
    field_name: str,
    table: dict[str, Any],
    source_path: str,
    sources: dict[str, str],
    warnings: list[str],
    *,
    cast: type[int] | type[float],
    positive: bool,
) -> None:
    """Apply a numeric TOML field with optional positivity validation."""
    value = table.get(field_name)
    if value is None or isinstance(value, bool):
        return
    try:
        parsed = cast(value)
    except (TypeError, ValueError):
        warnings.append(f"{source_path} must be numeric")
        return
    if positive and parsed <= 0:
        warnings.append(f"{source_path} must be positive")
        return
    setattr(target, field_name, parsed)
    sources[source_path] = "config.toml"


def _table(
    root: dict[str, Any],
    key: str,
    warnings: list[str],
    *,
    prefix: str = "",
) -> dict[str, Any]:
    """Return a TOML table or warn and use an empty table."""
    value = root.get(key, {}) or {}
    if isinstance(value, dict):
        return value
    path = f"{prefix}.{key}" if prefix else key
    warnings.append(f"{path} must be a table")
    return {}


def _default_sources(config: PluginConfig) -> dict[str, str]:
    """Create default source labels for every leaf field in the config tree."""
    sources: dict[str, str] = {}

    def walk(obj: Any, prefix: str) -> None:
        if is_dataclass(obj) and not isinstance(obj, type):
            for item in fields(obj):
                path = f"{prefix}.{item.name}" if prefix else item.name
                value = getattr(obj, item.name)
                if is_dataclass(value) and not isinstance(value, type):
                    walk(value, path)
                else:
                    sources[path] = "default"

    walk(config, "")
    return sources


def _dataclass_to_dict(obj: Any) -> Any:
    """Recursively convert dataclasses, lists, and dicts to plain values."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _dataclass_to_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, list):
        return [_dataclass_to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _dataclass_to_dict(v) for k, v in obj.items()}
    return obj


def _lint_no_keys_in_config(toml_tree: dict[str, Any]) -> list[str]:
    """Warn when config.toml contains key-shaped secret fields."""
    warnings: list[str] = []

    def walk(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            key_str = str(key)
            full = f"{path}.{key_str}" if path else key_str
            if _KEY_LOOKALIKE_RE.search(key_str):
                warnings.append(
                    f"config.toml[{full}] looks like an API key; keys should "
                    "live in .env.provider, not config.toml"
                )
            walk(value, full)

    walk(toml_tree, "")
    return warnings


def _lint_unknown_keys(toml_tree: dict[str, Any]) -> list[str]:
    """Warn on top-level legacy sections and nested retained-section typos."""
    warnings: list[str] = []

    def walk(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            full = f"{path}.{key}" if path else str(key)
            if path == "" and key not in _KNOWN_TOP_LEVEL_KEYS:
                warnings.append(f"unknown top-level config key: {key}")
                continue
            if full not in _KNOWN_PATHS:
                warnings.append(f"config.toml[{full}]: unknown key (typo?)")
                continue
            walk(value, full)

    walk(toml_tree, "")
    return warnings

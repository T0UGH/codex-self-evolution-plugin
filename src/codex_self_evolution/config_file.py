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

from .config import DEFAULT_LOCK_STALE_SECONDS, REFLECT_SKILL_PREFIX


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
    # Name of the env var to read the API key from. Lets two
    # anthropic-style profiles (e.g. GLM + Kimi) each bind to their own key
    # (ZHIPU_API_KEY / KIMI_API_KEY) instead of fighting over the shared
    # ANTHROPIC_API_KEY slot. Empty = fall back to dialect default
    # (MINIMAX_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY).
    api_key_env: str = ""
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
class PiCompileConfig:
    provider: str = "kimi"
    model: str = "kimi-k2.6"
    mode: str = "edit"
    timeout_seconds: float = 900.0


@dataclass
class CompileConfig:
    backend: str = "agent:pi"
    allow_fallback: bool = True
    opencode: OpencodeCompileConfig = field(default_factory=OpencodeCompileConfig)
    pi: PiCompileConfig = field(default_factory=PiCompileConfig)


@dataclass
class SchedulerConfig:
    backend: str = "agent:pi"
    interval_seconds: int = 300


@dataclass
class SkillSynthesisAgentConfig:
    backend: str = "agent:pi"
    provider: str = "minimax"
    model: str = "MiniMax-M2.7"
    timeout_seconds: float = 1800.0


@dataclass
class SkillSynthesisConfig:
    enabled: bool = True
    default_mode: str = "incremental"
    lookback_hours: int = 24
    lookback_days: int = 30
    skills_prefix: str = "csep-synth-"
    agent: SkillSynthesisAgentConfig = field(default_factory=SkillSynthesisAgentConfig)


@dataclass
class SessionReflectionTriggerConfig:
    """Deterministic Stop-hook trigger policy configuration."""

    enabled: bool = True
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

    enabled: bool = True
    backend: str = "codex-app-server"
    model: str = "gpt-5.3-codex-spark"
    ephemeral: bool = True
    sandbox: str = "danger-full-access"
    approval_policy: str = "never"
    skill_prefix: str = REFLECT_SKILL_PREFIX
    timeout_seconds: float = 900.0
    max_concurrent_jobs: int = 1
    replace_stop_reviewer: bool = True
    trigger: SessionReflectionTriggerConfig = field(default_factory=SessionReflectionTriggerConfig)


@dataclass
class SessionRecallConfig:
    """Session recall archive configuration."""

    enabled: bool = True
    stop_hook_archive: bool = True


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
    skill_synthesis: SkillSynthesisConfig = field(default_factory=SkillSynthesisConfig)
    session_reflection: SessionReflectionConfig = field(default_factory=SessionReflectionConfig)
    session_recall: SessionRecallConfig = field(default_factory=SessionRecallConfig)
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
ALLOWED_COMPILE_BACKENDS = {"script", "agent:opencode", "agent:pi"}
ALLOWED_SKILL_SYNTHESIS_BACKENDS = {"agent:pi"}
ALLOWED_SKILL_SYNTHESIS_MODES = {"incremental", "full"}
ALLOWED_SESSION_REFLECTION_BACKENDS = {"codex-app-server"}
ALLOWED_SESSION_REFLECTION_SANDBOXES = {"read-only", "workspace-write", "danger-full-access"}
ALLOWED_SESSION_REFLECTION_APPROVAL_POLICIES = {"untrusted", "on-failure", "on-request", "never"}
ALLOWED_SESSION_REFLECTION_TRIGGER_MODES = {"one_shot_active", "evidence_first"}

# Map new-style CODEX_SELF_EVOLUTION_* env vars to dotted config paths.
_NEW_ENV_MAP: dict[str, str] = {
    "CODEX_SELF_EVOLUTION_REVIEWER_PROVIDER": "reviewer.provider",
    "CODEX_SELF_EVOLUTION_REVIEWER_MODEL": "reviewer.model",
    "CODEX_SELF_EVOLUTION_REVIEWER_BASE_URL": "reviewer.base_url",
    "CODEX_SELF_EVOLUTION_REVIEWER_TIMEOUT": "reviewer.timeout_seconds",
    "CODEX_SELF_EVOLUTION_COMPILE_BACKEND": "compile.backend",
    "CODEX_SELF_EVOLUTION_OPENCODE_MODEL": "compile.opencode.model",
    "CODEX_SELF_EVOLUTION_OPENCODE_AGENT": "compile.opencode.agent",
    "CODEX_SELF_EVOLUTION_PI_PROVIDER": "compile.pi.provider",
    "CODEX_SELF_EVOLUTION_PI_MODEL": "compile.pi.model",
    "CODEX_SELF_EVOLUTION_PI_MODE": "compile.pi.mode",
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

    # api_key_env is opt-in; users leave it blank to keep the dialect
    # default env var. No env override — this is a per-profile declaration.
    api_key_env, api_key_env_source = _resolve(
        field_path="reviewer.api_key_env",
        new_env=None,
        env_map=env_map,
        toml_value=reviewer_toml.get("api_key_env"),
        default=config.reviewer.api_key_env,
    )
    config.reviewer.api_key_env = api_key_env
    sources["reviewer.api_key_env"] = api_key_env_source

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
    pi_toml = compile_toml.get("pi", {}) or {}
    config.compile.pi.provider, sources["compile.pi.provider"] = _resolve(
        field_path="compile.pi.provider",
        new_env="CODEX_SELF_EVOLUTION_PI_PROVIDER",
        env_map=env_map,
        toml_value=pi_toml.get("provider"),
        default=config.compile.pi.provider,
    )
    config.compile.pi.model, sources["compile.pi.model"] = _resolve(
        field_path="compile.pi.model",
        new_env="CODEX_SELF_EVOLUTION_PI_MODEL",
        env_map=env_map,
        toml_value=pi_toml.get("model"),
        default=config.compile.pi.model,
    )
    config.compile.pi.mode, sources["compile.pi.mode"] = _resolve(
        field_path="compile.pi.mode",
        new_env="CODEX_SELF_EVOLUTION_PI_MODE",
        env_map=env_map,
        toml_value=pi_toml.get("mode"),
        default=config.compile.pi.mode,
        validator=lambda v: v in {"edit", "json"},
    )
    config.compile.pi.timeout_seconds, sources["compile.pi.timeout_seconds"] = _resolve_number(
        "compile.pi.timeout_seconds",
        new_env=None,
        env_map=env_map,
        toml_value=pi_toml.get("timeout_seconds"),
        default=config.compile.pi.timeout_seconds,
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

    # --- skill_synthesis ---
    synth_toml = raw_toml.get("skill_synthesis", {}) or {}
    synth_enabled = synth_toml.get("enabled")
    if isinstance(synth_enabled, bool):
        config.skill_synthesis.enabled = synth_enabled
        sources["skill_synthesis.enabled"] = "config.toml"
    else:
        sources["skill_synthesis.enabled"] = "default"

    config.skill_synthesis.default_mode, sources["skill_synthesis.default_mode"] = _resolve(
        field_path="skill_synthesis.default_mode",
        new_env=None,
        env_map=env_map,
        toml_value=synth_toml.get("default_mode"),
        default=config.skill_synthesis.default_mode,
        validator=lambda v: v in ALLOWED_SKILL_SYNTHESIS_MODES,
    )
    config.skill_synthesis.lookback_hours, sources["skill_synthesis.lookback_hours"] = _resolve_number(
        "skill_synthesis.lookback_hours",
        new_env=None,
        env_map=env_map,
        toml_value=synth_toml.get("lookback_hours"),
        default=config.skill_synthesis.lookback_hours,
        cast=int,
    )
    config.skill_synthesis.lookback_days, sources["skill_synthesis.lookback_days"] = _resolve_number(
        "skill_synthesis.lookback_days",
        new_env=None,
        env_map=env_map,
        toml_value=synth_toml.get("lookback_days"),
        default=config.skill_synthesis.lookback_days,
        cast=int,
    )
    config.skill_synthesis.skills_prefix, sources["skill_synthesis.skills_prefix"] = _resolve(
        field_path="skill_synthesis.skills_prefix",
        new_env=None,
        env_map=env_map,
        toml_value=synth_toml.get("skills_prefix"),
        default=config.skill_synthesis.skills_prefix,
    )

    synth_agent_toml = synth_toml.get("agent", {}) or {}
    config.skill_synthesis.agent.backend, sources["skill_synthesis.agent.backend"] = _resolve(
        field_path="skill_synthesis.agent.backend",
        new_env=None,
        env_map=env_map,
        toml_value=synth_agent_toml.get("backend"),
        default=config.skill_synthesis.agent.backend,
    )
    config.skill_synthesis.agent.provider, sources["skill_synthesis.agent.provider"] = _resolve(
        field_path="skill_synthesis.agent.provider",
        new_env=None,
        env_map=env_map,
        toml_value=synth_agent_toml.get("provider"),
        default=config.skill_synthesis.agent.provider,
    )
    config.skill_synthesis.agent.model, sources["skill_synthesis.agent.model"] = _resolve(
        field_path="skill_synthesis.agent.model",
        new_env=None,
        env_map=env_map,
        toml_value=synth_agent_toml.get("model"),
        default=config.skill_synthesis.agent.model,
    )
    config.skill_synthesis.agent.timeout_seconds, sources["skill_synthesis.agent.timeout_seconds"] = _resolve_number(
        "skill_synthesis.agent.timeout_seconds",
        new_env=None,
        env_map=env_map,
        toml_value=synth_agent_toml.get("timeout_seconds"),
        default=config.skill_synthesis.agent.timeout_seconds,
        cast=float,
    )

    if config.skill_synthesis.enabled:
        if config.skill_synthesis.agent.backend not in ALLOWED_SKILL_SYNTHESIS_BACKENDS:
            warnings.append(
                "skill_synthesis.agent.backend must be agent:pi in v1; "
                f"got {config.skill_synthesis.agent.backend!r}"
            )
        if not str(config.skill_synthesis.agent.provider).strip():
            warnings.append("skill_synthesis.agent.provider is required when skill synthesis is enabled")
        if not str(config.skill_synthesis.agent.model).strip():
            warnings.append("skill_synthesis.agent.model is required when skill synthesis is enabled")
        if config.skill_synthesis.agent.timeout_seconds <= 0:
            warnings.append("skill_synthesis.agent.timeout_seconds must be positive")

    # --- session_reflection ---
    reflection_toml = raw_toml.get("session_reflection", {}) or {}
    reflection_enabled = reflection_toml.get("enabled")
    if isinstance(reflection_enabled, bool):
        config.session_reflection.enabled = reflection_enabled
        sources["session_reflection.enabled"] = "config.toml"
    else:
        sources["session_reflection.enabled"] = "default"

    config.session_reflection.backend, sources["session_reflection.backend"] = _resolve(
        field_path="session_reflection.backend",
        new_env=None,
        env_map=env_map,
        toml_value=reflection_toml.get("backend"),
        default=config.session_reflection.backend,
    )
    config.session_reflection.model, sources["session_reflection.model"] = _resolve(
        field_path="session_reflection.model",
        new_env=None,
        env_map=env_map,
        toml_value=reflection_toml.get("model"),
        default=config.session_reflection.model,
    )

    reflection_ephemeral = reflection_toml.get("ephemeral")
    if isinstance(reflection_ephemeral, bool):
        config.session_reflection.ephemeral = reflection_ephemeral
        sources["session_reflection.ephemeral"] = "config.toml"
    else:
        sources["session_reflection.ephemeral"] = "default"

    config.session_reflection.sandbox, sources["session_reflection.sandbox"] = _resolve(
        field_path="session_reflection.sandbox",
        new_env=None,
        env_map=env_map,
        toml_value=reflection_toml.get("sandbox"),
        default=config.session_reflection.sandbox,
    )
    config.session_reflection.approval_policy, sources["session_reflection.approval_policy"] = _resolve(
        field_path="session_reflection.approval_policy",
        new_env=None,
        env_map=env_map,
        toml_value=reflection_toml.get("approval_policy"),
        default=config.session_reflection.approval_policy,
    )
    config.session_reflection.skill_prefix, sources["session_reflection.skill_prefix"] = _resolve(
        field_path="session_reflection.skill_prefix",
        new_env=None,
        env_map=env_map,
        toml_value=reflection_toml.get("skill_prefix"),
        default=config.session_reflection.skill_prefix,
    )
    config.session_reflection.timeout_seconds, sources["session_reflection.timeout_seconds"] = _resolve_number(
        "session_reflection.timeout_seconds",
        new_env=None,
        env_map=env_map,
        toml_value=reflection_toml.get("timeout_seconds"),
        default=config.session_reflection.timeout_seconds,
        cast=float,
    )
    config.session_reflection.max_concurrent_jobs, sources["session_reflection.max_concurrent_jobs"] = _resolve_number(
        "session_reflection.max_concurrent_jobs",
        new_env=None,
        env_map=env_map,
        toml_value=reflection_toml.get("max_concurrent_jobs"),
        default=config.session_reflection.max_concurrent_jobs,
        cast=int,
    )

    replace_stop_reviewer = reflection_toml.get("replace_stop_reviewer")
    if isinstance(replace_stop_reviewer, bool):
        config.session_reflection.replace_stop_reviewer = replace_stop_reviewer
        sources["session_reflection.replace_stop_reviewer"] = "config.toml"
    else:
        sources["session_reflection.replace_stop_reviewer"] = "default"

    trigger_toml = reflection_toml.get("trigger", {}) or {}
    if not isinstance(trigger_toml, dict):
        warnings.append("session_reflection.trigger must be a table")
        trigger_toml = {}

    trigger_enabled = trigger_toml.get("enabled")
    if isinstance(trigger_enabled, bool):
        config.session_reflection.trigger.enabled = trigger_enabled
        sources["session_reflection.trigger.enabled"] = "config.toml"
    else:
        sources["session_reflection.trigger.enabled"] = "default"

    def _positive_trigger_int(field: str, default: int) -> int:
        """Resolve a trigger interval/limit that must be a positive integer."""
        value = trigger_toml.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            sources[f"session_reflection.trigger.{field}"] = "config.toml"
            return value
        sources[f"session_reflection.trigger.{field}"] = "default"
        if field in trigger_toml:
            warnings.append(f"session_reflection.trigger.{field} must be a positive integer")
        return default

    config.session_reflection.trigger.memory_stop_interval = _positive_trigger_int(
        "memory_stop_interval",
        config.session_reflection.trigger.memory_stop_interval,
    )
    config.session_reflection.trigger.memory_context_chars = _positive_trigger_int(
        "memory_context_chars",
        config.session_reflection.trigger.memory_context_chars,
    )
    config.session_reflection.trigger.skill_tool_call_interval = _positive_trigger_int(
        "skill_tool_call_interval",
        config.session_reflection.trigger.skill_tool_call_interval,
    )
    config.session_reflection.trigger.active_job_stale_seconds = _positive_trigger_int(
        "active_job_stale_seconds",
        config.session_reflection.trigger.active_job_stale_seconds,
    )

    high_signal = trigger_toml.get("high_signal_immediate")
    if isinstance(high_signal, bool):
        config.session_reflection.trigger.high_signal_immediate = high_signal
        sources["session_reflection.trigger.high_signal_immediate"] = "config.toml"
    else:
        sources["session_reflection.trigger.high_signal_immediate"] = "default"

    mode = trigger_toml.get("skill_generation_mode")
    if mode in ALLOWED_SESSION_REFLECTION_TRIGGER_MODES:
        config.session_reflection.trigger.skill_generation_mode = str(mode)
        sources["session_reflection.trigger.skill_generation_mode"] = "config.toml"
    else:
        sources["session_reflection.trigger.skill_generation_mode"] = "default"
        if mode not in (None, ""):
            warnings.append(
                "session_reflection.trigger.skill_generation_mode must be "
                "'one_shot_active' or 'evidence_first'"
            )

    if (
        "timeout_seconds" in reflection_toml
        and sources["session_reflection.timeout_seconds"] == "default"
    ):
        warnings.append("session_reflection.timeout_seconds must be numeric")
    if (
        "max_concurrent_jobs" in reflection_toml
        and sources["session_reflection.max_concurrent_jobs"] == "default"
    ):
        warnings.append("session_reflection.max_concurrent_jobs must be numeric")
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
    if (
        "skill_prefix" in reflection_toml
        and not str(reflection_toml.get("skill_prefix")).startswith(REFLECT_SKILL_PREFIX)
    ):
        warnings.append(
            "session_reflection.skill_prefix must start with "
            f"{REFLECT_SKILL_PREFIX!r}; got {reflection_toml.get('skill_prefix')!r}"
        )
    elif not str(config.session_reflection.skill_prefix).startswith(REFLECT_SKILL_PREFIX):
        warnings.append(
            "session_reflection.skill_prefix must start with "
            f"{REFLECT_SKILL_PREFIX!r}; got {config.session_reflection.skill_prefix!r}"
        )
    if config.session_reflection.timeout_seconds <= 0:
        warnings.append("session_reflection.timeout_seconds must be positive")
    if config.session_reflection.max_concurrent_jobs <= 0:
        warnings.append("session_reflection.max_concurrent_jobs must be positive")

    # --- session_recall ---
    session_recall_toml = raw_toml.get("session_recall", {}) or {}
    sr_enabled = session_recall_toml.get("enabled")
    if isinstance(sr_enabled, bool):
        config.session_recall.enabled = sr_enabled
        sources["session_recall.enabled"] = "config.toml"
    else:
        sources["session_recall.enabled"] = "default"

    sr_stop_hook = session_recall_toml.get("stop_hook_archive")
    if isinstance(sr_stop_hook, bool):
        config.session_recall.stop_hook_archive = sr_stop_hook
        sources["session_recall.stop_hook_archive"] = "config.toml"
    else:
        sources["session_recall.stop_hook_archive"] = "default"

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
    "compile.pi", "compile.pi.provider", "compile.pi.model", "compile.pi.mode",
    "compile.pi.timeout_seconds",
    "scheduler", "scheduler.backend", "scheduler.interval_seconds",
    "skill_synthesis", "skill_synthesis.enabled",
    "skill_synthesis.default_mode", "skill_synthesis.lookback_hours",
    "skill_synthesis.lookback_days", "skill_synthesis.skills_prefix",
    "skill_synthesis.agent", "skill_synthesis.agent.backend",
    "skill_synthesis.agent.provider", "skill_synthesis.agent.model",
    "skill_synthesis.agent.timeout_seconds",
    "session_reflection", "session_reflection.enabled",
    "session_reflection.backend", "session_reflection.model",
    "session_reflection.ephemeral", "session_reflection.sandbox",
    "session_reflection.approval_policy", "session_reflection.skill_prefix",
    "session_reflection.timeout_seconds", "session_reflection.max_concurrent_jobs",
    "session_reflection.replace_stop_reviewer",
    "session_reflection.trigger", "session_reflection.trigger.enabled",
    "session_reflection.trigger.memory_stop_interval",
    "session_reflection.trigger.memory_context_chars",
    "session_reflection.trigger.skill_tool_call_interval",
    "session_reflection.trigger.high_signal_immediate",
    "session_reflection.trigger.skill_generation_mode",
    "session_reflection.trigger.active_job_stale_seconds",
    "session_recall", "session_recall.enabled",
    "session_recall.stop_hook_archive",
    "log", "log.retention_days",
])

# Fields allowed inside [profiles.<name>]. Mirrors ReviewerConfig shape —
# the resolver uses this same schema regardless of which profile is active.
_RECOGNIZED_PROFILE_FIELDS: frozenset[str] = frozenset([
    "provider", "model", "base_url", "api_key_env",
    "timeout_seconds", "max_tokens",
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

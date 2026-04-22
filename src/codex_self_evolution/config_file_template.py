"""Static TOML template emitted by ``codex-self-evolution config init``.

Schema 2: profile-first. Multiple named provider presets live side by side
in ``[profiles.<name>]`` sections; switch between them by flipping
``active_profile`` at the top (or via ``codex-self-evolution config use
<name>``). Keys still live in .env.provider; only behavior in config.toml.

Matches ``docs/design_v2.md`` §3.2 (revised 2026-04-22 to profile-based).
"""

from __future__ import annotations


CONFIG_TEMPLATE: str = """\
# ~/.codex-self-evolution/config.toml
#
# Single source of truth for plugin behavior. API keys stay in
# .env.provider (secrets); behavior settings live here.
#
# Run `codex-self-evolution config show` to see the resolved final values.
# Run `codex-self-evolution config use <profile>` to switch active reviewer.
# See docs/design_v2.md for the full reference.

schema_version = 2

# Which [profiles.<name>] is live. Flip this (or use `config use`) to
# switch providers without editing each field manually.
active_profile = "minimax"


# ===========================================================================
# [profiles.*] — named reviewer configurations. Each one is self-contained.
# ===========================================================================

# MiniMax (default; Anthropic-style endpoint). Uses $MINIMAX_API_KEY.
[profiles.minimax]
provider = "minimax"
model = "MiniMax-M2.7"
timeout_seconds = 30
max_tokens = 4096
max_retries = 2
retry_backoff = [2.0, 5.0]


# GLM via Zhipu's Anthropic-compatible endpoint. `api_key_env` binds this
# profile to $ZHIPU_API_KEY so it doesn't collide with `kimi` or other
# anthropic-style profiles.
[profiles.glm]
provider = "anthropic-style"
model = "glm-5"
base_url = "https://open.bigmodel.cn/api/anthropic/v1/messages"
api_key_env = "ZHIPU_API_KEY"
timeout_seconds = 60
max_retries = 2
retry_backoff = [3.0, 8.0]


# Kimi (Moonshot) via its Anthropic-compatible endpoint. Uses $KIMI_API_KEY
# so `config use kimi` doesn't require touching GLM's key.
[profiles.kimi]
provider = "anthropic-style"
model = "kimi-k2.6"
base_url = "https://api.kimi.com/coding/v1/messages"
api_key_env = "KIMI_API_KEY"
timeout_seconds = 60
max_retries = 2
retry_backoff = [3.0, 8.0]


# DeepSeek (cheap, capable) via OpenAI-compat. Uses $OPENAI_API_KEY.
[profiles.deepseek]
provider = "openai-compatible"
model = "deepseek-chat"
base_url = "https://api.deepseek.com/v1"


# Gemini via Google's OpenAI-compat endpoint. Uses $OPENAI_API_KEY.
[profiles.gemini]
provider = "openai-compatible"
model = "gemini-2.5-flash"
base_url = "https://generativelanguage.googleapis.com/v1beta/openai"


# Codex CLI — no API key needed. Uses your existing ChatGPT login.
[profiles.codex]
provider = "codex-cli"
# subprocess.command = []   # leave empty for built-in default argv


# Opencode CLI — no API key needed. Uses opencode's own auth.
[profiles.opencode]
provider = "opencode-cli"


# ===========================================================================
# [compile] — merge reviewer suggestions into MEMORY/USER.md + recall + skills
# ===========================================================================

[compile]
# script | agent:opencode
backend = "agent:opencode"
allow_fallback = true


[compile.opencode]
# Empty strings → let opencode pick from ~/.config/opencode/opencode.json.
model = ""
agent = ""
timeout_seconds = 900


# ===========================================================================
# [scheduler] — launchd-triggered preflight scan
# ===========================================================================

[scheduler]
backend = "agent:opencode"
# interval_seconds is documentation-only — authoritative value lives in
# ~/Library/LaunchAgents/com.codex-self-evolution.preflight.plist.
interval_seconds = 300


# ===========================================================================
# [log] — plugin.log rotation
# ===========================================================================

[log]
retention_days = 14
"""

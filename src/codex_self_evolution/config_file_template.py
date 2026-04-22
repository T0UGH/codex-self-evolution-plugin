"""Static TOML template emitted by ``codex-self-evolution config init``.

Kept out of :mod:`config_file` so the template string — which is long and
prose-heavy — doesn't clutter the loader module. Format is a plain TOML
file with every section commented and explained; users can uncomment what
they need and leave the rest.

Matches the schema in ``docs/design_v2.md`` §3.2.
"""

from __future__ import annotations


CONFIG_TEMPLATE: str = """\
# ~/.codex-self-evolution/config.toml
#
# Single source of truth for plugin behavior. API keys stay in
# .env.provider (secrets); behavior settings live here.
#
# Every field below is optional — if absent, built-in defaults kick in.
# Environment variables still work as overrides. Run
# `codex-self-evolution config show` to see the resolved final values.
#
# See docs/design_v2.md for the full reference.

schema_version = 1


# ===========================================================================
# [reviewer] — Stop-hook background reviewer
# ===========================================================================

[reviewer]
# provider: how the Stop-hook reviewer is dispatched.
#   minimax           — HTTP POST to MiniMax (Anthropic-style endpoint)
#   openai-compatible — HTTP POST to any OpenAI-compat endpoint (GLM,
#                       DeepSeek, Qwen, Kimi, Gemini-OpenAI-mode, etc.)
#   anthropic-style   — HTTP POST to any Anthropic-style endpoint
#   codex-cli         — Subprocess: runs the local `codex exec` CLI (uses
#                       your existing ChatGPT login, no API key needed)
#   opencode-cli      — Subprocess: runs the local `opencode run` CLI
provider = "minimax"

# model: provider-specific model name. Empty string → use provider default.
model = ""

# base_url: HTTP endpoint URL. Empty string → provider default. This is
# how you switch to different providers without writing new code:
#
#   Gemini   → "https://generativelanguage.googleapis.com/v1beta/openai"
#   DeepSeek → "https://api.deepseek.com/v1"
#   GLM      → "https://open.bigmodel.cn/api/paas/v4"
#   Qwen     → "https://dashscope.aliyuncs.com/compatible-mode/v1"
#   Kimi     → "https://api.moonshot.cn/v1"
#
# Paired with provider = "openai-compatible" for all of the above.
base_url = ""

# HTTP-request budgets. Adjust when your provider is slow or rate-limited.
timeout_seconds = 30
max_tokens = 4096
max_retries = 2
retry_backoff = [2.0, 5.0]


# Only applies when provider = "codex-cli" / "opencode-cli" / custom.
[reviewer.subprocess]
# command: subprocess argv. Empty array → use provider's built-in default.
# To point at a custom CLI: command = ["my-llm-cli", "--json"]
command = []

# payload_mode: how to hand the review snapshot to the child process.
#   stdin  — JSON piped via stdin (codex-cli default)
#   file   — JSON written to a tempfile, path attached via argv
#   inline — JSON appended to the prompt (simpler, but argv size limits bite)
payload_mode = "stdin"

# response_format: how to parse the child's stdout.
#   codex-events    — codex exec --json event stream
#   opencode-events — opencode run --format json event stream
#   raw-json        — child prints one JSON object directly
response_format = "codex-events"

timeout_seconds = 90


# ===========================================================================
# [compile] — merge reviewer suggestions into MEMORY/USER.md, recall, skills
# ===========================================================================

[compile]
# backend: pick how compile merges new suggestions.
#   script         — local dedup; deterministic, sub-second
#   agent:opencode — LLM agent merges; smarter dedup + skill generation
backend = "agent:opencode"

# When agent backend fails (subprocess crashed, output malformed, etc.)
# fall back to script. Keep this true in production — without it, a
# single opencode flake stalls every compile until the user notices.
allow_fallback = true


[compile.opencode]
# Empty strings → let opencode pick from its own ~/.config/opencode/opencode.json.
model = ""
agent = ""

# Must stay well under the 30-min compile lock stale threshold.
timeout_seconds = 900


# ===========================================================================
# [scheduler] — launchd-triggered preflight scan
# ===========================================================================

[scheduler]
# Default backend when the launchd scheduler invokes `scan`. Same values
# as compile.backend.
backend = "agent:opencode"

# Documentation-only today — the authoritative interval lives in the
# launchd plist (~/Library/LaunchAgents/com.codex-self-evolution.preflight.plist).
# Change this value AND re-run scripts/install-scheduler.sh to take effect.
interval_seconds = 300


# ===========================================================================
# [log] — plugin.log rotation
# ===========================================================================

[log]
retention_days = 14
"""

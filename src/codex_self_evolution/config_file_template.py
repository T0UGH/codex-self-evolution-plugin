"""Static TOML template emitted by ``codex-self-evolution config init``."""

from __future__ import annotations


CONFIG_TEMPLATE: str = """\
# ~/.codex-self-evolution/config.toml

schema_version = 2

[session_reflection]
enabled = true
backend = "codex-app-server"
model = "gpt-5.3-codex-spark"
ephemeral = true
sandbox = "danger-full-access"
approval_policy = "never"
skill_prefix = "csep-reflect-"
timeout_seconds = 900
max_concurrent_jobs = 1

[session_reflection.trigger]
enabled = true
memory_stop_interval = 3
memory_context_chars = 16000
skill_tool_call_interval = 15
high_signal_immediate = true
skill_generation_mode = "one_shot_active"
active_job_stale_seconds = 1800

[session_recall]
enabled = true
stop_hook_archive = true

[log]
retention_days = 14
"""

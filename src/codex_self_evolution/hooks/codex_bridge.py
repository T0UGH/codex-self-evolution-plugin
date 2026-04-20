"""Codex CLI native hook bridge.

Codex 0.121+ User-scope hooks (``~/.codex/hooks.json``) deliver event payloads
over stdin using a Claude Code–compatible schema, e.g. for ``Stop``:

    {
      "session_id": "019da...",
      "turn_id": "019da...",
      "transcript_path": "/Users/.../rollout-....jsonl",
      "cwd": "/path/to/workspace",
      "hook_event_name": "Stop",
      "model": "gpt-5.4",
      "permission_mode": "bypassPermissions",
      "stop_hook_active": false,
      "last_assistant_message": "..."
    }

This module is the one place that understands that schema so the rest of the
plugin keeps operating on its own canonical ``SuggestionEnvelope``-oriented
payload.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_PROVIDER_ENV = "CODEX_SELF_EVOLUTION_REVIEWER_PROVIDER"
DEFAULT_PROVIDER_FALLBACK = "minimax"

# Upper bound on how many transcript characters we hand to the reviewer, so a
# huge jsonl can't blow past provider limits. Applied at the joined string
# level (not per message).
TRANSCRIPT_MAX_CHARS = 8000


def map_codex_stop_payload(
    codex_payload: dict[str, Any],
    *,
    reviewer_provider: str | None = None,
    read_transcript: bool = True,
) -> dict[str, Any]:
    """Translate a Codex Stop hook payload into the shape our ``stop_review``
    entry point expects.

    - ``session_id`` → ``thread_id`` (fallback ``unknown-thread``)
    - ``turn_id`` passed through (fallback empty string)
    - ``cwd`` passed through (fallback ``.``)
    - ``transcript``: best-effort transcript. If ``read_transcript`` and
      ``transcript_path`` resolves to a readable jsonl, we stitch the
      user/assistant messages (truncated to ``TRANSCRIPT_MAX_CHARS``). Otherwise
      fall back to ``last_assistant_message``.
    - ``thread_read_output`` left empty (Codex doesn't give us an equivalent).
    - ``reviewer_provider``: explicit argument > ``CODEX_SELF_EVOLUTION_REVIEWER_PROVIDER``
      env var > ``minimax`` default.
    - Extra Codex-only fields (``codex_transcript_path``, ``hook_event_name``,
      ``model``, ``permission_mode``) preserved under passthrough keys so
      downstream debugging has the full context.
    """
    provider = reviewer_provider or os.environ.get(DEFAULT_PROVIDER_ENV) or DEFAULT_PROVIDER_FALLBACK

    cwd = str(codex_payload.get("cwd") or ".")
    transcript_path = codex_payload.get("transcript_path") or ""
    transcript_text = ""
    if read_transcript and transcript_path:
        transcript_text = _read_transcript(transcript_path)
    if not transcript_text:
        transcript_text = str(codex_payload.get("last_assistant_message") or "")

    return {
        "thread_id": str(codex_payload.get("session_id") or "unknown-thread"),
        "turn_id": str(codex_payload.get("turn_id") or ""),
        "cwd": cwd,
        "transcript": transcript_text,
        "thread_read_output": str(codex_payload.get("last_assistant_message") or ""),
        "reviewer_provider": provider,
        # Passthrough for debugging / audit. stop_review ignores unknown keys.
        "codex_transcript_path": str(transcript_path),
        "codex_hook_event": str(codex_payload.get("hook_event_name") or ""),
        "codex_model": str(codex_payload.get("model") or ""),
        "codex_permission_mode": str(codex_payload.get("permission_mode") or ""),
    }


def _read_transcript(path: str, limit: int = TRANSCRIPT_MAX_CHARS) -> str:
    """Read a Codex rollout jsonl and return a plain-text transcript.

    Codex stores each assistant/user/tool event as one JSON object per line. We
    only surface user prompts and assistant messages so the reviewer sees a
    conversational view rather than a dump of tool call machinery.

    Returns empty string on any read/parse error — caller falls back to
    ``last_assistant_message``.
    """
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return ""

    collected: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        rendered = _render_transcript_entry(entry)
        if rendered:
            collected.append(rendered)

    if not collected:
        return ""

    joined = "\n\n".join(collected)
    if len(joined) > limit:
        # Keep the tail — the end of the conversation is more load-bearing
        # for a reviewer than the early context. Leading ellipsis signals the
        # cut to both the reviewer prompt and humans reading the snapshot.
        joined = "...\n" + joined[-(limit - 4):]
    return joined


def _render_transcript_entry(entry: Any) -> str:
    """Render one rollout jsonl entry into a single ``role: text`` line.

    Codex rollout objects are not strictly typed; we defensively pull the
    fields most commonly present:
      - ``{"role": "user"|"assistant", "content": "...")``
      - ``{"type": "agent_message", "text": "..."}``
      - ``{"type": "user_message", "text": "..."}``
    Anything else is skipped.
    """
    if not isinstance(entry, dict):
        return ""

    role = str(entry.get("role") or "").lower()
    entry_type = str(entry.get("type") or "").lower()

    text = entry.get("content") or entry.get("text") or entry.get("message")
    if isinstance(text, list):
        # Codex sometimes stores content as list of content-part objects.
        parts: list[str] = []
        for part in text:
            if isinstance(part, dict):
                piece = part.get("text") or part.get("content")
                if isinstance(piece, str):
                    parts.append(piece)
        text = "\n".join(parts)
    if not isinstance(text, str):
        return ""
    text = text.strip()
    if not text:
        return ""

    if role in {"user", "assistant"}:
        label = role
    elif entry_type in {"user_message", "user_input"}:
        label = "user"
    elif entry_type in {"agent_message", "assistant_message"}:
        label = "assistant"
    else:
        # Ignore tool calls, reasoning, meta — they bloat the transcript with
        # signal the reviewer doesn't need.
        return ""

    return f"{label}: {text}"

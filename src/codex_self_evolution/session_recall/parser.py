from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import ParsedMessage, ParsedSession
from .repo import collect_repo_metadata

_READABLE_ROLES = {"user", "assistant", "tool", "system", "developer"}
_TYPE_TO_ROLE = {
    "agent_message": "assistant",
    "assistant_message": "assistant",
    "user_message": "user",
    "user_input": "user",
}


def parse_codex_jsonl(path: str | Path, *, session_id: str = "", cwd: str = "") -> ParsedSession:
    session_path = Path(path).expanduser().resolve()
    fallback_cwd = str(Path(cwd).expanduser().resolve()) if cwd else str(session_path.parent)
    resolved_session_id = session_id or session_path.stem
    resolved_cwd = fallback_cwd
    session_meta: dict[str, Any] = {}
    messages: list[ParsedMessage] = []

    lines = session_path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line_number, raw_line in enumerate(lines):
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            entry = json.loads(raw_line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue

        entry_type = str(entry.get("type") or "")
        payload = entry.get("payload")
        if entry_type == "session_meta" and isinstance(payload, dict):
            resolved_session_id = str(payload.get("id") or resolved_session_id)
            resolved_cwd = str(payload.get("cwd") or resolved_cwd)
            session_meta.update(payload)
            continue

        candidate = _message_candidate(entry)
        if candidate is None:
            continue
        role, content, tool_name, metadata = candidate
        if not content.strip():
            continue
        uid = _message_uid(entry, raw_line, resolved_session_id)
        messages.append(
            ParsedMessage(
                session_id=resolved_session_id,
                message_uid=uid,
                message_index=len(messages),
                role=role,
                content=content.strip(),
                raw_json=raw_line,
                metadata={**metadata, "line_number": line_number},
                timestamp=str(entry.get("timestamp") or metadata.get("timestamp") or ""),
                tool_name=tool_name,
                raw_event_type=entry_type or str(metadata.get("type") or ""),
            )
        )

    repo_meta = collect_repo_metadata(resolved_cwd)
    return ParsedSession(
        session_id=resolved_session_id,
        session_path=session_path,
        cwd=repo_meta["cwd"],
        messages=messages,
        metadata={**session_meta, **repo_meta},
    )


def _message_candidate(entry: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]] | None:
    if entry.get("type") == "event_msg":
        return None
    payload = entry.get("payload")
    source = payload if entry.get("type") == "response_item" and isinstance(payload, dict) else entry
    if not isinstance(source, dict):
        return None

    source_type = str(source.get("type") or "").lower()
    role = str(source.get("role") or "").lower()
    if not role:
        role = _TYPE_TO_ROLE.get(source_type, "")
    if role not in _READABLE_ROLES:
        return None

    content = _extract_text(source.get("content"))
    if not content:
        content = _extract_text(source.get("text"))
    if not content:
        content = _extract_text(source.get("message"))

    tool_name = str(
        source.get("tool_name")
        or source.get("name")
        or _tool_name_from_calls(source.get("tool_calls"))
        or ""
    )
    if role == "assistant" and source.get("tool_calls") and not content:
        content = f"tool_calls: {json.dumps(source.get('tool_calls'), ensure_ascii=False, sort_keys=True)}"
    if role == "tool" and tool_name and content:
        content = content

    metadata = {
        "type": source_type,
        "id": source.get("id"),
        "call_id": source.get("call_id") or source.get("tool_call_id"),
        "tool_calls": source.get("tool_calls"),
    }
    return role, content or "", tool_name, metadata


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for part in value:
            if isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        text = value.get("text") or value.get("content") or value.get("message")
        if isinstance(text, str):
            return text
    return ""


def _tool_name_from_calls(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return ""
    first = value[0]
    if not isinstance(first, dict):
        return ""
    function = first.get("function")
    if isinstance(function, dict) and function.get("name"):
        return str(function["name"])
    if first.get("name"):
        return str(first["name"])
    return ""


def _message_uid(entry: dict[str, Any], raw_line: str, session_id: str) -> str:
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    candidates = [
        entry.get("id"),
        payload.get("id"),
        entry.get("item_id"),
        payload.get("item_id"),
        entry.get("call_id"),
        payload.get("call_id"),
        entry.get("tool_call_id"),
        payload.get("tool_call_id"),
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return hashlib.sha256(f"{session_id}\n{raw_line}".encode("utf-8")).hexdigest()

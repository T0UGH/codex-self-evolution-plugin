from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
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
    source_updated_at = _source_updated_at(session_path)
    metadata = {**session_meta, **repo_meta}
    if source_updated_at:
        metadata.setdefault("source_updated_at", source_updated_at)
    return ParsedSession(
        session_id=resolved_session_id,
        session_path=session_path,
        cwd=repo_meta["cwd"],
        messages=messages,
        metadata=metadata,
    )


def parse_claude_jsonl(path: str | Path, *, session_id: str = "", cwd: str = "") -> ParsedSession:
    session_path = Path(path).expanduser().resolve()
    raw_session_id = session_id or session_path.stem
    resolved_session_id = _claude_session_id(raw_session_id)
    resolved_cwd = str(Path(cwd).expanduser().resolve()) if cwd else str(session_path.parent)
    session_meta: dict[str, Any] = {
        "source": "claude_code_jsonl",
        "claude_session_id": raw_session_id,
    }
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

        if isinstance(entry.get("sessionId"), str) and not session_id:
            raw_session_id = str(entry["sessionId"])
            resolved_session_id = _claude_session_id(raw_session_id)
            session_meta["claude_session_id"] = raw_session_id
        if isinstance(entry.get("cwd"), str) and not cwd:
            resolved_cwd = str(entry["cwd"])
        for key in ("version", "gitBranch", "entrypoint", "permissionMode", "userType"):
            if entry.get(key) is not None:
                session_meta.setdefault(key, entry.get(key))
        if entry.get("type") == "ai-title" and isinstance(entry.get("aiTitle"), str):
            session_meta.setdefault("title", entry["aiTitle"])

        for candidate in _claude_message_candidates(entry, raw_line, line_number, resolved_session_id):
            role, content, tool_name, metadata, uid = candidate
            if not content.strip():
                continue
            messages.append(
                ParsedMessage(
                    session_id=resolved_session_id,
                    message_uid=uid,
                    message_index=len(messages),
                    role=role,
                    content=content.strip(),
                    raw_json=raw_line,
                    metadata=metadata,
                    timestamp=str(entry.get("timestamp") or ""),
                    tool_name=tool_name,
                    raw_event_type=str(entry.get("type") or ""),
                )
            )

    repo_meta = collect_repo_metadata(resolved_cwd)
    source_updated_at = _source_updated_at(session_path)
    metadata = {**session_meta, **repo_meta}
    if source_updated_at:
        metadata.setdefault("source_updated_at", source_updated_at)
    if messages:
        metadata.setdefault("started_at", messages[0].timestamp)
        metadata.setdefault("timestamp", messages[0].timestamp)
    return ParsedSession(
        session_id=resolved_session_id,
        session_path=session_path,
        cwd=repo_meta["cwd"],
        messages=messages,
        metadata=metadata,
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


def _claude_message_candidates(
    entry: dict[str, Any],
    raw_line: str,
    line_number: int,
    session_id: str,
) -> list[tuple[str, str, str, dict[str, Any], str]]:
    entry_type = str(entry.get("type") or "")
    if entry_type not in {"user", "assistant", "system"}:
        return []
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
    role = str(message.get("role") or entry_type).lower()
    if role not in _READABLE_ROLES:
        return []
    content = message.get("content")
    base_metadata = {
        "type": entry_type,
        "id": message.get("id"),
        "uuid": entry.get("uuid"),
        "parentUuid": entry.get("parentUuid"),
        "promptId": entry.get("promptId"),
        "sessionId": entry.get("sessionId"),
        "line_number": line_number,
    }
    base_uid = str(entry.get("uuid") or message.get("id") or _message_uid(entry, raw_line, session_id))
    if isinstance(content, str):
        return [(role, content, "", base_metadata, base_uid)]
    if not isinstance(content, list):
        return []

    candidates: list[tuple[str, str, str, dict[str, Any], str]] = []
    for block_index, block in enumerate(content):
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "")
        metadata = {**base_metadata, "block_type": block_type, "block_index": block_index}
        uid = f"{base_uid}:{block_index}"
        if block_type == "thinking":
            continue
        if block_type == "text":
            text = _extract_text(block)
            candidates.append((role, text, "", metadata, uid))
            continue
        if block_type == "tool_use":
            tool_name = str(block.get("name") or "")
            tool_input = block.get("input")
            rendered = f"tool_use: {tool_name}"
            if tool_input not in (None, ""):
                rendered += "\ninput: " + json.dumps(tool_input, ensure_ascii=False, sort_keys=True)
            candidates.append(("assistant", rendered, tool_name, metadata, uid))
            continue
        if block_type == "tool_result":
            content_text = _extract_text(block.get("content"))
            if not content_text:
                content_text = _extract_text(block)
            tool_name = str(block.get("tool_use_id") or "")
            candidates.append(("tool", content_text, tool_name, metadata, uid))
    return candidates


def _claude_session_id(value: str) -> str:
    value = str(value or "").strip() or "unknown-session"
    return value if value.startswith("claude:") else f"claude:{value}"


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


def _source_updated_at(path: Path) -> str:
    """Return the source transcript mtime as a stable UTC string."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ""
    return datetime.fromtimestamp(mtime, UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

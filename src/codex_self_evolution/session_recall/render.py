from __future__ import annotations

import re
from typing import Any

_SECRET_RE = re.compile(
    r"(authorization:\s*bearer\s+)[^\s]+|"
    r"(api[_-]?key\s*[=:]\s*)[^\s]+|"
    r"(token\s*[=:]\s*)[^\s]+|"
    r"(password\s*[=:]\s*)[^\s]+|"
    r"(cookie\s*[=:]\s*)[^\s]+",
    re.IGNORECASE,
)


def render_markdown(payload: dict[str, Any]) -> str:
    query = str(payload.get("query") or "").strip()
    count = int(payload.get("count") or 0)
    status = str(payload.get("status") or ("matched" if count else "no_match"))
    scope = str(payload.get("scope") or "repo")
    budget = payload.get("budget") or {}
    used = int(budget.get("used_chars") or 0)
    total = int(budget.get("budget_chars") or 0)
    lines = ["## Focused Recall", "", f"Status: {status}", f"Scope: {scope}", f"Results: {count}"]
    if total:
        lines.append(f"Budget: {used}/{total} chars")
    if query:
        lines.append(f"Query: {query}")
    if payload.get("error"):
        lines.extend(["", f"Recall failed softly: {payload['error']}"])
        return "\n".join(lines).rstrip() + "\n"
    if count == 0:
        return "\n".join(lines).rstrip() + "\n"
    for idx, item in enumerate(payload.get("results") or [], start=1):
        session_id = str(item.get("session_id") or "")
        title = item.get("updated_at") or item.get("started_at") or "session"
        lines.extend(["", f"### {idx}. {title} session {session_id}"])
        if item.get("score") not in (None, ""):
            lines.append(f"Score: {item.get('score')}")
        lines.append(f"Matched: {item.get('matched') or item.get('preview') or ''}")
        if item.get("source"):
            lines.append(f"Source: {item['source']}")
        if item.get("repo"):
            lines.append(f"Repo: {item['repo']}")
        if item.get("worktree"):
            lines.append(f"Worktree: {item['worktree']}")
        if item.get("branch"):
            lines.append(f"Branch: {item['branch']}")
        if item.get("hit_count", 0) > 1:
            lines.append(f"Other hits: {int(item['hit_count']) - 1} hidden by budget")
        windows = item.get("windows") or []
        if windows:
            for window_idx, window in enumerate(windows, start=1):
                if len(windows) > 1:
                    lines.extend(["", f"Window {window_idx} matched: {window.get('matched') or ''}"])
                for msg in window.get("messages") or []:
                    role = msg.get("role", "message")
                    tool = msg.get("tool_name")
                    label = f"{role}:{tool}" if tool else str(role)
                    content = str(msg.get("content") or "")
                    lines.extend(["", f"[{label}] {content}"])
        else:
            for msg in item.get("messages") or []:
                role = msg.get("role", "message")
                tool = msg.get("tool_name")
                label = f"{role}:{tool}" if tool else str(role)
                content = str(msg.get("content") or "")
                lines.extend(["", f"[{label}] {content}"])
    return "\n".join(lines).rstrip() + "\n"


def budget_payload(
    *,
    query: str,
    scope: str,
    results: list[dict[str, Any]],
    budget_chars: int,
    message_chars: int,
    tool_message_chars: int,
    recent: bool = False,
) -> dict[str, Any]:
    budget_chars = max(200, int(budget_chars))
    message_chars = max(80, int(message_chars))
    tool_message_chars = max(80, int(tool_message_chars))
    output_results: list[dict[str, Any]] = []
    used = 0
    truncated = False
    for result in results:
        item = {key: value for key, value in result.items() if key != "messages"}
        item_windows = []
        source_windows = result.get("windows") or []
        if source_windows:
            for window in source_windows:
                budgeted_window, used, truncated = _budget_messages(
                    window.get("messages") or [],
                    used=used,
                    budget_chars=budget_chars,
                    message_chars=message_chars,
                    tool_message_chars=tool_message_chars,
                    truncated=truncated,
                )
                item_windows.append({**window, "messages": budgeted_window})
                if used >= budget_chars:
                    break
        else:
            item_messages = []
            source_messages = result.get("messages") or []
            if recent and not source_messages:
                source_messages = [{"role": "preview", "content": result.get("preview", "")}]
            item_messages, used, truncated = _budget_messages(
                source_messages,
                used=used,
                budget_chars=budget_chars,
                message_chars=message_chars,
                tool_message_chars=tool_message_chars,
                truncated=truncated,
            )
            item["messages"] = item_messages
        if item_windows:
            item["windows"] = item_windows
            item["messages"] = item_windows[0].get("messages") or []
        output_results.append(item)
        if used >= budget_chars:
            break
    return {
        "query": query,
        "scope": scope,
        "count": len(output_results),
        "results": output_results,
        "focused_recall": "",
        "status": "matched" if output_results else "no_match",
        "budget": {
            "budget_chars": budget_chars,
            "used_chars": min(used, budget_chars),
            "truncated": truncated,
            "truncation_reason": "budget_exceeded" if truncated else "",
        },
    }


def _budget_messages(
    source_messages: list[dict[str, Any]],
    *,
    used: int,
    budget_chars: int,
    message_chars: int,
    tool_message_chars: int,
    truncated: bool,
) -> tuple[list[dict[str, Any]], int, bool]:
    item_messages = []
    for msg in source_messages:
        limit = tool_message_chars if msg.get("role") == "tool" else message_chars
        content, was_truncated = _truncate(_redact(str(msg.get("content") or "")), limit)
        truncated = truncated or was_truncated
        entry = dict(msg)
        entry["content"] = content
        cost = len(content) + len(str(entry.get("role") or "")) + 8
        if used + cost > budget_chars:
            truncated = True
            if not item_messages:
                entry["content"], _ = _truncate(content, max(80, budget_chars - used - 40))
                item_messages.append(entry)
                used = budget_chars
            break
        item_messages.append(entry)
        used += cost
    return item_messages, used, truncated


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    if limit <= 40:
        return text[:limit], True
    head = max(20, limit // 2 - 20)
    tail = max(20, limit - head - 35)
    return f"{text[:head].rstrip()}\n[truncated: message exceeded {limit} chars]\n{text[-tail:].lstrip()}", True


def _redact(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        prefix = next((group for group in match.groups() if group), "")
        return f"{prefix}[REDACTED]"

    return _SECRET_RE.sub(repl, text)

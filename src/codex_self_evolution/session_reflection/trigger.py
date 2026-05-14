from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from ..storage import atomic_write_json, load_json, utc_now
from .paths import SessionReflectionTriggerPaths, build_session_reflection_trigger_paths

DECISION_RETENTION = 200
MEMORY_KEYWORDS = ["记住", "记录一下", "下次", "以后不要", "不要再", "规则", "约定", "偏好", "习惯", "memory"]
SKILL_KEYWORDS = ["skill", "技能", "工作流", "workflow", "复用", "沉淀", "SOP", "runbook"]
HANDOFF_KEYWORDS = ["交接", "handoff", "status", "收尾", "状态文档"]
CORRECTION_KEYWORDS = ["不是这个意思", "你理解错了", "不要改代码", "别改代码", "恢复", "回退", "过度抽象"]
ENGLISH_KEYWORDS = {"memory", "skill", "workflow", "sop", "runbook", "handoff", "status"}
BOOTSTRAP_USER_MARKERS = (
    "<environment_context>",
    "<skills_instructions>",
    "<plugins_instructions>",
    "# AGENTS.md instructions",
    "<INSTRUCTIONS>",
)
BOOTSTRAP_WRAPPER_PREFIXES = (
    "# AGENTS.md instructions",
    "<environment_context>",
    "<skills_instructions>",
    "<plugins_instructions>",
)


class TriggerLockBusy(RuntimeError):
    """Raised when a per-session trigger lock is already held."""


def _utc_timestamp() -> str:
    """Return a stable UTC timestamp for trigger sidecar files."""
    return utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def payload_session_id(payload: dict[str, Any]) -> str:
    """Return the parent session id from a Codex Stop payload."""
    return str(payload.get("session_id") or payload.get("thread_id") or "unknown-session")


def trigger_paths_for_payload(
    payload: dict[str, Any],
    *,
    home: str | Path | None = None,
) -> SessionReflectionTriggerPaths:
    """Return trigger sidecar paths for a Stop payload."""
    return build_session_reflection_trigger_paths(payload_session_id(payload), home=home)


def default_trigger_state(session_id: str) -> dict[str, Any]:
    """Return a fresh trigger state object for one parent session."""
    now = _utc_timestamp()
    return {
        "schema_version": 1,
        "session_id": session_id,
        "last_counted_byte_offset": 0,
        "last_counted_message_index": 0,
        "last_counted_event_uid": "",
        "stops_since_memory_review": 0,
        "readable_chars_since_memory_review": 0,
        "tool_calls_since_skill_review": 0,
        "last_memory_review_at": None,
        "last_skill_review_at": None,
        "active_job_id": None,
        "last_decision": {},
        "updated_at": now,
    }


def load_trigger_state(paths: SessionReflectionTriggerPaths, *, session_id: str) -> dict[str, Any]:
    """Load trigger state or return defaults when no valid state exists."""
    default_state = default_trigger_state(session_id)
    if not paths.state_path.is_file():
        return default_state
    try:
        raw = load_json(paths.state_path)
    except (OSError, ValueError):
        return default_state
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        return default_state
    return {
        field: raw.get(field, value)
        for field, value in default_state.items()
    } | {"session_id": session_id, "schema_version": 1}


def write_trigger_state(paths: SessionReflectionTriggerPaths, state: dict[str, Any]) -> None:
    """Atomically write one session trigger state with a fresh update time."""
    paths.session_dir.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _utc_timestamp()
    atomic_write_json(paths.state_path, state)


def append_decision(paths: SessionReflectionTriggerPaths, decision: dict[str, Any]) -> None:
    """Append a trigger decision row and retain only the newest decisions."""
    paths.session_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "schema_version": 1,
        "created_at": _utc_timestamp(),
        "decision": decision,
    }
    rows: list[str] = []
    if paths.decisions_path.is_file():
        rows = paths.decisions_path.read_text(encoding="utf-8").splitlines()
    rows.append(json.dumps(row, ensure_ascii=False, sort_keys=True))
    rows = rows[-DECISION_RETENTION:]
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=paths.session_dir,
        delete=False,
    ) as handle:
        handle.write("\n".join(rows) + "\n")
        temp_name = handle.name
    os.replace(temp_name, paths.decisions_path)


@contextmanager
def session_trigger_lock(paths: SessionReflectionTriggerPaths) -> Iterator[None]:
    """Acquire a non-blocking per-session trigger lock."""
    paths.session_dir.mkdir(parents=True, exist_ok=True)
    with paths.lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise TriggerLockBusy(f"trigger lock busy: {paths.lock_path}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def reset_counters_after_job(
    state: dict[str, Any],
    job: dict[str, Any],
    *,
    memory_succeeded: bool,
    skill_succeeded: bool,
    now: str,
) -> dict[str, Any]:
    """Reset only the successful scopes covered by a job counter snapshot."""
    updated = dict(state)
    raw_snapshot = job.get("counter_snapshot")
    snapshot = raw_snapshot if isinstance(raw_snapshot, dict) else {}
    if job.get("review_memory") and memory_succeeded:
        updated["stops_since_memory_review"] = max(
            0,
            int(updated.get("stops_since_memory_review") or 0)
            - int(snapshot.get("stops_since_memory_review") or 0),
        )
        updated["readable_chars_since_memory_review"] = max(
            0,
            int(updated.get("readable_chars_since_memory_review") or 0)
            - int(snapshot.get("readable_chars_since_memory_review") or 0),
        )
        updated["last_memory_review_at"] = now
    if job.get("review_skills") and skill_succeeded:
        updated["tool_calls_since_skill_review"] = max(
            0,
            int(updated.get("tool_calls_since_skill_review") or 0)
            - int(snapshot.get("tool_calls_since_skill_review") or 0),
        )
        updated["last_skill_review_at"] = now
    if updated.get("active_job_id") == job.get("job_id"):
        updated["active_job_id"] = None
    return updated


def _extract_source(entry: dict[str, Any]) -> dict[str, Any]:
    """Return the message-like source object from a transcript JSONL row."""
    payload = entry.get("payload")
    if entry.get("type") == "response_item" and isinstance(payload, dict):
        return payload
    return entry


def _extract_text(value: Any) -> str:
    """Extract readable text from common Codex transcript content shapes."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    if isinstance(value, dict):
        text = value.get("text") or value.get("content") or value.get("message")
        return text if isinstance(text, str) else ""
    return ""


def _message_text(source: dict[str, Any]) -> str:
    """Return the first readable message text found in a source object."""
    return (
        _extract_text(source.get("content"))
        or _extract_text(source.get("text"))
        or _extract_text(source.get("message"))
        or _extract_text(source.get("output"))
    )


def _tool_call_summary(source: dict[str, Any]) -> tuple[int, str]:
    """Return tool-call count plus bounded readable summaries for accounting."""
    if source.get("type") == "function_call":
        name = source.get("name") or "tool"
        args = source.get("arguments") or ""
        args_text = json.dumps(args, ensure_ascii=False, sort_keys=True) if not isinstance(args, str) else args
        return 1, f"{name}: {args_text[:300]}"
    calls = source.get("tool_calls")
    if not isinstance(calls, list):
        return 0, ""
    summaries: list[str] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = call.get("name") or function.get("name") or call.get("tool_name") or "tool"
        args = call.get("arguments") or function.get("arguments") or ""
        args_text = json.dumps(args, ensure_ascii=False, sort_keys=True) if not isinstance(args, str) else args
        summaries.append(f"{name}: {args_text[:300]}")
    return len(summaries), "\n".join(summaries)


def _scan_keyword_group(text: str, keywords: list[str]) -> list[str]:
    """Match Chinese keywords literally and English keywords as whole words."""
    matched: list[str] = []
    for keyword in keywords:
        if keyword.lower() in ENGLISH_KEYWORDS:
            if re.search(rf"\b{re.escape(keyword)}\b", text, flags=re.IGNORECASE):
                matched.append(keyword)
        elif keyword in text:
            matched.append(keyword)
    return matched


def _event_uid(entry: dict[str, Any], source: dict[str, Any], fallback: str) -> str:
    """Extract the newest event identifier from known transcript fields."""
    for key in ("event_uid", "uid", "id", "message_id"):
        value = source.get(key) or entry.get(key)
        if value:
            return str(value)
    return fallback


def _is_readable_message(source: dict[str, Any], role: str) -> bool:
    """Return whether message text should count toward readable context."""
    if source.get("type") == "function_call_output" or role == "tool":
        return False
    return True


def _is_scannable_user_text(text: str) -> bool:
    """Return whether user text looks like an actual user turn."""
    stripped = text.lstrip()
    if stripped.startswith(BOOTSTRAP_WRAPPER_PREFIXES):
        return False
    marker_count = sum(1 for marker in BOOTSTRAP_USER_MARKERS if marker in text)
    return marker_count < 2


def _read_transcript_delta(path: Path, offset: int) -> tuple[int, int, int, list[str], int, str]:
    """Scan transcript rows after byte offset and return incremental counters."""
    readable_chars = 0
    tool_calls = 0
    user_texts: list[str] = []
    message_count = 0
    last_uid = ""
    with path.open("rb") as handle:
        size = path.stat().st_size
        if offset < 0 or offset > size:
            offset = 0
        handle.seek(offset)
        for raw in handle:
            row_start = handle.tell() - len(raw)
            try:
                line = raw.decode("utf-8", errors="replace").strip()
                entry = json.loads(line)
            except ValueError:
                if not raw.endswith(b"\n"):
                    return row_start, readable_chars, tool_calls, user_texts, message_count, last_uid
                continue
            if not isinstance(entry, dict):
                continue
            source = _extract_source(entry)
            role = str(source.get("role") or "").lower()
            text = _message_text(source)
            calls_count, calls_summary = _tool_call_summary(source)

            if text and _is_readable_message(source, role):
                readable_chars += len(text)
                message_count += 1
                if role == "user" and _is_scannable_user_text(text):
                    user_texts.append(text)
            if calls_count:
                tool_calls += calls_count
                readable_chars += len(calls_summary)
                message_count += 1
            last_uid = _event_uid(entry, source, last_uid)
        return handle.tell(), readable_chars, tool_calls, user_texts, message_count, last_uid


def evaluate_trigger_policy(
    payload: dict[str, Any],
    config: Any,
    *,
    home: str | Path | None = None,
    active_job: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update trigger state from transcript delta and return the trigger decision."""
    session_id = payload_session_id(payload)
    paths = trigger_paths_for_payload(payload, home=home)
    with session_trigger_lock(paths):
        state = load_trigger_state(paths, session_id=session_id)
        transcript_path = Path(str(payload.get("transcript_path") or payload.get("codex_transcript_path") or ""))
        warnings: list[str] = []
        new_offset = int(state.get("last_counted_byte_offset") or 0)
        readable_delta = 0
        tool_delta = 0
        user_texts: list[str] = []
        message_count_delta = 0
        last_uid = str(state.get("last_counted_event_uid") or "")

        if transcript_path.is_file():
            new_offset, readable_delta, tool_delta, user_texts, message_count_delta, delta_last_uid = _read_transcript_delta(
                transcript_path,
                int(state.get("last_counted_byte_offset") or 0),
            )
            last_uid = delta_last_uid or last_uid
        else:
            warnings.append("transcript_unreadable")

        state["stops_since_memory_review"] = int(state.get("stops_since_memory_review") or 0) + 1
        state["readable_chars_since_memory_review"] = (
            int(state.get("readable_chars_since_memory_review") or 0) + readable_delta
        )
        state["tool_calls_since_skill_review"] = int(state.get("tool_calls_since_skill_review") or 0) + tool_delta
        state["last_counted_byte_offset"] = new_offset
        state["last_counted_message_index"] = int(state.get("last_counted_message_index") or 0) + message_count_delta
        state["last_counted_event_uid"] = last_uid

        matched_memory: list[str] = []
        matched_skill: list[str] = []
        matched_snippet = ""
        if bool(getattr(config, "high_signal_immediate", True)):
            for user_text in user_texts:
                memory_hits = (
                    _scan_keyword_group(user_text, MEMORY_KEYWORDS)
                    + _scan_keyword_group(user_text, HANDOFF_KEYWORDS)
                    + _scan_keyword_group(user_text, CORRECTION_KEYWORDS)
                )
                skill_hits = _scan_keyword_group(user_text, SKILL_KEYWORDS)
                if memory_hits or skill_hits:
                    matched_memory.extend(memory_hits)
                    matched_skill.extend(skill_hits)
                    if not matched_snippet:
                        matched_snippet = user_text[:160]

        reasons: list[str] = []
        review_memory = False
        review_skills = False
        if int(state["stops_since_memory_review"]) >= int(getattr(config, "memory_stop_interval", 3)):
            review_memory = True
            reasons.append("memory_stop_interval")
        if int(state["readable_chars_since_memory_review"]) >= int(getattr(config, "memory_context_chars", 16000)):
            review_memory = True
            reasons.append("memory_context_chars")
        if int(state["tool_calls_since_skill_review"]) >= int(getattr(config, "skill_tool_call_interval", 15)):
            review_skills = True
            reasons.append("skill_tool_call_interval")
        if matched_memory:
            review_memory = True
            reasons.append("high_signal_memory_keyword")
        if matched_skill:
            review_skills = True
            reasons.append("high_signal_skill_keyword")

        matched_keywords = sorted(set(matched_memory + matched_skill))
        active_or_reserved = active_job is not None or _state_has_fresh_active_reservation(state, config)
        if active_or_reserved and (review_memory or review_skills):
            decision = {
                "schema_version": 1,
                "status": "deferred_active_job",
                "review_memory": review_memory,
                "review_skills": review_skills,
                "trigger_reasons": reasons,
                "matched_keywords": matched_keywords,
                "matched_context_snippet": matched_snippet,
                "skip_reason": "active_job_running",
                "warnings": warnings,
                "counters": _counter_snapshot(state),
            }
        elif review_memory or review_skills:
            decision = {
                "schema_version": 1,
                "status": "queued",
                "review_memory": review_memory,
                "review_skills": review_skills,
                "trigger_reasons": reasons,
                "matched_keywords": matched_keywords,
                "matched_context_snippet": matched_snippet,
                "warnings": warnings,
                "counters": _counter_snapshot(state),
            }
            state["active_job_id"] = "pending"
        else:
            decision = {
                "schema_version": 1,
                "status": "archive_only",
                "review_memory": False,
                "review_skills": False,
                "trigger_reasons": [],
                "matched_keywords": [],
                "matched_context_snippet": "",
                "skip_reason": warnings[0] if warnings else "below_threshold",
                "warnings": warnings,
                "counters": _counter_snapshot(state),
            }

        state["last_decision"] = decision
        write_trigger_state(paths, state)
        append_decision(paths, decision)
        return {"status": decision["status"], "decision": decision, "state": state, "paths": paths}


def _counter_snapshot(state: dict[str, Any]) -> dict[str, int]:
    """Return the counters a queued job should later subtract from state."""
    return {
        "stops_since_memory_review": int(state.get("stops_since_memory_review") or 0),
        "readable_chars_since_memory_review": int(state.get("readable_chars_since_memory_review") or 0),
        "tool_calls_since_skill_review": int(state.get("tool_calls_since_skill_review") or 0),
    }


def _state_has_fresh_active_reservation(state: dict[str, Any], config: Any) -> bool:
    """Return whether trigger state still reserves an active job slot."""
    if not state.get("active_job_id"):
        return False
    try:
        updated_at = datetime.fromisoformat(str(state["updated_at"]).replace("Z", "+00:00"))
        age_seconds = (utc_now() - updated_at).total_seconds()
    except (KeyError, TypeError, ValueError):
        return True
    stale_after_seconds = int(getattr(config, "active_job_stale_seconds", 1800))
    if age_seconds > stale_after_seconds:
        state["active_job_id"] = None
        return False
    return True

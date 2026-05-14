from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..storage import atomic_write_json, load_json, utc_now
from .paths import SessionReflectionTriggerPaths, build_session_reflection_trigger_paths

DECISION_RETENTION = 200


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

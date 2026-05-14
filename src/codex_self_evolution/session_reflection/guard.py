from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import GuardDecision
from .state import child_thread_registry_path, find_existing_parent_job, global_lock_path

REFLECTION_MARKERS = ("CSEP_REFLECTION_JOB_ID=", "CSEP_REFLECTION_CHILD=1")
TRANSCRIPT_MARKER_READ_BYTES = 1024 * 1024


def evaluate_recursion_guard(payload: dict[str, Any], *, home: str | Path | None = None) -> GuardDecision:
    """Evaluate whether a Stop payload should start a reflection job."""
    thread_source = _payload_text(payload, "threadSource", "thread_source", "source")
    if thread_source == "memory_consolidation":
        return GuardDecision(True, "thread_source_memory_consolidation", thread_source)

    session_id = _payload_text(payload, "session_id", "thread_id")
    if session_id and child_thread_registry_path(session_id, home=home).is_file():
        return GuardDecision(True, "child_thread_registry", session_id)

    transcript_path = _payload_text(payload, "transcript_path", "codex_transcript_path")
    if transcript_path and _transcript_has_marker(Path(transcript_path)):
        return GuardDecision(True, "reflection_marker", transcript_path)

    if session_id and find_existing_parent_job(session_id, home=home):
        return GuardDecision(True, "parent_job_exists", session_id)

    lock_path = global_lock_path(home=home)
    if lock_path.exists():
        return GuardDecision(True, "global_lock", str(lock_path))

    return GuardDecision(False)


def _transcript_has_marker(path: Path) -> bool:
    """Check a bounded transcript prefix for reflection guard markers."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            text = handle.read(TRANSCRIPT_MARKER_READ_BYTES)
    except OSError:
        return False
    return any(marker in text for marker in REFLECTION_MARKERS)


def _payload_text(payload: dict[str, Any], *keys: str) -> str:
    """Read the first present payload field as text."""
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return str(value)
    return ""

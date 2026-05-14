from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import GuardDecision
from .state import child_thread_registry_path, global_lock_status

REFLECTION_MARKERS = ("CSEP_REFLECTION_JOB_ID=", "CSEP_REFLECTION_CHILD=1")
TRANSCRIPT_MARKER_CHUNK_SIZE = 64 * 1024


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

    lock = global_lock_status(home=home)
    if lock["locked"] and not lock["stale"]:
        return GuardDecision(True, "global_lock", str(lock["path"]))

    return GuardDecision(False)


def _transcript_has_marker(path: Path) -> bool:
    """Scan a transcript for reflection guard markers without loading it all."""
    overlap = max(len(marker) for marker in REFLECTION_MARKERS) - 1
    tail = ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            while True:
                chunk = handle.read(TRANSCRIPT_MARKER_CHUNK_SIZE)
                if not chunk:
                    return False
                text = tail + chunk
                if any(marker in text for marker in REFLECTION_MARKERS):
                    return True
                tail = text[-overlap:]
    except OSError:
        return False


def _payload_text(payload: dict[str, Any], *keys: str) -> str:
    """Read the first present payload field as text."""
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return str(value)
    return ""

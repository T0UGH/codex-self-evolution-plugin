from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..storage import atomic_write_json, load_json
from .paths import build_session_reflection_paths

ACTIVE_PARENT_STATUSES = {"queued", "running"}
FINDABLE_PARENT_STATUSES = ACTIVE_PARENT_STATUSES | {"succeeded"}
SESSION_REFLECTION_MODEL = "gpt-5.3-codex-spark"


def utc_timestamp() -> str:
    """Return a UTC timestamp suitable for persisted job metadata."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def latest_job_path(*, home: str | Path | None = None) -> Path:
    """Return the root latest-job pointer path."""
    return build_session_reflection_paths(home=home).root / "latest.json"


def global_lock_path(*, home: str | Path | None = None) -> Path:
    """Return the global session reflection lock path."""
    return build_session_reflection_paths(home=home).locks_dir / "global.lock"


def create_job_from_payload(payload: dict[str, Any], *, home: str | Path | None = None) -> dict[str, Any]:
    """Create and persist a queued reflection job from a raw Stop payload."""
    created_at = utc_timestamp()
    parent_session_id = _payload_text(payload, "session_id", "thread_id", default="unknown-session")
    parent_turn_id = _payload_text(payload, "turn_id")
    job_id = _new_job_id(created_at)
    paths = build_session_reflection_paths(home=home, job_id=job_id)
    job = {
        "schema_version": 1,
        "job_id": job_id,
        "parent_session_id": parent_session_id,
        "parent_turn_id": parent_turn_id,
        "parent_transcript_path": _payload_text(payload, "transcript_path", "codex_transcript_path"),
        "cwd": _payload_text(payload, "cwd"),
        "model": SESSION_REFLECTION_MODEL,
        "status": "queued",
        "created_at": created_at,
        "updated_at": created_at,
        "raw_payload": payload,
    }
    atomic_write_json(paths.job_path, job)
    atomic_write_json(latest_job_path(home=home), job)
    return job


def load_job(job_id: str, *, home: str | Path | None = None) -> dict[str, Any]:
    """Load one persisted reflection job by id."""
    raw = load_json(build_session_reflection_paths(home=home, job_id=job_id).job_path)
    if not isinstance(raw, dict):
        raise ValueError(f"job {job_id} is not a JSON object")
    return raw


def update_job_status(job_id: str, status: str, *, home: str | Path | None = None, **fields: Any) -> dict[str, Any]:
    """Update a job status, optional fields, and the latest-job pointer."""
    job = load_job(job_id, home=home)
    job.update(fields)
    job["status"] = status
    job["updated_at"] = utc_timestamp()
    paths = build_session_reflection_paths(home=home, job_id=job_id)
    atomic_write_json(paths.job_path, job)
    atomic_write_json(latest_job_path(home=home), job)
    return job


def list_jobs(*, home: str | Path | None = None) -> list[dict[str, Any]]:
    """Return readable job objects sorted by persisted filename."""
    jobs_dir = build_session_reflection_paths(home=home).jobs_dir
    if not jobs_dir.is_dir():
        return []
    jobs: list[dict[str, Any]] = []
    for path in sorted(jobs_dir.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(raw, dict):
            jobs.append(raw)
    return jobs


def find_existing_parent_job(parent_session_id: str, *, home: str | Path | None = None) -> dict[str, Any] | None:
    """Find active or succeeded reflection job for one parent session."""
    for job in reversed(list_jobs(home=home)):
        if job.get("parent_session_id") != parent_session_id:
            continue
        if job.get("status") in FINDABLE_PARENT_STATUSES:
            return job
    return None


def register_child_thread(
    *,
    child_thread_id: str,
    parent_session_id: str,
    job_id: str,
    home: str | Path | None = None,
) -> Path:
    """Persist child thread metadata used by the recursion guard."""
    path = child_thread_registry_path(child_thread_id, home=home)
    atomic_write_json(
        path,
        {
            "schema_version": 1,
            "child_thread_id": child_thread_id,
            "parent_session_id": parent_session_id,
            "job_id": job_id,
            "thread_source": "memory_consolidation",
            "created_at": utc_timestamp(),
        },
    )
    return path


def child_thread_registry_path(child_thread_id: str, *, home: str | Path | None = None) -> Path:
    """Return the registry path for a possible child thread id."""
    return build_session_reflection_paths(home=home).child_threads_dir / f"{child_thread_id}.json"


def write_global_lock(*, home: str | Path | None = None) -> Path:
    """Create the global reflection lock file for tests and later worker code."""
    path = global_lock_path(home=home)
    atomic_write_json(path, {"created_at": utc_timestamp(), "pid": os.getpid()})
    return path


def _new_job_id(created_at: str) -> str:
    """Build a sortable job id with a random suffix to avoid collisions."""
    compact = created_at.replace("-", "").replace(":", "").replace("Z", "Z")
    return f"{compact}-{uuid.uuid4().hex[:8]}"


def _payload_text(payload: dict[str, Any], *keys: str, default: str = "") -> str:
    """Read the first present payload field as text."""
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return str(value)
    return default

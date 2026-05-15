from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from ..config import DEFAULT_LOCK_STALE_SECONDS
from ..storage import _pid_alive, atomic_write_json, compute_stable_id, load_json, utc_now
from .paths import build_session_reflection_paths

ACTIVE_PARENT_STATUSES = {"queued", "running"}
SESSION_REFLECTION_MODEL = "gpt-5.3-codex-spark"


def utc_timestamp() -> str:
    """Return a UTC timestamp suitable for persisted job metadata."""
    return utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def latest_job_path(*, home: str | Path | None = None) -> Path:
    """Return the root latest-job pointer path."""
    return build_session_reflection_paths(home=home).root / "latest.json"


def global_lock_path(*, home: str | Path | None = None) -> Path:
    """Return the global session reflection lock path."""
    return build_session_reflection_paths(home=home).locks_dir / "global.lock"


def global_lock_status(
    *,
    home: str | Path | None = None,
    stale_after_seconds: int = DEFAULT_LOCK_STALE_SECONDS,
) -> dict[str, object]:
    """Return whether the global reflection lock exists and is stale."""
    path = global_lock_path(home=home)
    if not path.exists():
        return {"locked": False, "stale": False, "path": str(path)}
    try:
        raw = load_json(path)
        if not isinstance(raw, dict):
            raise ValueError("lock payload is not a JSON object")
        created_at = datetime.fromisoformat(str(raw["created_at"]).replace("Z", "+00:00"))
        owner_pid = raw.get("pid")
    except (KeyError, OSError, ValueError):
        return {
            "locked": True,
            "stale": False,
            "stale_reason": "unreadable_lock",
            "path": str(path),
        }
    age = (utc_now() - created_at).total_seconds()
    pid_alive = _pid_alive(owner_pid)
    # Future timestamps are treated as active locks; old timestamps and dead pids
    # are stale so a crashed worker cannot permanently disable reflection.
    stale = (not pid_alive) or age > stale_after_seconds
    stale_reason: str | None = None
    if not pid_alive:
        stale_reason = "pid_not_alive"
    elif age > stale_after_seconds:
        stale_reason = "exceeded_max_age"
    elif age < 0:
        stale_reason = "future_timestamp"
    return {
        "locked": True,
        "stale": stale,
        "stale_reason": stale_reason,
        "path": str(path),
        "age_seconds": age,
        "owner_pid": owner_pid,
        "pid_alive": pid_alive,
        "owner_token": raw.get("owner_token"),
    }


def create_job_from_payload(
    payload: dict[str, Any],
    *,
    home: str | Path | None = None,
    trigger_decision: dict[str, Any] | None = None,
    covered_byte_offset: int = 0,
    covered_message_index: int = 0,
    covered_event_uid: str = "",
    skill_generation_mode: str = "one_shot_active",
) -> dict[str, Any]:
    """Create and persist a queued reflection job from a raw Stop payload."""
    created_at = utc_timestamp()
    parent_session_id = _payload_text(payload, "session_id", "thread_id", default="unknown-session")
    parent_turn_id = _payload_text(payload, "turn_id")
    job_id = _new_job_id(created_at)
    paths = build_session_reflection_paths(home=home, job_id=job_id)
    decision_provided = trigger_decision is not None
    decision = trigger_decision if decision_provided else {}
    counters = decision.get("counters") if isinstance(decision.get("counters"), dict) else {}
    job = {
        "schema_version": 2 if decision_provided else 1,
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
    if decision_provided:
        job.update(
            {
                "review_memory": True,
                "review_skills": True,
                "trigger_reasons": list(decision.get("trigger_reasons") or []),
                "skill_generation_mode": skill_generation_mode,
                "covered_byte_offset": int(covered_byte_offset),
                "covered_message_index": int(covered_message_index),
                "covered_event_uid": str(covered_event_uid or ""),
                "counter_snapshot": {
                    "stops_since_memory_review": int(counters.get("stops_since_memory_review") or 0),
                    "readable_chars_since_memory_review": int(counters.get("readable_chars_since_memory_review") or 0),
                    "tool_calls_since_skill_review": int(counters.get("tool_calls_since_skill_review") or 0),
                },
                "trigger_decision": decision,
            }
        )
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


def find_active_parent_job(
    parent_session_id: str,
    *,
    home: str | Path | None = None,
    stale_after_seconds: int = DEFAULT_LOCK_STALE_SECONDS,
) -> dict[str, Any] | None:
    """Find a non-stale queued or running reflection job for one parent session."""
    for job in reversed(list_jobs(home=home)):
        if job.get("parent_session_id") != parent_session_id:
            continue
        if job.get("status") in ACTIVE_PARENT_STATUSES and not _job_is_stale(job, stale_after_seconds):
            return job
    return None


def find_existing_parent_job(parent_session_id: str, *, home: str | Path | None = None) -> dict[str, Any] | None:
    """Find the newest persisted reflection job for diagnostics."""
    for job in reversed(list_jobs(home=home)):
        if job.get("parent_session_id") == parent_session_id:
            return job
    return None


def _job_is_stale(job: dict[str, Any], stale_after_seconds: int) -> bool:
    """Return whether a job updated_at is old enough to ignore as active."""
    try:
        updated_at = datetime.fromisoformat(str(job["updated_at"]).replace("Z", "+00:00"))
        return (utc_now() - updated_at).total_seconds() > stale_after_seconds
    except (KeyError, TypeError, ValueError):
        return False


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
    filename = f"{compute_stable_id(child_thread_id)}.json"
    return build_session_reflection_paths(home=home).child_threads_dir / filename


def acquire_global_lock(
    *,
    home: str | Path | None = None,
    stale_after_seconds: int = DEFAULT_LOCK_STALE_SECONDS,
) -> dict[str, str]:
    """Atomically acquire the global reflection lock with an owner token."""
    path = global_lock_path(home=home)
    owner_token = uuid.uuid4().hex
    payload = {"created_at": utc_timestamp(), "pid": os.getpid(), "owner_token": owner_token}
    path.parent.mkdir(parents=True, exist_ok=True)
    with _global_lock_acquire_guard(path):
        try:
            _write_lock_exclusive(path, payload)
        except FileExistsError as exc:
            status = global_lock_status(home=home, stale_after_seconds=stale_after_seconds)
            if status["locked"] and not status["stale"]:
                raise ReflectionLockError(f"global reflection lock is active: {status['path']}") from exc
            try:
                path.unlink()
            except OSError as unlink_exc:
                raise ReflectionLockError(f"failed to remove stale reflection lock: {unlink_exc}") from unlink_exc
            try:
                _write_lock_exclusive(path, payload)
            except FileExistsError as second_exc:
                raise ReflectionLockError(f"global reflection lock is active: {path}") from second_exc
    return {"path": str(path), "owner_token": owner_token}


def release_global_lock(
    *,
    owner_token: str,
    home: str | Path | None = None,
) -> bool:
    """Release the global lock only if this owner still owns it."""
    path = global_lock_path(home=home)
    try:
        guard = _global_lock_acquire_guard(path)
    except ReflectionLockError:
        return False
    try:
        with guard:
            try:
                raw = load_json(path)
            except FileNotFoundError:
                return False
            except (OSError, ValueError):
                return False
            if not isinstance(raw, dict) or raw.get("owner_token") != owner_token:
                return False
            try:
                path.unlink()
            except FileNotFoundError:
                return False
            return True
    except ReflectionLockError:
        return False


class ReflectionLockError(RuntimeError):
    """Raised when the reflection global lock cannot be acquired."""


@contextmanager
def _global_lock_acquire_guard(path: Path) -> Iterator[None]:
    """Serialize lock acquisition so stale replacement cannot race itself."""
    import fcntl

    guard_path = path.with_name(f"{path.name}.acquire")
    with guard_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ReflectionLockError(f"global reflection lock acquisition is active: {guard_path}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_lock_exclusive(path: Path, payload: dict[str, str | int]) -> None:
    """Write a lock payload only when the target path does not already exist."""
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


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

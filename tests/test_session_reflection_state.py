from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

import pytest

from codex_self_evolution.storage import atomic_write_json, utc_now
from codex_self_evolution.session_reflection.state import (
    ReflectionLockError,
    acquire_global_lock,
    child_thread_registry_path,
    create_job_from_payload,
    find_existing_parent_job,
    global_lock_path,
    latest_job_path,
    register_child_thread,
    release_global_lock,
    update_job_status,
)


def _payload(repo: Path) -> dict[str, object]:
    """Return a raw Codex Stop payload fixture."""
    transcript = repo / "rollout.jsonl"
    transcript.write_text('{"role":"assistant","content":"done"}\n', encoding="utf-8")
    return {
        "session_id": "parent-1",
        "turn_id": "turn-1",
        "transcript_path": str(transcript),
        "cwd": str(repo),
        "hook_event_name": "Stop",
        "model": "gpt-5.4",
    }


def test_create_job_from_payload_writes_job_and_latest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    job = create_job_from_payload(_payload(repo), home=tmp_path)

    assert job["schema_version"] == 1
    assert job["parent_session_id"] == "parent-1"
    assert job["parent_turn_id"] == "turn-1"
    assert job["parent_transcript_path"] == str(repo / "rollout.jsonl")
    assert job["cwd"] == str(repo)
    assert job["model"] == "gpt-5.3-codex-spark"
    assert job["status"] == "queued"
    assert job["created_at"] == job["updated_at"]

    job_path = tmp_path / "session_reflection" / "jobs" / f"{job['job_id']}.json"
    assert json.loads(job_path.read_text(encoding="utf-8"))["job_id"] == job["job_id"]
    assert json.loads(latest_job_path(home=tmp_path).read_text(encoding="utf-8"))["job_id"] == job["job_id"]


def test_find_existing_parent_job_detects_queued_and_succeeded(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    created = create_job_from_payload(_payload(repo), home=tmp_path)

    assert find_existing_parent_job("parent-1", home=tmp_path)["job_id"] == created["job_id"]

    updated = update_job_status(created["job_id"], "succeeded", home=tmp_path, receipt_path="receipt.json")

    found = find_existing_parent_job("parent-1", home=tmp_path)
    assert found["status"] == "succeeded"
    assert found["receipt_path"] == "receipt.json"
    assert json.loads(latest_job_path(home=tmp_path).read_text(encoding="utf-8"))["status"] == updated["status"]


def test_find_existing_parent_job_ignores_failed_jobs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    created = create_job_from_payload(_payload(repo), home=tmp_path)
    update_job_status(created["job_id"], "failed", home=tmp_path)

    assert find_existing_parent_job("parent-1", home=tmp_path) is None


def test_register_child_thread_writes_registry(tmp_path: Path) -> None:
    registry = register_child_thread(
        child_thread_id="child-1",
        parent_session_id="parent-1",
        job_id="job-1",
        home=tmp_path,
    )

    data = json.loads(registry.read_text(encoding="utf-8"))
    assert data["child_thread_id"] == "child-1"
    assert data["parent_session_id"] == "parent-1"
    assert data["job_id"] == "job-1"
    assert data["thread_source"] == "memory_consolidation"


def test_register_child_thread_uses_path_safe_filename(tmp_path: Path) -> None:
    child_threads_dir = tmp_path / "session_reflection" / "child_threads"
    unsafe_id = "../nested/thread/1"

    registry = register_child_thread(
        child_thread_id=unsafe_id,
        parent_session_id="parent-1",
        job_id="job-1",
        home=tmp_path,
    )

    assert registry.parent == child_threads_dir
    assert registry == child_thread_registry_path(unsafe_id, home=tmp_path)
    assert registry.name != f"{unsafe_id}.json"
    assert json.loads(registry.read_text(encoding="utf-8"))["child_thread_id"] == unsafe_id


def test_acquire_global_lock_refuses_active_lock(tmp_path: Path) -> None:
    """Global lock acquisition fails instead of overwriting live owners."""
    atomic_write_json(
        global_lock_path(home=tmp_path),
        {
            "created_at": utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "pid": os.getpid(),
            "owner_token": "existing-owner",
        },
    )

    with pytest.raises(ReflectionLockError, match="global reflection lock is active"):
        acquire_global_lock(home=tmp_path)


def test_release_global_lock_requires_owner_token(tmp_path: Path) -> None:
    """Lock release preserves locks whose owner token changed."""
    lock = acquire_global_lock(home=tmp_path)
    atomic_write_json(
        global_lock_path(home=tmp_path),
        {
            "created_at": utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "pid": os.getpid(),
            "owner_token": "other-owner",
        },
    )

    assert release_global_lock(owner_token=lock["owner_token"], home=tmp_path) is False
    assert json.loads(global_lock_path(home=tmp_path).read_text(encoding="utf-8"))["owner_token"] == "other-owner"


def test_acquire_global_lock_does_not_remove_fresh_owner_during_stale_race(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A stale removal race preserves a fresh owner and reports contention."""
    path = global_lock_path(home=tmp_path)
    stale_created_at = utc_now() - timedelta(hours=2)
    atomic_write_json(
        path,
        {
            "created_at": stale_created_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "pid": os.getpid(),
            "owner_token": "stale-owner",
        },
    )
    real_link = os.link
    raced = False

    def racing_link(
        src: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        dst: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        """Replace the stale lock with a fresh owner just before claim link."""
        nonlocal raced
        if not raced:
            raced = True
            atomic_write_json(
                path,
                {
                    "created_at": utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "pid": os.getpid(),
                    "owner_token": "fresh-owner",
                },
            )
        real_link(src, dst)

    monkeypatch.setattr("codex_self_evolution.session_reflection.state.os.link", racing_link)

    with pytest.raises(ReflectionLockError, match="changed during stale replacement"):
        acquire_global_lock(home=tmp_path, stale_after_seconds=60)

    lock = json.loads(path.read_text(encoding="utf-8"))
    assert lock["owner_token"] == "fresh-owner"

from __future__ import annotations

import fcntl
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
    find_active_parent_job,
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


def test_find_active_parent_job_detects_only_queued_and_running(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    created = create_job_from_payload(_payload(repo), home=tmp_path)

    assert find_active_parent_job("parent-1", home=tmp_path)["job_id"] == created["job_id"]

    update_job_status(created["job_id"], "running", home=tmp_path)

    assert find_active_parent_job("parent-1", home=tmp_path)["job_id"] == created["job_id"]

    update_job_status(created["job_id"], "succeeded", home=tmp_path, receipt_path="receipt.json")

    assert find_active_parent_job("parent-1", home=tmp_path) is None

    update_job_status(created["job_id"], "failed", home=tmp_path)

    assert find_active_parent_job("parent-1", home=tmp_path) is None


def test_find_active_parent_job_ignores_stale_active_jobs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    created = create_job_from_payload(_payload(repo), home=tmp_path)
    job_path = tmp_path / "session_reflection" / "jobs" / f"{created['job_id']}.json"
    stale_job = {
        **json.loads(job_path.read_text(encoding="utf-8")),
        "updated_at": _timestamp(utc_now() - timedelta(seconds=61)),
    }
    atomic_write_json(job_path, stale_job)

    assert find_active_parent_job("parent-1", home=tmp_path, stale_after_seconds=60) is None

    stale_job["updated_at"] = "not-a-timestamp"
    atomic_write_json(job_path, stale_job)

    assert find_active_parent_job("parent-1", home=tmp_path, stale_after_seconds=60)["job_id"] == created["job_id"]


def test_find_active_parent_job_treats_naive_updated_at_as_active(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    created = create_job_from_payload(_payload(repo), home=tmp_path)
    job_path = tmp_path / "session_reflection" / "jobs" / f"{created['job_id']}.json"
    job = {
        **json.loads(job_path.read_text(encoding="utf-8")),
        "updated_at": utc_now().replace(microsecond=0, tzinfo=None).isoformat(),
    }
    atomic_write_json(job_path, job)

    assert find_active_parent_job("parent-1", home=tmp_path)["job_id"] == created["job_id"]


def test_find_active_parent_job_skips_newest_stale_and_returns_older_fresh_job(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    older = create_job_from_payload(_payload(repo), home=tmp_path)
    newest = create_job_from_payload(_payload(repo), home=tmp_path)
    jobs_dir = tmp_path / "session_reflection" / "jobs"
    fresh_job = {
        **json.loads((jobs_dir / f"{older['job_id']}.json").read_text(encoding="utf-8")),
        "status": "running",
        "updated_at": _timestamp(utc_now()),
    }
    stale_job = {
        **json.loads((jobs_dir / f"{newest['job_id']}.json").read_text(encoding="utf-8")),
        "status": "queued",
        "updated_at": _timestamp(utc_now() - timedelta(seconds=61)),
    }
    atomic_write_json(jobs_dir / f"{older['job_id']}.json", fresh_job)
    atomic_write_json(jobs_dir / f"{newest['job_id']}.json", stale_job)

    found = find_active_parent_job("parent-1", home=tmp_path, stale_after_seconds=60)

    assert found["job_id"] == older["job_id"]


def test_create_job_from_payload_accepts_trigger_decision_fields(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    decision = {
        "status": "queued",
        "review_memory": True,
        "review_skills": True,
        "trigger_reasons": ["high_signal_skill_keyword"],
        "counters": {
            "stops_since_memory_review": 2,
            "readable_chars_since_memory_review": 9300,
            "tool_calls_since_skill_review": 11,
        },
    }

    job = create_job_from_payload(
        _payload(repo),
        home=tmp_path,
        trigger_decision=decision,
        covered_byte_offset=123,
        covered_message_index=4,
        covered_event_uid="event-1",
        skill_generation_mode="one_shot_active",
    )

    assert job["schema_version"] == 2
    assert job["review_memory"] is True
    assert job["review_skills"] is True
    assert job["trigger_reasons"] == ["high_signal_skill_keyword"]
    assert job["skill_generation_mode"] == "one_shot_active"
    assert job["covered_byte_offset"] == 123
    assert job["covered_message_index"] == 4
    assert job["covered_event_uid"] == "event-1"
    assert job["counter_snapshot"]["tool_calls_since_skill_review"] == 11
    assert job["trigger_decision"] == decision


def test_create_job_from_payload_accepts_empty_trigger_decision(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    job = create_job_from_payload(_payload(repo), home=tmp_path, trigger_decision={})

    assert job["schema_version"] == 2
    assert job["review_memory"] is False
    assert job["review_skills"] is False
    assert job["trigger_reasons"] == []
    assert job["skill_generation_mode"] == "one_shot_active"
    assert job["covered_byte_offset"] == 0
    assert job["covered_message_index"] == 0
    assert job["covered_event_uid"] == ""
    assert job["counter_snapshot"] == {
        "stops_since_memory_review": 0,
        "readable_chars_since_memory_review": 0,
        "tool_calls_since_skill_review": 0,
    }
    assert job["trigger_decision"] == {}


def test_find_existing_parent_job_returns_newest_persisted_job(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    created = create_job_from_payload(_payload(repo), home=tmp_path)
    updated = update_job_status(created["job_id"], "failed", home=tmp_path, receipt_path="receipt.json")

    found = find_existing_parent_job("parent-1", home=tmp_path)

    assert found["status"] == "failed"
    assert found["receipt_path"] == "receipt.json"
    assert json.loads(latest_job_path(home=tmp_path).read_text(encoding="utf-8"))["status"] == updated["status"]


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


def test_release_global_lock_refuses_while_acquisition_guard_is_held(tmp_path: Path) -> None:
    """Lock release cannot race with another acquisition replacing the lock."""
    lock = acquire_global_lock(home=tmp_path)
    path = global_lock_path(home=tmp_path)
    guard_path = path.with_name(f"{path.name}.acquire")

    with guard_path.open("a+", encoding="utf-8") as guard:
        fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert release_global_lock(owner_token=lock["owner_token"], home=tmp_path) is False
        fcntl.flock(guard.fileno(), fcntl.LOCK_UN)

    assert json.loads(path.read_text(encoding="utf-8"))["owner_token"] == lock["owner_token"]
    assert release_global_lock(owner_token=lock["owner_token"], home=tmp_path) is True


def test_acquire_global_lock_refuses_while_acquisition_guard_is_held(tmp_path: Path) -> None:
    """Stale lock replacement is serialized by a separate acquisition guard."""
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
    guard_path = path.with_name(f"{path.name}.acquire")
    guard_path.parent.mkdir(parents=True, exist_ok=True)

    with guard_path.open("a+", encoding="utf-8") as guard:
        fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ReflectionLockError, match="acquisition is active"):
            acquire_global_lock(home=tmp_path, stale_after_seconds=60)
        fcntl.flock(guard.fileno(), fcntl.LOCK_UN)

    lock = acquire_global_lock(home=tmp_path, stale_after_seconds=60)
    assert json.loads(path.read_text(encoding="utf-8"))["owner_token"] == lock["owner_token"]


def _timestamp(value) -> str:
    """Format a timezone-aware datetime for persisted fixtures."""
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")

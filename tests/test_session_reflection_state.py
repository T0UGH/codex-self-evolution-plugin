from __future__ import annotations

import json
from pathlib import Path

from codex_self_evolution.session_reflection.state import (
    create_job_from_payload,
    find_existing_parent_job,
    latest_job_path,
    register_child_thread,
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
    register_child_thread(
        child_thread_id="child-1",
        parent_session_id="parent-1",
        job_id="job-1",
        home=tmp_path,
    )

    registry = tmp_path / "session_reflection" / "child_threads" / "child-1.json"
    data = json.loads(registry.read_text(encoding="utf-8"))
    assert data["child_thread_id"] == "child-1"
    assert data["parent_session_id"] == "parent-1"
    assert data["job_id"] == "job-1"
    assert data["thread_source"] == "memory_consolidation"

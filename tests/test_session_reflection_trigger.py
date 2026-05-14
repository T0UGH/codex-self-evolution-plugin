from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_self_evolution.session_reflection.trigger import (
    TriggerLockBusy,
    append_decision,
    load_trigger_state,
    reset_counters_after_job,
    session_trigger_lock,
    trigger_paths_for_payload,
    write_trigger_state,
)


def _payload(repo: Path, **overrides: object) -> dict[str, object]:
    """Return a minimal Stop payload for trigger sidecar tests."""
    transcript = repo / "rollout.jsonl"
    transcript.write_text("", encoding="utf-8")
    payload: dict[str, object] = {
        "session_id": "parent-1",
        "turn_id": "turn-1",
        "transcript_path": str(transcript),
        "cwd": str(repo),
    }
    payload.update(overrides)
    return payload


def test_load_trigger_state_defaults(tmp_path: Path) -> None:
    """Missing trigger state loads deterministic defaults."""
    repo = tmp_path / "repo"
    repo.mkdir()
    paths = trigger_paths_for_payload(_payload(repo), home=tmp_path)

    state = load_trigger_state(paths, session_id="parent-1")

    assert state["schema_version"] == 1
    assert state["session_id"] == "parent-1"
    assert state["last_counted_byte_offset"] == 0
    assert state["stops_since_memory_review"] == 0
    assert state["readable_chars_since_memory_review"] == 0
    assert state["tool_calls_since_skill_review"] == 0
    assert state["active_job_id"] is None


def test_write_trigger_state_and_append_decision(tmp_path: Path) -> None:
    """State writes atomically and decisions append as JSONL rows."""
    repo = tmp_path / "repo"
    repo.mkdir()
    paths = trigger_paths_for_payload(_payload(repo), home=tmp_path)
    state = load_trigger_state(paths, session_id="parent-1")
    state["stops_since_memory_review"] = 2

    write_trigger_state(paths, state)
    append_decision(paths, {"status": "archive_only", "skip_reason": "below_threshold"})

    assert json.loads(paths.state_path.read_text(encoding="utf-8"))["stops_since_memory_review"] == 2
    lines = paths.decisions_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["decision"]["skip_reason"] == "below_threshold"


def test_append_decision_retains_last_200_entries(tmp_path: Path) -> None:
    """Decision history keeps only the configured retention window."""
    repo = tmp_path / "repo"
    repo.mkdir()
    paths = trigger_paths_for_payload(_payload(repo), home=tmp_path)

    for idx in range(205):
        append_decision(paths, {"status": "archive_only", "idx": idx})

    rows = [json.loads(line) for line in paths.decisions_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 200
    assert rows[0]["decision"]["idx"] == 5
    assert rows[-1]["decision"]["idx"] == 204


def test_session_trigger_lock_is_non_blocking(tmp_path: Path) -> None:
    """Nested acquisition fails immediately instead of blocking."""
    repo = tmp_path / "repo"
    repo.mkdir()
    paths = trigger_paths_for_payload(_payload(repo), home=tmp_path)

    with session_trigger_lock(paths):
        with pytest.raises(TriggerLockBusy):
            with session_trigger_lock(paths):
                raise AssertionError("nested lock should not be acquired")


def test_reset_counters_after_job_uses_snapshot_subtraction(tmp_path: Path) -> None:
    """Successful scopes subtract the job snapshot while failed scopes remain."""
    state = {
        "stops_since_memory_review": 5,
        "readable_chars_since_memory_review": 22000,
        "tool_calls_since_skill_review": 18,
        "last_memory_review_at": None,
        "last_skill_review_at": None,
        "active_job_id": "job-1",
    }
    job = {
        "job_id": "job-1",
        "review_memory": True,
        "review_skills": True,
        "counter_snapshot": {
            "stops_since_memory_review": 3,
            "readable_chars_since_memory_review": 16000,
            "tool_calls_since_skill_review": 15,
        },
    }

    updated = reset_counters_after_job(
        state,
        job,
        memory_succeeded=True,
        skill_succeeded=False,
        now="2026-05-15T00:00:00Z",
    )

    assert updated["stops_since_memory_review"] == 2
    assert updated["readable_chars_since_memory_review"] == 6000
    assert updated["tool_calls_since_skill_review"] == 18
    assert updated["last_memory_review_at"] == "2026-05-15T00:00:00Z"
    assert updated["last_skill_review_at"] is None
    assert updated["active_job_id"] is None

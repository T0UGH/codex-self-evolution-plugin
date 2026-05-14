from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from codex_self_evolution.config import DEFAULT_LOCK_STALE_SECONDS
from codex_self_evolution.session_reflection.guard import evaluate_recursion_guard
from codex_self_evolution.session_reflection.state import (
    create_job_from_payload,
    global_lock_path,
    register_child_thread,
)
from codex_self_evolution.storage import atomic_write_json, utc_now


def _payload(repo: Path, **overrides: object) -> dict[str, object]:
    """Return a raw Codex Stop payload with optional overrides."""
    transcript = repo / "rollout.jsonl"
    transcript.write_text('{"role":"assistant","content":"normal"}\n', encoding="utf-8")
    payload: dict[str, object] = {
        "session_id": "parent-1",
        "turn_id": "turn-1",
        "transcript_path": str(transcript),
        "cwd": str(repo),
        "hook_event_name": "Stop",
        "model": "gpt-5.4",
    }
    payload.update(overrides)
    return payload


def test_guard_skips_memory_consolidation_source_variants(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    for key in ("threadSource", "thread_source", "source"):
        decision = evaluate_recursion_guard(
            _payload(repo, **{key: "memory_consolidation"}),
            home=tmp_path,
        )

        assert decision.skip is True
        assert decision.reason == "thread_source_memory_consolidation"


def test_guard_skips_child_registry_hit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    register_child_thread(
        child_thread_id="parent-1",
        parent_session_id="root-parent",
        job_id="job-1",
        home=tmp_path,
    )

    decision = evaluate_recursion_guard(_payload(repo), home=tmp_path)

    assert decision.skip is True
    assert decision.reason == "child_thread_registry"


def test_guard_skips_thread_id_child_registry_hit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    register_child_thread(
        child_thread_id="thread-1",
        parent_session_id="root-parent",
        job_id="job-1",
        home=tmp_path,
    )

    decision = evaluate_recursion_guard(_payload(repo, session_id=None, thread_id="thread-1"), home=tmp_path)

    assert decision.skip is True
    assert decision.reason == "child_thread_registry"


def test_guard_skips_path_safe_child_registry_hit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    unsafe_id = "../nested/thread/1"
    registry = register_child_thread(
        child_thread_id=unsafe_id,
        parent_session_id="root-parent",
        job_id="job-1",
        home=tmp_path,
    )

    decision = evaluate_recursion_guard(_payload(repo, session_id=unsafe_id), home=tmp_path)

    assert registry.parent == tmp_path / "session_reflection" / "child_threads"
    assert decision.skip is True
    assert decision.reason == "child_thread_registry"


def test_guard_skips_transcript_marker(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    transcript = Path(str(payload["transcript_path"]))
    transcript.write_text('{"role":"user","content":"CSEP_REFLECTION_CHILD=1"}\n', encoding="utf-8")

    decision = evaluate_recursion_guard(
        payload,
        home=tmp_path,
    )

    assert decision.skip is True
    assert decision.reason == "reflection_marker"


def test_guard_scans_transcript_marker_after_one_mib(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    transcript = Path(str(payload["transcript_path"]))
    transcript.write_text(("x" * (1024 * 1024 + 10)) + "CSEP_REFLECTION_CHILD=1\n", encoding="utf-8")

    decision = evaluate_recursion_guard(payload, home=tmp_path)

    assert decision.skip is True
    assert decision.reason == "reflection_marker"


def test_guard_skips_alternate_transcript_marker(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo, transcript_path=None, codex_transcript_path=str(repo / "alternate.jsonl"))
    transcript = Path(str(payload["codex_transcript_path"]))
    transcript.write_text("CSEP_REFLECTION_JOB_ID=job-1\n", encoding="utf-8")

    decision = evaluate_recursion_guard(
        payload,
        home=tmp_path,
    )

    assert decision.skip is True
    assert decision.reason == "reflection_marker"


def test_recursion_guard_does_not_skip_existing_parent_job(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    create_job_from_payload(payload, home=tmp_path)

    decision = evaluate_recursion_guard(payload, home=tmp_path)

    assert decision.skip is False


def test_guard_skips_live_current_global_lock(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    atomic_write_json(global_lock_path(home=tmp_path), {"created_at": _timestamp(utc_now()), "pid": os.getpid()})

    decision = evaluate_recursion_guard(_payload(repo), home=tmp_path)

    assert decision.skip is True
    assert decision.reason == "global_lock"


def test_guard_allows_stale_old_global_lock(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    created_at = utc_now() - timedelta(seconds=DEFAULT_LOCK_STALE_SECONDS + 1)
    atomic_write_json(global_lock_path(home=tmp_path), {"created_at": _timestamp(created_at), "pid": os.getpid()})

    decision = evaluate_recursion_guard(_payload(repo), home=tmp_path)

    assert decision.skip is False
    assert decision.reason == ""


def test_guard_allows_dead_pid_global_lock(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    atomic_write_json(global_lock_path(home=tmp_path), {"created_at": _timestamp(utc_now()), "pid": 2_147_483_646})

    decision = evaluate_recursion_guard(_payload(repo), home=tmp_path)

    assert decision.skip is False
    assert decision.reason == ""


def test_guard_skips_future_timestamp_global_lock(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    created_at = utc_now() + timedelta(seconds=DEFAULT_LOCK_STALE_SECONDS + 1)
    atomic_write_json(global_lock_path(home=tmp_path), {"created_at": _timestamp(created_at), "pid": os.getpid()})

    decision = evaluate_recursion_guard(_payload(repo), home=tmp_path)

    assert decision.skip is True
    assert decision.reason == "global_lock"


def test_guard_allows_normal_parent(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    decision = evaluate_recursion_guard(_payload(repo), home=tmp_path)

    assert decision.skip is False
    assert decision.reason == ""
    assert decision.detail == ""


def _timestamp(value) -> str:
    """Format a timezone-aware datetime for lock fixtures."""
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")

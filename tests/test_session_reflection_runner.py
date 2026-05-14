from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from codex_self_evolution.session_reflection.runner import (
    enqueue_reflection_from_payload,
    run_reflection_job,
    session_reflection_status,
)
from codex_self_evolution.session_reflection.state import (
    child_thread_registry_path,
    create_job_from_payload,
    global_lock_path,
)


class FakeReflectionClient:
    """Fake app-server client that records calls and writes a receipt."""

    def __init__(self, *, fail_start: bool = False) -> None:
        """Configure whether turn/start raises after fork registration."""
        self.fail_start = fail_start
        self.fork_calls: list[dict[str, Any]] = []
        self.start_calls: list[dict[str, Any]] = []

    def fork_thread(self, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        """Return a deterministic child thread id."""
        self.fork_calls.append(kwargs)
        return "child-1", {"threadId": "child-1"}

    def start_reflection_turn(self, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        """Write the required receipt and return a deterministic turn id."""
        self.start_calls.append(kwargs)
        if self.fail_start:
            raise RuntimeError("turn failed")
        receipt_path = _receipt_path_from_prompt(str(kwargs["prompt"]))
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "job_id": _line_value(str(kwargs["prompt"]), "CSEP_REFLECTION_JOB_ID="),
                    "parent_session_id": _line_value(str(kwargs["prompt"]), "Parent session id: "),
                    "child_thread_id": "child-1",
                    "status": "succeeded",
                    "memory_changes": [],
                    "skill_changes": [],
                    "skipped_candidates": [],
                    "validation_notes": [],
                    "errors": [],
                    "started_at": "2026-05-14T12:00:00Z",
                    "finished_at": "2026-05-14T12:01:00Z",
                }
            ),
            encoding="utf-8",
        )
        return "turn-1", {"turnId": "turn-1"}


def _payload(repo: Path, **overrides: object) -> dict[str, object]:
    """Build a Stop payload fixture for runner tests."""
    transcript = repo / "rollout.jsonl"
    transcript.write_text('{"role":"assistant","content":"done"}\n', encoding="utf-8")
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


def _write_config(home: Path, text: str) -> None:
    """Write a config.toml fixture."""
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")


def _receipt_path_from_prompt(prompt: str) -> Path:
    """Extract the required receipt path from the child prompt."""
    return Path(_line_value(prompt, "Required receipt path: "))


def _line_value(text: str, prefix: str) -> str:
    """Read the value that follows a line prefix."""
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    raise AssertionError(f"missing prompt line prefix: {prefix}")


def test_enqueue_reflection_from_payload_creates_job(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A normal Stop payload becomes a queued reflection job."""
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))

    result = enqueue_reflection_from_payload(_payload(repo), home=tmp_path / "home")

    assert result["status"] == "queued"
    job = result["job"]
    assert job["status"] == "queued"
    assert job["parent_session_id"] == "parent-1"


def test_enqueue_reflection_from_payload_skips_when_disabled(tmp_path: Path) -> None:
    """Disabled session reflection does not create a job."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_config(home, "[session_reflection]\nenabled = false\n")

    result = enqueue_reflection_from_payload(_payload(repo), home=home)

    assert result == {"status": "skipped", "reason": "disabled"}
    assert not (home / "session_reflection" / "jobs").exists()


def test_enqueue_reflection_from_payload_skips_guarded_child_source(tmp_path: Path) -> None:
    """Recursion guard decisions are surfaced as skipped enqueue results."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()

    result = enqueue_reflection_from_payload(
        _payload(repo, threadSource="memory_consolidation"),
        home=home,
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "thread_source_memory_consolidation"


def test_run_reflection_job_forks_starts_registers_validates_and_cleans_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Runner performs the full foreground orchestration with a fake client."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))
    monkeypatch.setenv("CSEP_CODEX_SKILLS_DIR", str(tmp_path / "skills"))
    job = create_job_from_payload(_payload(repo), home=home)
    client = FakeReflectionClient()

    updated = run_reflection_job(str(job["job_id"]), home=home, client=client)

    assert updated["status"] == "skipped_empty"
    assert updated["child_thread_id"] == "child-1"
    assert updated["turn_id"] == "turn-1"
    assert updated["validation"]["status"] == "skipped_empty"
    assert client.fork_calls == [
        {
            "parent_thread_id": "parent-1",
            "transcript_path": str(repo / "rollout.jsonl"),
            "model": "gpt-5.3-codex-spark",
            "cwd": str(repo),
            "ephemeral": True,
            "sandbox": "danger-full-access",
            "approval_policy": "never",
        }
    ]
    assert client.start_calls[0]["child_thread_id"] == "child-1"
    assert "Required receipt path: " in client.start_calls[0]["prompt"]
    assert child_thread_registry_path("child-1", home=home).is_file()
    assert (home / "session_reflection" / "runs" / str(job["job_id"]) / "prompt.txt").is_file()
    assert (home / "session_reflection" / "runs" / str(job["job_id"]) / "validation.json").is_file()
    assert not global_lock_path(home=home).exists()


def test_run_reflection_job_marks_failed_and_cleans_lock_on_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A child turn exception marks the job failed and removes the lock."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))
    monkeypatch.setenv("CSEP_CODEX_SKILLS_DIR", str(tmp_path / "skills"))
    job = create_job_from_payload(_payload(repo), home=home)

    updated = run_reflection_job(str(job["job_id"]), home=home, client=FakeReflectionClient(fail_start=True))

    assert updated["status"] == "failed"
    assert "turn failed" in updated["error"]
    assert child_thread_registry_path("child-1", home=home).is_file()
    assert not global_lock_path(home=home).exists()


def test_session_reflection_status_is_compact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Status reports root existence, latest job, and lock state."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))
    job = create_job_from_payload(_payload(repo), home=home)

    status = session_reflection_status(home=home)

    assert status["root"] == str(home / "session_reflection")
    assert status["exists"] is True
    assert status["latest"]["job_id"] == job["job_id"]
    assert status["latest"]["status"] == "queued"
    assert status["global_lock"]["locked"] is False

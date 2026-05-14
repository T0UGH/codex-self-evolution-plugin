from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from codex_self_evolution.storage import atomic_write_json, utc_now
from codex_self_evolution.session_reflection.runner import (
    _build_project_paths_for_home,
    enqueue_reflection_from_payload,
    run_reflection_job,
    session_reflection_status,
)
from codex_self_evolution.session_reflection.state import (
    child_thread_registry_path,
    create_job_from_payload,
    global_lock_path,
    update_job_status,
)


class FakeReflectionClient:
    """Fake app-server client that records calls and writes a receipt."""

    def __init__(
        self,
        *,
        fail_start: bool = False,
        receipt_child_thread_id: str = "child-1",
        memory_changes: list[dict[str, Any]] | None = None,
        skill_changes: list[dict[str, Any]] | None = None,
        steal_lock_home: Path | None = None,
    ) -> None:
        """Configure whether turn/start raises after fork registration."""
        self.fail_start = fail_start
        self.receipt_child_thread_id = receipt_child_thread_id
        self.memory_changes = memory_changes if memory_changes is not None else []
        self.skill_changes = skill_changes if skill_changes is not None else []
        self.steal_lock_home = steal_lock_home
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
                    "child_thread_id": self.receipt_child_thread_id,
                    "status": "succeeded",
                    "memory_changes": self.memory_changes,
                    "skill_changes": self.skill_changes,
                    "skipped_candidates": [],
                    "validation_notes": [],
                    "errors": [],
                    "started_at": "2026-05-14T12:00:00Z",
                    "finished_at": "2026-05-14T12:01:00Z",
                }
            ),
            encoding="utf-8",
        )
        if self.steal_lock_home is not None:
            atomic_write_json(
                global_lock_path(home=self.steal_lock_home),
                {
                    "created_at": utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "pid": os.getpid(),
                    "owner_token": "other-owner",
                },
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


def _sha(path: Path) -> str:
    """Return a sha256 digest for a receipt fixture file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _receipt_path_from_prompt(prompt: str) -> Path:
    """Extract the required receipt path from the child prompt."""
    return Path(_line_value(prompt, "Required receipt path: "))


def _line_value(text: str, prefix: str) -> str:
    """Read the value that follows a line prefix."""
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    raise AssertionError(f"missing prompt line prefix: {prefix}")


def test_enqueue_reflection_from_payload_archives_only_below_threshold(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Low-signal Stop payloads update trigger state without creating a job."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))

    result = enqueue_reflection_from_payload(_payload(repo), home=home)

    assert result["status"] == "archive_only"
    assert result["decision"]["skip_reason"] == "below_threshold"
    assert not (home / "session_reflection" / "jobs").exists()


def test_enqueue_reflection_from_payload_queues_when_trigger_hits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """High-signal user keywords create a trigger-scoped reflection job."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    Path(str(payload["transcript_path"])).write_text(
        json.dumps({"type": "response_item", "payload": {"role": "user", "content": "请把这个工作流沉淀成 skill"}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))

    result = enqueue_reflection_from_payload(payload, home=home)

    assert result["status"] == "queued"
    job = result["job"]
    assert job["status"] == "queued"
    assert job["parent_session_id"] == "parent-1"
    assert job["schema_version"] == 2
    assert job["review_skills"] is True
    assert job["skill_generation_mode"] == "one_shot_active"
    assert job["trigger_decision"]["matched_keywords"] == ["skill", "工作流", "沉淀"]


def test_enqueue_reflection_from_payload_defers_when_active_job_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Active parent jobs prevent a second fork but do not skip trigger accounting."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    create_job_from_payload(payload, home=home)
    Path(str(payload["transcript_path"])).write_text(
        json.dumps({"type": "response_item", "payload": {"role": "user", "content": "记住这个规则"}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))

    result = enqueue_reflection_from_payload(payload, home=home)

    assert result["status"] == "deferred_active_job"
    assert result["decision"]["skip_reason"] == "active_job_running"


def test_enqueue_reflection_from_payload_skips_when_disabled(tmp_path: Path) -> None:
    """Disabled session reflection does not create a job."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_config(home, "[session_reflection]\nenabled = false\n")

    result = enqueue_reflection_from_payload(_payload(repo), home=home)

    assert result == {"status": "skipped", "reason": "disabled"}
    assert not (home / "session_reflection" / "jobs").exists()


def test_enqueue_reflection_from_payload_archives_when_trigger_disabled(tmp_path: Path) -> None:
    """Disabled trigger policy archives Stop payloads without evaluating or queuing."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_config(home, "[session_reflection.trigger]\nenabled = false\n")
    payload = _payload(repo)
    Path(str(payload["transcript_path"])).write_text(
        json.dumps({"type": "response_item", "payload": {"role": "user", "content": "请把这个工作流沉淀成 skill"}}) + "\n",
        encoding="utf-8",
    )

    result = enqueue_reflection_from_payload(payload, home=home)

    assert result["status"] == "archive_only"
    assert result["decision"]["skip_reason"] == "trigger_disabled"
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
    assert "Review memory: true" in client.start_calls[0]["prompt"]
    assert "Review skills: true" in client.start_calls[0]["prompt"]
    assert "Skill generation mode: one_shot_active" in client.start_calls[0]["prompt"]
    assert child_thread_registry_path("child-1", home=home).is_file()
    assert (home / "session_reflection" / "runs" / str(job["job_id"]) / "prompt.txt").is_file()
    assert (home / "session_reflection" / "runs" / str(job["job_id"]) / "validation.json").is_file()
    assert not global_lock_path(home=home).exists()


def test_run_reflection_job_resets_trigger_counters_after_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Successful validation subtracts the job counter snapshot."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))
    monkeypatch.setenv("CSEP_CODEX_SKILLS_DIR", str(tmp_path / "skills"))
    payload = _payload(repo)
    decision = {
        "status": "queued",
        "review_memory": True,
        "review_skills": False,
        "trigger_reasons": ["memory_stop_interval"],
        "counters": {
            "stops_since_memory_review": 3,
            "readable_chars_since_memory_review": 9000,
            "tool_calls_since_skill_review": 0,
        },
    }
    job = create_job_from_payload(
        payload,
        home=home,
        trigger_decision=decision,
        skill_generation_mode="one_shot_active",
    )
    from codex_self_evolution.session_reflection.trigger import (
        load_trigger_state,
        trigger_paths_for_payload,
        write_trigger_state,
    )

    trigger_paths = trigger_paths_for_payload(payload, home=home)
    state = load_trigger_state(trigger_paths, session_id="parent-1")
    state["active_job_id"] = job["job_id"]
    state["stops_since_memory_review"] = 4
    state["readable_chars_since_memory_review"] = 12000
    write_trigger_state(trigger_paths, state)

    run_reflection_job(str(job["job_id"]), home=home, client=FakeReflectionClient())

    updated = load_trigger_state(trigger_paths, session_id="parent-1")
    assert updated["stops_since_memory_review"] == 1
    assert updated["readable_chars_since_memory_review"] == 3000
    assert updated["active_job_id"] is None


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


def test_run_reflection_job_turn_exception_clears_active_job_without_counter_subtraction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pre-validation exceptions release the active job reservation without resetting counters."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))
    monkeypatch.setenv("CSEP_CODEX_SKILLS_DIR", str(tmp_path / "skills"))
    payload = _payload(repo)
    decision = {
        "status": "queued",
        "review_memory": True,
        "review_skills": True,
        "trigger_reasons": ["memory_stop_interval", "skill_tool_interval"],
        "counters": {
            "stops_since_memory_review": 3,
            "readable_chars_since_memory_review": 9000,
            "tool_calls_since_skill_review": 5,
        },
    }
    job = create_job_from_payload(
        payload,
        home=home,
        trigger_decision=decision,
        skill_generation_mode="one_shot_active",
    )
    from codex_self_evolution.session_reflection.trigger import (
        load_trigger_state,
        trigger_paths_for_payload,
        write_trigger_state,
    )

    trigger_paths = trigger_paths_for_payload(payload, home=home)
    state = load_trigger_state(trigger_paths, session_id="parent-1")
    state["active_job_id"] = job["job_id"]
    state["stops_since_memory_review"] = 4
    state["readable_chars_since_memory_review"] = 12000
    state["tool_calls_since_skill_review"] = 7
    write_trigger_state(trigger_paths, state)

    run_reflection_job(str(job["job_id"]), home=home, client=FakeReflectionClient(fail_start=True))

    updated = load_trigger_state(trigger_paths, session_id="parent-1")
    assert updated["stops_since_memory_review"] == 4
    assert updated["readable_chars_since_memory_review"] == 12000
    assert updated["tool_calls_since_skill_review"] == 7
    assert updated["active_job_id"] is None


def test_run_reflection_job_uses_explicit_home_for_memory_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Runner memory paths use home= without requiring a global env override."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.delenv("CODEX_SELF_EVOLUTION_HOME", raising=False)
    monkeypatch.setenv("CSEP_CODEX_SKILLS_DIR", str(tmp_path / "skills"))
    job = create_job_from_payload(_payload(repo), home=home)
    client = FakeReflectionClient()

    run_reflection_job(str(job["job_id"]), home=home, client=client)

    prompt = client.start_calls[0]["prompt"]
    assert f"User memory file: {home / 'projects'}" in prompt
    assert f"Project memory file: {home / 'projects'}" in prompt
    assert "/memory/USER.md" in prompt
    assert "/memory/MEMORY.md" in prompt


def test_run_reflection_job_fails_wrong_child_receipt_id_and_cleans_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Receipt validation binds the receipt to the current child thread."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))
    monkeypatch.setenv("CSEP_CODEX_SKILLS_DIR", str(tmp_path / "skills"))
    job = create_job_from_payload(_payload(repo), home=home)

    updated = run_reflection_job(
        str(job["job_id"]),
        home=home,
        client=FakeReflectionClient(receipt_child_thread_id="wrong-child"),
    )

    assert updated["status"] == "failed"
    assert updated["validation"]["reason"] == "child_thread_id_mismatch"
    assert not global_lock_path(home=home).exists()


def test_run_reflection_job_failed_validation_clears_active_job_without_counter_subtraction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Failed validation releases the active job reservation without resetting counters."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))
    monkeypatch.setenv("CSEP_CODEX_SKILLS_DIR", str(tmp_path / "skills"))
    payload = _payload(repo)
    decision = {
        "status": "queued",
        "review_memory": True,
        "review_skills": True,
        "trigger_reasons": ["memory_stop_interval", "skill_tool_interval"],
        "counters": {
            "stops_since_memory_review": 3,
            "readable_chars_since_memory_review": 9000,
            "tool_calls_since_skill_review": 5,
        },
    }
    job = create_job_from_payload(
        payload,
        home=home,
        trigger_decision=decision,
        skill_generation_mode="one_shot_active",
    )
    from codex_self_evolution.session_reflection.trigger import (
        load_trigger_state,
        trigger_paths_for_payload,
        write_trigger_state,
    )

    trigger_paths = trigger_paths_for_payload(payload, home=home)
    state = load_trigger_state(trigger_paths, session_id="parent-1")
    state["active_job_id"] = job["job_id"]
    state["stops_since_memory_review"] = 4
    state["readable_chars_since_memory_review"] = 12000
    state["tool_calls_since_skill_review"] = 7
    write_trigger_state(trigger_paths, state)

    run_reflection_job(
        str(job["job_id"]),
        home=home,
        client=FakeReflectionClient(receipt_child_thread_id="wrong-child"),
    )

    updated = load_trigger_state(trigger_paths, session_id="parent-1")
    assert updated["stops_since_memory_review"] == 4
    assert updated["readable_chars_since_memory_review"] == 12000
    assert updated["tool_calls_since_skill_review"] == 7
    assert updated["active_job_id"] is None


def test_run_reflection_job_exception_clears_active_job_without_counter_subtraction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pre-validation runner exceptions release active job reservation only."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))
    monkeypatch.setenv("CSEP_CODEX_SKILLS_DIR", str(tmp_path / "skills"))
    payload = _payload(repo)
    decision = {
        "status": "queued",
        "review_memory": True,
        "review_skills": True,
        "trigger_reasons": ["memory_stop_interval", "skill_tool_call_interval"],
        "counters": {
            "stops_since_memory_review": 3,
            "readable_chars_since_memory_review": 9000,
            "tool_calls_since_skill_review": 5,
        },
    }
    job = create_job_from_payload(payload, home=home, trigger_decision=decision)
    from codex_self_evolution.session_reflection.trigger import (
        load_trigger_state,
        trigger_paths_for_payload,
        write_trigger_state,
    )

    trigger_paths = trigger_paths_for_payload(payload, home=home)
    state = load_trigger_state(trigger_paths, session_id="parent-1")
    state["active_job_id"] = job["job_id"]
    state["stops_since_memory_review"] = 4
    state["readable_chars_since_memory_review"] = 12000
    state["tool_calls_since_skill_review"] = 7
    write_trigger_state(trigger_paths, state)

    run_reflection_job(str(job["job_id"]), home=home, client=FakeReflectionClient(fail_start=True))

    updated = load_trigger_state(trigger_paths, session_id="parent-1")
    assert updated["stops_since_memory_review"] == 4
    assert updated["readable_chars_since_memory_review"] == 12000
    assert updated["tool_calls_since_skill_review"] == 7
    assert updated["active_job_id"] is None


def test_run_reflection_job_partial_invalid_skill_does_not_reset_skill_counter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Invalid generated skills keep skill counters while successful memory resets."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))
    monkeypatch.setenv("CSEP_CODEX_SKILLS_DIR", str(tmp_path / "skills"))
    payload = _payload(repo)
    memory = _build_project_paths_for_home(repo, home=home).memory_dir / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("Remember the scoped rule.\n", encoding="utf-8")
    skill = tmp_path / "skills" / "csep-reflect-alpha" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Alpha\n\n## Skill Decision\n\nMissing required sections.\n", encoding="utf-8")
    decision = {
        "status": "queued",
        "review_memory": True,
        "review_skills": True,
        "trigger_reasons": ["memory_stop_interval", "skill_tool_call_interval"],
        "counters": {
            "stops_since_memory_review": 3,
            "readable_chars_since_memory_review": 9000,
            "tool_calls_since_skill_review": 5,
        },
    }
    job = create_job_from_payload(payload, home=home, trigger_decision=decision)
    from codex_self_evolution.session_reflection.trigger import (
        load_trigger_state,
        trigger_paths_for_payload,
        write_trigger_state,
    )

    trigger_paths = trigger_paths_for_payload(payload, home=home)
    state = load_trigger_state(trigger_paths, session_id="parent-1")
    state["active_job_id"] = job["job_id"]
    state["stops_since_memory_review"] = 4
    state["readable_chars_since_memory_review"] = 12000
    state["tool_calls_since_skill_review"] = 7
    write_trigger_state(trigger_paths, state)

    run_reflection_job(
        str(job["job_id"]),
        home=home,
        client=FakeReflectionClient(
            memory_changes=[{"path": str(memory), "action": "add", "after_hash": _sha(memory)}],
            skill_changes=[{"skill_id": "csep-reflect-alpha", "path": str(skill), "action": "create", "after_hash": _sha(skill)}],
        ),
    )

    updated = load_trigger_state(trigger_paths, session_id="parent-1")
    assert updated["stops_since_memory_review"] == 1
    assert updated["readable_chars_since_memory_review"] == 3000
    assert updated["tool_calls_since_skill_review"] == 7
    assert updated["active_job_id"] is None


def test_run_reflection_job_active_lock_fails_without_app_server_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A live global lock prevents another worker from starting."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))
    monkeypatch.setenv("CSEP_CODEX_SKILLS_DIR", str(tmp_path / "skills"))
    job = create_job_from_payload(_payload(repo), home=home)
    atomic_write_json(
        global_lock_path(home=home),
        {
            "created_at": utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "pid": os.getpid(),
            "owner_token": "existing-owner",
        },
    )
    client = FakeReflectionClient()

    updated = run_reflection_job(str(job["job_id"]), home=home, client=client)

    assert updated["status"] == "failed"
    assert "global reflection lock is active" in updated["error"]
    assert client.fork_calls == []
    assert json.loads(global_lock_path(home=home).read_text(encoding="utf-8"))["owner_token"] == "existing-owner"


def test_run_reflection_job_does_not_release_lock_after_owner_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Runner lock cleanup does not remove a lock owned by another worker."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))
    monkeypatch.setenv("CSEP_CODEX_SKILLS_DIR", str(tmp_path / "skills"))
    job = create_job_from_payload(_payload(repo), home=home)

    updated = run_reflection_job(str(job["job_id"]), home=home, client=FakeReflectionClient(steal_lock_home=home))

    assert updated["status"] == "skipped_empty"
    assert json.loads(global_lock_path(home=home).read_text(encoding="utf-8"))["owner_token"] == "other-owner"


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


def test_session_reflection_status_reports_failure_details(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Status includes failure reason and artifact paths for latest job."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))
    job = create_job_from_payload(_payload(repo), home=home)
    updated = update_job_status(
        str(job["job_id"]),
        "failed",
        home=home,
        error="app-server unavailable",
        receipt_path=str(home / "session_reflection" / "runs" / str(job["job_id"]) / "receipt.json"),
        validation={"status": "failed", "reason": "missing_receipt", "error": "receipt missing"},
    )

    status = session_reflection_status(home=home)

    assert status["latest"]["job_id"] == updated["job_id"]
    assert status["latest"]["status"] == "failed"
    assert status["latest"]["error"] == "app-server unavailable"
    assert status["latest"]["receipt_path"].endswith("/receipt.json")
    assert status["latest"]["validation_path"].endswith("/validation.json")
    assert status["latest"]["validation"] == {
        "status": "failed",
        "reason": "missing_receipt",
        "error": "receipt missing",
    }

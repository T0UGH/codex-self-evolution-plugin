from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from codex_self_evolution.config_file import SessionReflectionTriggerConfig
from codex_self_evolution.session_reflection.trigger import (
    TriggerLockBusy,
    append_decision,
    evaluate_trigger_policy,
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


def _append_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    """Append JSONL transcript rows using the Codex transcript shape."""
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


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
    assert updated["active_job_reserved_at"] is None


def test_evaluate_trigger_archive_only_updates_counters_and_offset(tmp_path: Path) -> None:
    """Archive-only decisions still persist incremental counters and offset."""
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    transcript = Path(str(payload["transcript_path"]))
    _append_jsonl(
        transcript,
        [
            {"type": "response_item", "payload": {"role": "user", "content": "hello"}},
            {"type": "response_item", "payload": {"role": "assistant", "content": "done"}},
        ],
    )
    cfg = SessionReflectionTriggerConfig(
        memory_stop_interval=3,
        memory_context_chars=16000,
        skill_tool_call_interval=15,
    )

    result = evaluate_trigger_policy(payload, cfg, home=tmp_path)

    assert result["status"] == "archive_only"
    assert result["decision"]["skip_reason"] == "below_threshold"
    assert result["state"]["stops_since_memory_review"] == 1
    assert result["state"]["readable_chars_since_memory_review"] >= len("hellodone")
    assert result["state"]["last_counted_byte_offset"] == transcript.stat().st_size


def test_evaluate_trigger_memory_stop_interval_queues_reflection(tmp_path: Path) -> None:
    """Memory-only trigger reasons still queue a full reflection pass."""
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    paths = trigger_paths_for_payload(payload, home=tmp_path)
    state = load_trigger_state(paths, session_id="parent-1")
    state["stops_since_memory_review"] = 2
    write_trigger_state(paths, state)

    result = evaluate_trigger_policy(payload, SessionReflectionTriggerConfig(), home=tmp_path)

    assert result["status"] == "queued"
    assert result["decision"]["review_memory"] is True
    assert result["decision"]["review_skills"] is True
    assert result["decision"]["trigger_reasons"] == ["memory_stop_interval"]


def test_evaluate_trigger_tool_calls_queue_skill(tmp_path: Path) -> None:
    """Skill review queues when legacy tool_calls rows hit the threshold."""
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    transcript = Path(str(payload["transcript_path"]))
    _append_jsonl(
        transcript,
        [
            {
                "type": "response_item",
                "payload": {
                    "role": "assistant",
                    "tool_calls": [{"name": "exec_command", "arguments": {"cmd": "date"}}],
                },
            }
        ],
    )
    paths = trigger_paths_for_payload(payload, home=tmp_path)
    state = load_trigger_state(paths, session_id="parent-1")
    state["tool_calls_since_skill_review"] = 14
    write_trigger_state(paths, state)

    result = evaluate_trigger_policy(payload, SessionReflectionTriggerConfig(), home=tmp_path)

    assert result["status"] == "queued"
    assert result["decision"]["review_memory"] is True
    assert result["decision"]["review_skills"] is True
    assert "skill_tool_call_interval" in result["decision"]["trigger_reasons"]


def test_evaluate_trigger_function_call_rows_queue_skill(tmp_path: Path) -> None:
    """Real Codex function_call transcript rows count as tool calls."""
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    transcript = Path(str(payload["transcript_path"]))
    _append_jsonl(
        transcript,
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "functions.exec_command",
                    "arguments": '{"cmd":"date"}',
                },
            }
        ],
    )
    paths = trigger_paths_for_payload(payload, home=tmp_path)
    state = load_trigger_state(paths, session_id="parent-1")
    state["tool_calls_since_skill_review"] = 14
    write_trigger_state(paths, state)

    result = evaluate_trigger_policy(payload, SessionReflectionTriggerConfig(), home=tmp_path)

    assert result["status"] == "queued"
    assert result["state"]["tool_calls_since_skill_review"] == 15
    assert result["decision"]["review_memory"] is True
    assert result["decision"]["review_skills"] is True
    assert result["decision"]["trigger_reasons"] == ["skill_tool_call_interval"]


def test_evaluate_trigger_keyword_scans_only_user_message(tmp_path: Path) -> None:
    """Assistant and tool text cannot trigger high-signal keywords."""
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    transcript = Path(str(payload["transcript_path"]))
    _append_jsonl(
        transcript,
        [
            {"type": "response_item", "payload": {"role": "assistant", "content": "memory skill"}},
            {"type": "response_item", "payload": {"role": "tool", "content": "skill"}},
            {"type": "response_item", "payload": {"role": "user", "content": "下次记住这个规则"}},
        ],
    )

    result = evaluate_trigger_policy(payload, SessionReflectionTriggerConfig(), home=tmp_path)

    assert result["status"] == "queued"
    assert result["decision"]["review_memory"] is True
    assert "high_signal_memory_keyword" in result["decision"]["trigger_reasons"]
    assert "记住" in result["decision"]["matched_keywords"]
    assert "matched_context_snippet" in result["decision"]


def test_evaluate_trigger_skips_tool_output_readable_chars(tmp_path: Path) -> None:
    """Function call outputs and tool role rows do not count full output text."""
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    transcript = Path(str(payload["transcript_path"]))
    long_output = "test log line\n" * 2000
    _append_jsonl(
        transcript,
        [
            {"type": "response_item", "payload": {"role": "user", "content": "hello"}},
            {
                "type": "response_item",
                "payload": {"type": "function_call_output", "output": long_output},
            },
            {"type": "response_item", "payload": {"role": "tool", "content": long_output}},
        ],
    )

    result = evaluate_trigger_policy(payload, SessionReflectionTriggerConfig(), home=tmp_path)

    assert result["status"] == "archive_only"
    assert result["state"]["readable_chars_since_memory_review"] == len("hello")


def test_evaluate_trigger_skips_bootstrap_user_keyword_content(tmp_path: Path) -> None:
    """Bootstrap-style user context cannot trigger high-signal keyword scans."""
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    transcript = Path(str(payload["transcript_path"]))
    _append_jsonl(
        transcript,
        [
            {
                "type": "response_item",
                "payload": {
                    "role": "user",
                    "content": "# AGENTS.md instructions\n<INSTRUCTIONS>\n以后不要触发 memory\n</INSTRUCTIONS>",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "role": "user",
                    "content": "<environment_context>\n请沉淀 skill\n</environment_context>",
                },
            },
        ],
    )

    result = evaluate_trigger_policy(payload, SessionReflectionTriggerConfig(), home=tmp_path)

    assert result["status"] == "archive_only"
    assert result["decision"]["matched_keywords"] == []
    assert "high_signal_memory_keyword" not in result["decision"]["trigger_reasons"]
    assert "high_signal_skill_keyword" not in result["decision"]["trigger_reasons"]


def test_evaluate_trigger_long_normal_user_keyword_triggers(tmp_path: Path) -> None:
    """Long normal user turns remain eligible for high-signal keyword scans."""
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    transcript = Path(str(payload["transcript_path"]))
    _append_jsonl(
        transcript,
        [
            {
                "type": "response_item",
                "payload": {"role": "user", "content": ("普通正文" * 2000) + "\n请记住这个偏好"},
            },
        ],
    )

    result = evaluate_trigger_policy(payload, SessionReflectionTriggerConfig(), home=tmp_path)

    assert result["status"] == "queued"
    assert "high_signal_memory_keyword" in result["decision"]["trigger_reasons"]
    assert "记住" in result["decision"]["matched_keywords"]


def test_evaluate_trigger_quoted_agents_marker_still_triggers(tmp_path: Path) -> None:
    """Quoting bootstrap marker text inside a normal request does not suppress it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    transcript = Path(str(payload["transcript_path"]))
    _append_jsonl(
        transcript,
        [
            {
                "type": "response_item",
                "payload": {
                    "role": "user",
                    "content": "我引用一下 '# AGENTS.md instructions' 这个标题；请记住这个偏好",
                },
            },
        ],
    )

    result = evaluate_trigger_policy(payload, SessionReflectionTriggerConfig(), home=tmp_path)

    assert result["status"] == "queued"
    assert "high_signal_memory_keyword" in result["decision"]["trigger_reasons"]
    assert "偏好" in result["decision"]["matched_keywords"]


def test_evaluate_trigger_does_not_advance_past_trailing_malformed_json(tmp_path: Path) -> None:
    """A partial trailing JSONL row remains available for the next delta scan."""
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    transcript = Path(str(payload["transcript_path"]))
    valid_row = json.dumps(
        {"type": "response_item", "payload": {"role": "user", "content": "hello"}},
        ensure_ascii=False,
    )
    transcript.write_text(valid_row + "\n" + '{"type":"response_item"', encoding="utf-8")

    result = evaluate_trigger_policy(payload, SessionReflectionTriggerConfig(), home=tmp_path)

    assert result["status"] == "archive_only"
    assert result["state"]["last_counted_byte_offset"] == len((valid_row + "\n").encode("utf-8"))
    assert result["state"]["last_counted_byte_offset"] < transcript.stat().st_size


def test_english_keyword_uses_word_boundary(tmp_path: Path) -> None:
    """English keyword matching requires whole words and is case-insensitive."""
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    transcript = Path(str(payload["transcript_path"]))
    _append_jsonl(
        transcript,
        [
            {"type": "response_item", "payload": {"role": "user", "content": "this is unskilled text"}},
        ],
    )

    result = evaluate_trigger_policy(payload, SessionReflectionTriggerConfig(), home=tmp_path)

    assert result["status"] == "archive_only"
    assert result["decision"]["matched_keywords"] == []


def test_evaluate_trigger_active_job_defers_queue(tmp_path: Path) -> None:
    """Active parent jobs defer new queueable trigger decisions."""
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    transcript = Path(str(payload["transcript_path"]))
    _append_jsonl(
        transcript,
        [
            {"type": "response_item", "payload": {"role": "user", "content": "请沉淀成 skill"}},
        ],
    )

    result = evaluate_trigger_policy(
        payload,
        SessionReflectionTriggerConfig(),
        home=tmp_path,
        active_job={"job_id": "job-1"},
    )

    assert result["status"] == "deferred_active_job"
    assert result["decision"]["skip_reason"] == "active_job_running"
    assert result["decision"]["review_memory"] is True
    assert result["decision"]["review_skills"] is True
    assert result["state"]["active_job_id"] is None


def test_evaluate_trigger_pending_reservation_defers_queue(tmp_path: Path) -> None:
    """A pending reservation prevents duplicate queued jobs before job creation is visible."""
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    paths = trigger_paths_for_payload(payload, home=tmp_path)
    state = load_trigger_state(paths, session_id="parent-1")
    state["active_job_id"] = "pending"
    state["stops_since_memory_review"] = 2
    write_trigger_state(paths, state)

    result = evaluate_trigger_policy(payload, SessionReflectionTriggerConfig(), home=tmp_path)

    assert result["status"] == "deferred_active_job"
    assert result["decision"]["skip_reason"] == "active_job_running"
    assert result["decision"]["review_memory"] is True
    assert result["state"]["active_job_id"] == "pending"
    assert result["state"]["active_job_reserved_at"] is not None


def test_evaluate_trigger_stale_pending_reservation_allows_queue(tmp_path: Path) -> None:
    """A stale pending reservation does not block future queueable decisions forever."""
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    paths = trigger_paths_for_payload(payload, home=tmp_path)
    state = load_trigger_state(paths, session_id="parent-1")
    state["active_job_id"] = "pending"
    state["active_job_reserved_at"] = "2000-01-01T00:00:00Z"
    state["stops_since_memory_review"] = 2
    paths.session_dir.mkdir(parents=True, exist_ok=True)
    paths.state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")

    result = evaluate_trigger_policy(
        payload,
        SessionReflectionTriggerConfig(active_job_stale_seconds=60),
        home=tmp_path,
    )

    assert result["status"] == "queued"
    assert result["decision"]["review_memory"] is True
    assert result["state"]["active_job_id"] == "pending"
    assert result["state"]["active_job_reserved_at"] is not None


def test_evaluate_trigger_deferred_pending_does_not_refresh_reservation_age(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated deferred decisions do not extend pending reservation TTL."""
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    paths = trigger_paths_for_payload(payload, home=tmp_path)
    monkeypatch.setattr(
        "codex_self_evolution.session_reflection.trigger.utc_now",
        lambda: datetime(2026, 5, 15, 0, 5, tzinfo=timezone.utc),
    )
    state = load_trigger_state(paths, session_id="parent-1")
    state["active_job_id"] = "pending"
    state["active_job_reserved_at"] = "2026-05-15T00:00:00Z"
    state["stops_since_memory_review"] = 2
    write_trigger_state(paths, state)

    first = evaluate_trigger_policy(payload, SessionReflectionTriggerConfig(), home=tmp_path)
    before = first["state"]["active_job_reserved_at"]

    second = evaluate_trigger_policy(payload, SessionReflectionTriggerConfig(), home=tmp_path)

    assert first["status"] == "deferred_active_job"
    assert second["status"] == "deferred_active_job"
    assert second["state"]["active_job_reserved_at"] == before

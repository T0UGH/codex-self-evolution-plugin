"""Codex native Stop hook payload mapping + --from-stdin CLI handoff."""

import json
import sys
from io import StringIO

import pytest

from codex_self_evolution import cli
from codex_self_evolution.hooks.codex_bridge import (
    DEFAULT_PROVIDER_ENV,
    TRANSCRIPT_MAX_CHARS,
    map_codex_stop_payload,
)


def _codex_payload(**overrides):
    payload = {
        "session_id": "019da-session",
        "turn_id": "019da-turn",
        "transcript_path": "",
        "cwd": "/tmp/target-repo",
        "hook_event_name": "Stop",
        "model": "gpt-5.4",
        "permission_mode": "bypassPermissions",
        "stop_hook_active": False,
        "last_assistant_message": "done",
    }
    payload.update(overrides)
    return payload


def test_map_codex_stop_payload_uses_last_assistant_when_no_transcript():
    result = map_codex_stop_payload(_codex_payload())
    assert result["thread_id"] == "019da-session"
    assert result["turn_id"] == "019da-turn"
    assert result["cwd"] == "/tmp/target-repo"
    assert result["transcript"] == "done"
    assert "reviewer_provider" not in result
    assert result["codex_hook_event"] == "Stop"
    assert result["codex_model"] == "gpt-5.4"


def test_map_codex_stop_payload_falls_back_to_defaults_when_fields_missing():
    result = map_codex_stop_payload({})
    assert result["thread_id"] == "unknown-thread"
    assert result["turn_id"] == ""
    assert result["cwd"] == "."
    assert result["transcript"] == ""
    assert "reviewer_provider" not in result


def test_map_codex_stop_payload_honors_env_provider_override(monkeypatch):
    monkeypatch.setenv(DEFAULT_PROVIDER_ENV, "openai-compatible")
    result = map_codex_stop_payload(_codex_payload())
    assert result["reviewer_provider"] == "openai-compatible"


def test_map_codex_stop_payload_explicit_provider_beats_env(monkeypatch):
    monkeypatch.setenv(DEFAULT_PROVIDER_ENV, "openai-compatible")
    result = map_codex_stop_payload(_codex_payload(), reviewer_provider="anthropic-style")
    assert result["reviewer_provider"] == "anthropic-style"


def test_map_codex_stop_payload_reads_jsonl_transcript(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(
        "\n".join(
            [
                json.dumps({"role": "user", "content": "please summarize"}),
                json.dumps({"type": "reasoning", "text": "internal monologue hidden"}),
                json.dumps({"role": "assistant", "content": "here's the summary"}),
                json.dumps({"type": "agent_message", "text": "extra tail"}),
            ]
        ),
        encoding="utf-8",
    )
    result = map_codex_stop_payload(_codex_payload(transcript_path=str(rollout)))
    assert "user: please summarize" in result["transcript"]
    assert "assistant: here's the summary" in result["transcript"]
    assert "assistant: extra tail" in result["transcript"]
    # Reasoning / tool noise is filtered out.
    assert "internal monologue hidden" not in result["transcript"]


def test_map_codex_stop_payload_truncates_large_transcript(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    big_content = "x" * (TRANSCRIPT_MAX_CHARS * 2)
    rollout.write_text(
        json.dumps({"role": "assistant", "content": big_content}),
        encoding="utf-8",
    )
    result = map_codex_stop_payload(_codex_payload(transcript_path=str(rollout)))
    assert len(result["transcript"]) <= TRANSCRIPT_MAX_CHARS
    assert result["transcript"].startswith("...")


def test_map_codex_stop_payload_falls_back_when_transcript_unreadable(tmp_path):
    missing = tmp_path / "does-not-exist.jsonl"
    result = map_codex_stop_payload(
        _codex_payload(transcript_path=str(missing), last_assistant_message="fallback text")
    )
    assert result["transcript"] == "fallback text"


def test_map_codex_stop_payload_skip_transcript_read(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(
        json.dumps({"role": "assistant", "content": "should not appear"}),
        encoding="utf-8",
    )
    result = map_codex_stop_payload(
        _codex_payload(transcript_path=str(rollout), last_assistant_message="last msg"),
        read_transcript=False,
    )
    assert result["transcript"] == "last msg"


def test_map_codex_stop_payload_preserves_thread_source_for_debug():
    result = map_codex_stop_payload(_codex_payload(threadSource="memory_consolidation"))
    assert result["codex_thread_source"] == "memory_consolidation"

    result = map_codex_stop_payload(_codex_payload(thread_source="agent"))
    assert result["codex_thread_source"] == "agent"

    result = map_codex_stop_payload(_codex_payload(source="stop_hook"))
    assert result["codex_thread_source"] == "stop_hook"


def test_map_codex_stop_payload_handles_content_list_parts():
    codex_payload = {
        "session_id": "s1",
        "cwd": "/tmp",
        "last_assistant_message": "",
    }
    # Inline feeding via transcript file with list-of-parts content.
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
        handle.write(
            json.dumps(
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "part one"},
                        {"type": "text", "text": "part two"},
                    ],
                }
            )
            + "\n"
        )
        path = handle.name
    codex_payload["transcript_path"] = path

    result = map_codex_stop_payload(codex_payload)
    assert "part one" in result["transcript"]
    assert "part two" in result["transcript"]


def test_cli_from_stdin_spawns_reflection_worker_and_returns_continue(monkeypatch, capsys, tmp_path):
    captured = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs

    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(
        cli,
        "enqueue_reflection_from_payload",
        lambda payload, *, home=None: {
            "status": "queued",
            "job_id": "job-1",
            "payload": payload,
            "home": home,
        },
    )

    codex_payload = _codex_payload(cwd=str(tmp_path), last_assistant_message="hi there")
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(codex_payload)))

    exit_code = cli.main(["stop-review", "--from-stdin"])
    assert exit_code == 0

    out = capsys.readouterr().out.strip()
    assert json.loads(out) == {"continue": True}

    argv = captured["argv"]
    assert argv[0] == sys.executable
    assert argv[1:4] == ["-m", "codex_self_evolution.cli", "session-reflect"]
    assert argv[4:6] == ["--job", "job-1"]
    # Subprocess must be detached so it outlives the hook caller.
    assert captured["kwargs"].get("start_new_session") is True


def test_cli_from_stdin_forwards_state_dir_to_child(monkeypatch, capsys):
    captured_argv = []
    monkeypatch.setattr(
        cli.subprocess,
        "Popen",
        lambda argv, **_: captured_argv.extend(argv) or None,
    )
    monkeypatch.setattr(
        cli,
        "enqueue_reflection_from_payload",
        lambda payload, *, home=None: {"status": "queued", "job_id": "job-1"},
    )
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(_codex_payload())))

    cli.main(["stop-review", "--from-stdin", "--state-dir", "/explicit/state"])
    capsys.readouterr()  # drain stdout so pytest doesn't complain

    assert "--home" in captured_argv
    assert captured_argv[captured_argv.index("--home") + 1] == "/explicit/state"


def test_cli_from_stdin_tolerates_malformed_json(monkeypatch, capsys):
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not spawn")))
    monkeypatch.setattr(sys, "stdin", StringIO("not json"))

    exit_code = cli.main(["stop-review", "--from-stdin"])
    assert exit_code == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["continue"] is True
    assert "warning" in out


def test_cli_stop_review_requires_payload_when_not_from_stdin(capsys):
    with pytest.raises(SystemExit):
        cli.main(["stop-review"])

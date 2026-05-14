from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from codex_self_evolution import cli


def _codex_payload(**overrides: object) -> dict[str, object]:
    """Build a raw Codex Stop payload fixture."""
    payload: dict[str, object] = {
        "session_id": "parent-1",
        "turn_id": "turn-1",
        "transcript_path": "/tmp/rollout.jsonl",
        "cwd": "/tmp/repo",
        "hook_event_name": "Stop",
        "model": "gpt-5.4",
    }
    payload.update(overrides)
    return payload


def test_stop_review_from_stdin_spawns_session_reflect_job(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Queued Stop payloads spawn a detached session-reflect worker."""
    captured: dict[str, Any] = {}

    def fake_enqueue(payload: dict[str, Any], *, home: str | Path | None = None) -> dict[str, Any]:
        captured["payload"] = payload
        captured["home"] = home
        return {"status": "queued", "job_id": "job-123"}

    class FakePopen:
        """Capture subprocess construction without starting a real worker."""

        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            """Record argv and detach options passed by the hook."""
            captured["argv"] = argv
            captured["kwargs"] = kwargs

    monkeypatch.setattr(cli, "enqueue_reflection_from_payload", fake_enqueue)
    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(_codex_payload())))

    exit_code = cli.main(["stop-review", "--from-stdin", "--state-dir", "/tmp/csep-home"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {"continue": True}
    assert captured["payload"]["session_id"] == "parent-1"
    assert captured["home"] == "/tmp/csep-home"
    assert captured["argv"][:6] == [
        sys.executable,
        "-m",
        "codex_self_evolution.cli",
        "session-reflect",
        "--job",
        "job-123",
    ]
    assert captured["argv"][-2:] == ["--home", "/tmp/csep-home"]
    assert captured["kwargs"]["start_new_session"] is True
    assert captured["kwargs"]["close_fds"] is True
    assert "session-reflect-" in captured["kwargs"]["stdout"].name
    assert captured["kwargs"]["stdout"].name.startswith("/tmp/codex-self-evolution/")


def test_stop_review_from_stdin_skipped_enqueue_does_not_spawn(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Skipped reflection enqueue still lets Codex continue."""
    monkeypatch.setattr(
        cli,
        "enqueue_reflection_from_payload",
        lambda payload, *, home=None: {"status": "skipped", "reason": "disabled"},
    )
    monkeypatch.setattr(
        cli.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not spawn")),
    )
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(_codex_payload())))

    exit_code = cli.main(["stop-review", "--from-stdin"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {"continue": True}


def test_session_reflect_job_dispatches_worker_with_home(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """session-reflect --job forwards the job id and explicit home to the runner."""
    calls: dict[str, Any] = {}

    def fake_run(job_id: str, *, home: str | Path | None = None) -> dict[str, Any]:
        calls["job_id"] = job_id
        calls["home"] = home
        return {"status": "succeeded", "job_id": job_id}

    monkeypatch.setattr(cli, "run_reflection_job", fake_run)

    exit_code = cli.main(["session-reflect", "--job", "job-123", "--home", "/tmp/csep-home"])

    assert exit_code == 0
    assert calls == {"job_id": "job-123", "home": "/tmp/csep-home"}
    assert json.loads(capsys.readouterr().out)["status"] == "succeeded"


def test_session_reflect_status_dispatches_status(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """session-reflect --status forwards the explicit home to status."""
    calls: dict[str, Any] = {}

    def fake_status(*, home: str | Path | None = None) -> dict[str, Any]:
        calls["home"] = home
        return {"root": "/tmp/csep-home/session_reflection", "exists": True}

    monkeypatch.setattr(cli, "session_reflection_status", fake_status)

    exit_code = cli.main(["session-reflect", "--status", "--home", "/tmp/csep-home"])

    assert exit_code == 0
    assert calls == {"home": "/tmp/csep-home"}
    assert json.loads(capsys.readouterr().out)["exists"] is True


def test_session_reflect_hook_payload_enqueues_and_runs_foreground(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Manual hook-payload mode runs queued jobs in the foreground."""
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(_codex_payload(session_id="parent-2")), encoding="utf-8")
    calls: dict[str, Any] = {}

    def fake_enqueue(payload: dict[str, Any], *, home: str | Path | None = None) -> dict[str, Any]:
        calls["payload"] = payload
        calls["enqueue_home"] = home
        return {"status": "queued", "job_id": "job-456"}

    def fake_run(job_id: str, *, home: str | Path | None = None) -> dict[str, Any]:
        calls["job_id"] = job_id
        calls["run_home"] = home
        return {"status": "succeeded", "job_id": job_id}

    monkeypatch.setattr(cli, "enqueue_reflection_from_payload", fake_enqueue)
    monkeypatch.setattr(cli, "run_reflection_job", fake_run)

    exit_code = cli.main(["session-reflect", "--hook-payload", str(payload_path), "--home", "/tmp/home"])

    assert exit_code == 0
    assert calls == {
        "payload": _codex_payload(session_id="parent-2"),
        "enqueue_home": "/tmp/home",
        "job_id": "job-456",
        "run_home": "/tmp/home",
    }
    assert json.loads(capsys.readouterr().out) == {"job_id": "job-456", "status": "succeeded"}


def test_stop_review_from_stdin_malformed_json_is_non_blocking_without_enqueue_or_spawn(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Malformed Stop payloads keep the legacy non-blocking warning behavior."""
    monkeypatch.setattr(
        cli,
        "enqueue_reflection_from_payload",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not enqueue")),
    )
    monkeypatch.setattr(
        cli.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not spawn")),
    )
    monkeypatch.setattr(sys, "stdin", StringIO("not json"))

    exit_code = cli.main(["stop-review", "--from-stdin"])

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["continue"] is True
    assert "warning" in out

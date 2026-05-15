import json
import sys
from io import StringIO
from pathlib import Path
from typing import Any

from codex_self_evolution import cli, csep


def test_csep_recall_defaults_to_markdown_from_session_recall(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps({"role": "user", "content": "pytest workflow from sqlite recall"}) + "\n",
        encoding="utf-8",
    )

    assert csep.main([
        "session-archive",
        "--transcript-path",
        str(transcript),
        "--cwd",
        str(repo),
        "--session-id",
        "s1",
    ]) == 0
    capsys.readouterr()

    exit_code = csep.main(["recall", "pytest workflow", "--cwd", str(repo)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert out.startswith("## Focused Recall")
    assert "Status: matched" in out
    assert "pytest workflow from sqlite recall" in out


def test_csep_recall_does_not_fallback_to_legacy_recall_index(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    legacy_recall = state / "recall"
    legacy_recall.mkdir(parents=True)
    (legacy_recall / "index.json").write_text(
        json.dumps({
            "records": [
                {
                    "id": "legacy",
                    "summary": "legacy pytest workflow",
                    "content": "this must not be returned",
                    "source_paths": [],
                    "repo_fingerprint": "legacy",
                    "cwd": str(repo),
                }
            ]
        }),
        encoding="utf-8",
    )

    exit_code = csep.main([
        "recall",
        "legacy pytest workflow",
        "--cwd",
        str(repo),
        "--state-dir",
        str(state),
        "--format",
        "json",
    ])

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "no_match"
    assert out["count"] == 0
    assert out["results"] == []


def test_csep_dispatches_main_runtime_commands(tmp_path, monkeypatch, capsys):
    """The short csep entrypoint owns status, config, and reflection commands."""
    monkeypatch.setattr(
        cli,
        "collect_status",
        lambda *, home=None: {"status": "ok", "home": str(home)},
    )

    assert csep.main(["status", "--home", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "home": str(tmp_path),
        "status": "ok",
    }

    assert csep.main(["config", "path", "--home", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["config_path"] == str(tmp_path / "config.toml")

    monkeypatch.setattr(
        cli,
        "session_reflection_status",
        lambda *, home=None: {"exists": True, "home": str(home)},
    )
    assert csep.main(["session-reflect", "--status", "--home", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "exists": True,
        "home": str(tmp_path),
    }


def test_csep_session_stop_from_stdin_spawns_csep_worker(monkeypatch, capsys):
    """The short hook command keeps background reflection on the short module too."""
    captured: dict[str, Any] = {}

    def fake_enqueue(payload: dict[str, Any], *, home: str | Path | None = None) -> dict[str, Any]:
        captured["payload"] = payload
        captured["home"] = home
        return {"status": "queued", "job_id": "job-123"}

    class FakePopen:
        """Capture subprocess construction without launching a worker."""

        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            captured["argv"] = argv
            captured["kwargs"] = kwargs

    payload = {
        "session_id": "parent-1",
        "transcript_path": "/tmp/rollout.jsonl",
        "cwd": "/tmp/repo",
        "hook_event_name": "Stop",
    }
    monkeypatch.setattr(cli, "enqueue_reflection_from_payload", fake_enqueue)
    monkeypatch.setattr(cli, "_spawn_session_archive_from_stop_payload", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(payload)))

    assert csep.main(["session-stop", "--from-stdin", "--state-dir", "/tmp/csep-home"]) == 0

    assert json.loads(capsys.readouterr().out) == {"continue": True}
    assert captured["home"] == "/tmp/csep-home"
    assert captured["argv"][:4] == [
        sys.executable,
        "-m",
        "codex_self_evolution.csep",
        "session-reflect",
    ]

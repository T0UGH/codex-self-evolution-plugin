import json
import subprocess
import sys
import tomllib
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from codex_self_evolution import __version__
from codex_self_evolution import cli, csep


def test_runtime_version_matches_pyproject():
    pyproject = Path(__file__).parents[1] / "pyproject.toml"
    project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]

    assert __version__ == project["version"]


def test_csep_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        csep.main(["--version"])

    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("csep ")


def test_long_cli_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])

    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("codex-self-evolution ")


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


def test_csep_setup_installs_marketplace_and_enables_config(tmp_path, monkeypatch, capsys):
    """Setup is the one-command path for normal plugin installation."""
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(csep.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(csep.subprocess, "run", fake_run)
    config_path = tmp_path / "config.toml"

    exit_code = csep.main([
        "setup",
        "--package",
        "csep==1.0.0",
        "--marketplace-source",
        "T0UGH/codex-self-evolution-plugin",
        "--codex-config",
        str(config_path),
    ])

    assert exit_code == 0
    assert calls == [
        ["uv", "tool", "install", "--force", "--reinstall", "--refresh", "csep==1.0.0"],
        ["codex", "plugin", "marketplace", "add", "T0UGH/codex-self-evolution-plugin"],
    ]
    text = config_path.read_text(encoding="utf-8")
    assert "[features]" in text
    assert "plugins = true" in text
    assert "hooks = true" in text
    assert "plugin_hooks = true" in text
    assert '[plugins."codex-self-evolution@codex-self-evolution"]' in text
    assert "enabled = true" in text
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ok"


def test_setup_config_upsert_is_idempotent(tmp_path):
    """Config setup updates existing tables instead of appending duplicates."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[features]
plugins = false

[plugins."codex-self-evolution@codex-self-evolution"]
enabled = false
""".lstrip(),
        encoding="utf-8",
    )

    assert csep._enable_codex_plugin_config(config_path) is True
    first = config_path.read_text(encoding="utf-8")
    assert csep._enable_codex_plugin_config(config_path) is False
    assert config_path.read_text(encoding="utf-8") == first
    assert first.count("[features]") == 1
    assert first.count('[plugins."codex-self-evolution@codex-self-evolution"]') == 1
    assert "plugins = true" in first
    assert "hooks = true" in first
    assert "plugin_hooks = true" in first
    assert "enabled = true" in first

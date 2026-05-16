import json

from codex_self_evolution import csep


def test_csep_session_archive_and_recall(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps({"role": "user", "content": "hermes session recall smoke"}) + "\n",
        encoding="utf-8",
    )

    archive_code = csep.main(
        [
            "session-archive",
            "--transcript-path",
            str(transcript),
            "--cwd",
            str(repo),
            "--session-id",
            "s1",
        ]
    )
    assert archive_code == 0
    archive_out = json.loads(capsys.readouterr().out)
    assert archive_out["status"] == "archived"

    recall_code = csep.main(["recall", "hermes session recall", "--cwd", str(repo)])
    assert recall_code == 0
    out = capsys.readouterr().out
    assert "## Focused Recall" in out
    assert "Status: matched" in out
    assert "hermes session recall smoke" in out


def test_csep_recall_recent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps({"role": "user", "content": "recent item"}) + "\n", encoding="utf-8")

    assert csep.main(["session-archive", "--transcript-path", str(transcript), "--cwd", str(repo), "--session-id", "s1"]) == 0
    capsys.readouterr()

    assert csep.main(["recall", "--recent", "--cwd", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "Status: matched" in out
    assert "recent item" in out


def test_csep_session_ingest_backfill(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))
    root = tmp_path / "sessions"
    root.mkdir()
    (root / "one.jsonl").write_text(json.dumps({"role": "user", "content": "backfilled"}) + "\n", encoding="utf-8")

    assert csep.main(["session-ingest", "--backfill", "--root", str(root), "--cwd", str(tmp_path), "--limit-files", "1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["processed_files"] == 1


def test_csep_recall_bootstrap_backfills_history(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "sessions"
    root.mkdir()
    (root / "one.jsonl").write_text(
        json.dumps({"role": "user", "content": "formal bootstrap history"}) + "\n",
        encoding="utf-8",
    )

    exit_code = csep.main(
        [
            "recall",
            "bootstrap",
            "--root",
            str(root),
            "--cwd",
            str(repo),
            "--limit-files",
            "1",
            "--format",
            "json",
        ]
    )

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["command"] == "recall bootstrap"
    assert out["processed_files"] == 1
    assert out["processed_successfully"] == 1
    assert out["new_sessions"] == 1

    assert csep.main(["recall", "formal bootstrap", "--cwd", str(repo)]) == 0
    recall_out = capsys.readouterr().out
    assert "Status: matched" in recall_out
    assert "formal bootstrap history" in recall_out


def test_csep_recall_budget_truncates(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps({"role": "user", "content": "needle " + ("x" * 500)}) + "\n", encoding="utf-8")

    csep.main(["session-archive", "--transcript-path", str(transcript), "--cwd", str(repo), "--session-id", "s1"])
    capsys.readouterr()

    csep.main(["recall", "needle", "--cwd", str(repo), "--budget-chars", "220", "--message-chars", "80"])
    out = capsys.readouterr().out
    assert "[truncated:" in out

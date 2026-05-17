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


def test_csep_session_archive_skipped_reflection_child_is_success(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))
    payload = tmp_path / "payload.json"
    payload.write_text(
        json.dumps({
            "session_id": "child-1",
            "cwd": str(tmp_path),
            "thread_source": "memory_consolidation",
        }),
        encoding="utf-8",
    )

    code = csep.main(["session-archive", "--from-hook-payload", str(payload)])

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "skipped"
    assert out["reason"] == "thread_source_memory_consolidation"


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


def test_csep_recall_sync_claude_backfills_claude_history(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path / "claude" / "projects" / "-tmp-repo"
    root.mkdir(parents=True)
    (root / "claude-session.jsonl").write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": "claude-session",
                "uuid": "u1",
                "cwd": str(repo),
                "timestamp": "2026-05-17T01:00:00.000Z",
                "message": {"role": "user", "content": "claude sync needle"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = csep.main(
        [
            "recall",
            "sync-claude",
            "--root",
            str(tmp_path / "claude" / "projects"),
            "--limit-files",
            "1",
            "--format",
            "json",
        ]
    )

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["command"] == "recall sync-claude"
    assert out["source"] == "claude_code"
    assert out["processed_files"] == 1
    assert out["processed_successfully"] == 1
    assert out["new_sessions"] == 1

    assert csep.main(["recall", "claude sync needle", "--cwd", str(repo)]) == 0
    recall_out = capsys.readouterr().out
    assert "Status: matched" in recall_out
    assert "claude sync needle" in recall_out


def test_csep_recall_claude_query_remains_search(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps({"role": "user", "content": "claude query needle"}) + "\n", encoding="utf-8")

    assert csep.main(["session-archive", "--transcript-path", str(transcript), "--cwd", str(repo), "--session-id", "s1"]) == 0
    capsys.readouterr()

    assert csep.main(["recall", "claude", "--cwd", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "Status: matched" in out
    assert "claude query needle" in out
    assert "Claude Recall Sync" not in out


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


def test_csep_recall_pipe_or_fallback_and_all_terms(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps({"role": "user", "content": "alpha only"}) + "\n", encoding="utf-8")

    csep.main(["session-archive", "--transcript-path", str(transcript), "--cwd", str(repo), "--session-id", "s1"])
    capsys.readouterr()

    assert csep.main(["recall", "alpha|beta", "--cwd", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "Status: matched" in out
    assert "alpha only" in out

    assert csep.main(["recall", "alpha beta gamma", "--cwd", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "Status: matched" in out

    assert csep.main(["recall", "alpha beta gamma", "--cwd", str(repo), "--all-terms"]) == 0
    out = capsys.readouterr().out
    assert "Status: no_match" in out


def test_csep_recall_json_includes_evidence_windows(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"role": "user", "content": "alpha decision"}),
                json.dumps({"role": "assistant", "content": "middle filler"}),
                json.dumps({"role": "assistant", "content": "middle filler two"}),
                json.dumps({"role": "assistant", "content": "middle filler three"}),
                json.dumps({"role": "assistant", "content": "beta decision"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    csep.main(["session-archive", "--transcript-path", str(transcript), "--cwd", str(repo), "--session-id", "s1"])
    capsys.readouterr()

    assert csep.main([
        "recall",
        "alpha|beta",
        "--cwd",
        str(repo),
        "--windows-per-session",
        "2",
        "--before",
        "0",
        "--after",
        "0",
        "--format",
        "json",
    ]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["results"][0]["windows"]) == 2
    assert [window["anchor"]["message_index"] for window in out["results"][0]["windows"]] == [0, 4]


def test_csep_recall_no_match_stays_minimal(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps({"role": "user", "content": "known needle"}) + "\n", encoding="utf-8")

    csep.main(["session-archive", "--transcript-path", str(transcript), "--cwd", str(repo), "--session-id", "s1"])
    capsys.readouterr()

    assert csep.main(["recall", "missing-only", "--cwd", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "Status: no_match" in out
    assert "Continue with the current repo" not in out
    assert "Next:" not in out

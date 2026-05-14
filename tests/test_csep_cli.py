import json

from codex_self_evolution import csep


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

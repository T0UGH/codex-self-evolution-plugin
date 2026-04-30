import json

from codex_self_evolution import csep
from codex_self_evolution.compiler.engine import _write_recall
from codex_self_evolution.schemas import RecallRecord
from codex_self_evolution.storage import repo_fingerprint


def test_csep_recall_defaults_to_markdown(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    _write_recall(
        state / "recall",
        [
            RecallRecord(
                id="r1",
                summary="pytest workflow",
                content="Run focused pytest first",
                source_paths=["tests/test_x.py"],
                repo_fingerprint=repo_fingerprint(repo),
                cwd=str(repo),
            )
        ],
    )
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))

    exit_code = csep.main(["recall", "pytest workflow", "--cwd", str(repo), "--state-dir", str(state)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert out.startswith("## Focused Recall")
    assert "Status: matched" in out
    assert "Run focused pytest first" in out


def test_csep_recall_json_mode(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))

    exit_code = csep.main(["recall", "missing topic", "--cwd", str(repo), "--state-dir", str(state), "--format", "json"])

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "no_match"
    assert out["count"] == 0

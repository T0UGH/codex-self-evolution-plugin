import json

from codex_self_evolution.recall.search import search_recall
from codex_self_evolution.recall.workflow import build_focused_recall, evaluate_recall_trigger
from codex_self_evolution.storage import repo_fingerprint



def test_recall_search_ranks_same_repo_and_cwd_first(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    recall_dir = state / "recall"
    recall_dir.mkdir(parents=True)
    recall_dir.joinpath("index.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "id": "same-repo",
                        "summary": "pytest workflow",
                        "content": "Run focused pytest first",
                        "source_paths": ["tests/test_x.py"],
                        "repo_fingerprint": repo_fingerprint(repo),
                        "cwd": str(repo),
                    },
                    {
                        "id": "global",
                        "summary": "generic workflow",
                        "content": "Use rg for search",
                        "source_paths": ["README.md"],
                        "repo_fingerprint": "other",
                        "cwd": "/elsewhere",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    results = search_recall(query="pytest workflow", cwd=repo, state_dir=state)
    assert results[0]["id"] == "same-repo"



def test_recall_trigger_and_focused_recall_helpers(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    recall_dir = state / "recall"
    recall_dir.mkdir(parents=True)
    recall_dir.joinpath("index.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "id": "same-repo",
                        "summary": "pytest workflow",
                        "content": "Run focused pytest first",
                        "source_paths": ["tests/test_x.py"],
                        "repo_fingerprint": repo_fingerprint(repo),
                        "cwd": str(repo),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    trigger = evaluate_recall_trigger("remember previous pytest workflow")
    assert trigger["triggered"] is True
    focused = build_focused_recall("pytest workflow", cwd=repo, state_dir=state)
    assert "pytest workflow" in focused["focused_recall"]

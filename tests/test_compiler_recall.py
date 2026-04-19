from codex_self_evolution.compiler.recall import compile_recall
from codex_self_evolution.schemas import Suggestion


def test_compile_recall_dedupes_and_preserves_context():
    suggestions = [
        Suggestion(
            family="recall_candidate",
            summary="Remember pytest invocation",
            details={"content": "Run focused pytest first", "source_paths": ["tests/test_x.py"]},
        ),
        Suggestion(
            family="recall_candidate",
            summary="Remember pytest invocation",
            details={"content": "Run focused pytest first", "source_paths": ["tests/test_x.py"]},
        ),
    ]
    records = compile_recall(suggestions, repo_fingerprint="repo1", cwd="/tmp/repo")
    assert len(records) == 1
    assert records[0].repo_fingerprint == "repo1"

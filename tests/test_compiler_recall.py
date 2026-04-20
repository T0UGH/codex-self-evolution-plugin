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


def _existing_record(content: str, record_id: str = "old1") -> dict:
    return {
        "id": record_id,
        "summary": "old recall",
        "content": content,
        "source_paths": ["legacy.md"],
        "repo_fingerprint": "legacy-repo",
        "cwd": "/legacy",
        "thread_id": "t-old",
        "turn_id": "",
        "source_updated_at": "",
    }


def test_compile_recall_preserves_existing_records_when_batch_is_empty():
    existing = [_existing_record("legacy content")]
    records = compile_recall([], repo_fingerprint="repo", cwd="/tmp", existing_records=existing)
    assert len(records) == 1
    assert records[0].id == "old1"
    assert records[0].repo_fingerprint == "legacy-repo"


def test_compile_recall_dedupes_new_against_existing_content():
    existing = [_existing_record("stable recall")]
    suggestions = [
        Suggestion(
            family="recall_candidate",
            summary="dup",
            details={"content": "stable recall"},
        ),
        Suggestion(
            family="recall_candidate",
            summary="fresh",
            details={"content": "brand new recall"},
        ),
    ]
    records = compile_recall(
        suggestions,
        repo_fingerprint="repo2",
        cwd="/tmp/repo",
        existing_records=existing,
    )
    contents = [item.content for item in records]
    assert contents == ["stable recall", "brand new recall"]
    # The existing record keeps its stable id; new entries get a fresh hashed id.
    assert records[0].id == "old1"
    assert records[1].id != "old1"


def test_compile_recall_uses_details_note_when_content_missing():
    suggestion = Suggestion(
        family="recall_candidate",
        summary="summary text",
        details={"note": "real recall text from reviewer"},
    )
    records = compile_recall([suggestion], repo_fingerprint="r", cwd="/tmp")
    assert len(records) == 1
    assert records[0].content == "real recall text from reviewer"


def test_compile_recall_prefers_content_over_note():
    suggestion = Suggestion(
        family="recall_candidate",
        summary="summary text",
        details={"content": "explicit recall content", "note": "note body"},
    )
    records = compile_recall([suggestion], repo_fingerprint="r", cwd="/tmp")
    assert records[0].content == "explicit recall content"


def test_compile_recall_skips_malformed_existing_entries():
    existing = [
        "not a dict",
        {"id": "x", "summary": "incomplete"},  # missing required fields
        _existing_record("valid one"),
    ]
    records = compile_recall([], repo_fingerprint="r", cwd="/tmp", existing_records=existing)
    assert [item.content for item in records] == ["valid one"]

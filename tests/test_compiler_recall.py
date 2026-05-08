from codex_self_evolution.compiler.recall import compile_recall, compile_recall_with_discarded
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
            details={"content": "When this workflow returns, use brand new recall.", "source_paths": ["fresh.md"]},
        ),
    ]
    records = compile_recall(
        suggestions,
        repo_fingerprint="repo2",
        cwd="/tmp/repo",
        existing_records=existing,
    )
    contents = [item.content for item in records]
    assert contents == ["stable recall", "When this workflow returns, use brand new recall."]
    # The existing record keeps its stable id; new entries get a fresh hashed id.
    assert records[0].id == "old1"
    assert records[1].id != "old1"


def test_compile_recall_uses_details_note_when_content_missing():
    suggestion = Suggestion(
        family="recall_candidate",
        summary="summary text",
        details={"note": "When the reviewer renames content, keep the reusable diagnostic text.", "source_paths": ["review.md"]},
    )
    records = compile_recall([suggestion], repo_fingerprint="r", cwd="/tmp")
    assert len(records) == 1
    assert records[0].content == "When the reviewer renames content, keep the reusable diagnostic text."


def test_compile_recall_prefers_content_over_note():
    suggestion = Suggestion(
        family="recall_candidate",
        summary="summary text",
        details={
            "content": "When this repo returns, prefer explicit recall content.",
            "note": "note body",
            "source_paths": ["review.md"],
        },
    )
    records = compile_recall([suggestion], repo_fingerprint="r", cwd="/tmp")
    assert records[0].content == "When this repo returns, prefer explicit recall content."


def test_compile_recall_skips_malformed_existing_entries():
    existing = [
        "not a dict",
        {"id": "x", "summary": "incomplete"},  # missing required fields
        _existing_record("valid one"),
    ]
    records = compile_recall([], repo_fingerprint="r", cwd="/tmp", existing_records=existing)
    assert [item.content for item in records] == ["valid one"]


def test_compile_recall_skips_process_state_without_future_trigger():
    suggestions = [
        Suggestion(
            family="recall_candidate",
            summary="MR process state",
            details={
                "content": "MR 493 comments were resolved and the branch was pushed yesterday.",
                "source_paths": ["docs/review.md"],
            },
        )
    ]

    records = compile_recall(suggestions, repo_fingerprint="repo", cwd="/tmp/repo")

    assert records == []


def test_compile_recall_reports_discarded_process_state():
    suggestion = Suggestion(
        family="recall_candidate",
        summary="MR process state",
        details={
            "content": "MR 493 comments were resolved and the branch was pushed yesterday.",
            "source_paths": ["docs/review.md"],
        },
    )

    records, discarded = compile_recall_with_discarded(
        [suggestion],
        repo_fingerprint="repo",
        cwd="/tmp/repo",
    )

    assert records == []
    assert discarded == [
        {
            "family": "recall_candidate",
            "summary": "MR process state",
            "reason": "missing_reuse_trigger",
        }
    ]


def test_compile_recall_keeps_future_trigger_with_evidence():
    suggestions = [
        Suggestion(
            family="recall_candidate",
            summary="Treasure handler/domain layering",
            details={
                "content": "When touching Treasure task assembly again, keep handler orchestration separate from domain filtering.",
                "source_paths": ["handler/treasure.go", "domain/task_filter.go"],
            },
        )
    ]

    records = compile_recall(suggestions, repo_fingerprint="repo", cwd="/tmp/repo")

    assert len(records) == 1
    assert records[0].summary == "Treasure handler/domain layering"

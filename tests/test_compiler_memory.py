from codex_self_evolution.compiler.memory import compile_memory
from codex_self_evolution.schemas import Suggestion


def test_compile_memory_merges_duplicate_content():
    suggestions = [
        Suggestion(family="memory_updates", summary="a", details={"content": "same"}, confidence=0.5),
        Suggestion(family="memory_updates", summary="b", details={"content": "same"}, confidence=0.9),
    ]
    records = compile_memory(suggestions)
    assert len(records["global"]) == 1
    assert records["global"][0]["confidence"] == 0.9
    assert records["user"] == []


def test_compile_memory_routes_user_and_global_scopes_separately():
    suggestions = [
        Suggestion(family="memory_updates", summary="user pref", details={"content": "Prefer concise summaries.", "scope": "user"}),
        Suggestion(family="memory_updates", summary="repo fact", details={"content": "Run focused pytest before full suite.", "scope": "global"}),
        Suggestion(family="memory_updates", summary="default scope", details={"content": "Unscoped memory stays permissive."}),
    ]
    records = compile_memory(suggestions)
    assert [item["content"] for item in records["user"]] == ["Prefer concise summaries."]
    assert [item["content"] for item in records["global"]] == [
        "Run focused pytest before full suite.",
        "Unscoped memory stays permissive.",
    ]


def test_compile_memory_preserves_existing_entries_when_batch_is_empty():
    existing = {
        "user": [
            {"summary": "old user", "content": "keep me user", "confidence": 0.7, "source_paths": ["a.md"]},
        ],
        "global": [
            {"summary": "old global", "content": "keep me global", "confidence": 0.9},
        ],
    }
    records = compile_memory([], existing_index=existing)
    assert [item["content"] for item in records["user"]] == ["keep me user"]
    assert records["user"][0]["source_paths"] == ["a.md"]
    assert records["user"][0]["confidence"] == 0.7
    assert [item["content"] for item in records["global"]] == ["keep me global"]


def test_compile_memory_dedupes_new_suggestion_against_existing_entry():
    existing = {
        "user": [],
        "global": [
            {"summary": "stable fact", "content": "already there", "confidence": 0.9},
        ],
    }
    suggestions = [
        Suggestion(
            family="memory_updates",
            summary="noise",
            details={"content": "already there"},
            confidence=0.4,
        ),
        Suggestion(
            family="memory_updates",
            summary="fresh",
            details={"content": "genuinely new fact"},
        ),
    ]
    records = compile_memory(suggestions, existing_index=existing)

    contents = [item["content"] for item in records["global"]]
    assert contents == ["already there", "genuinely new fact"]
    # existing entry should keep its original confidence; the new collision must not downgrade it.
    assert records["global"][0]["confidence"] == 0.9
    assert records["global"][0]["summary"] == "stable fact"


def test_compile_memory_uses_details_note_when_content_missing():
    suggestion = Suggestion(
        family="memory_updates",
        summary="summary text",
        details={"note": "actual note text, no content key"},
    )
    records = compile_memory([suggestion])
    assert len(records["global"]) == 1
    assert records["global"][0]["content"] == "actual note text, no content key"


def test_compile_memory_prefers_content_over_note():
    suggestion = Suggestion(
        family="memory_updates",
        summary="summary text",
        details={"content": "explicit content", "note": "note body"},
    )
    records = compile_memory([suggestion])
    assert records["global"][0]["content"] == "explicit content"


def test_compile_memory_replace_overwrites_matching_entry_by_old_summary():
    existing = {
        "user": [],
        "global": [
            {"summary": "Lite v1 架构决策", "content": "default workflow_only", "confidence": 0.7},
            {"summary": "unrelated fact", "content": "keep me", "confidence": 0.8},
        ],
    }
    suggestions = [
        Suggestion(
            family="memory_updates",
            summary="v1 recovery: history_only confirmed",
            details={
                "scope": "global",
                "action": "replace",
                "old_summary": "Lite v1",
                "content": "v1 recovery is history_only. Lite workflow deferred to v2.",
            },
            confidence=1.0,
        ),
    ]
    records = compile_memory(suggestions, existing_index=existing)
    global_records = records["global"]
    assert len(global_records) == 2
    # Match is by substring of old_summary against existing entry's summary.
    first = next(item for item in global_records if "history_only" in item["summary"])
    assert first["content"].startswith("v1 recovery is history_only")
    assert first["confidence"] == 1.0
    # Unrelated entry is untouched.
    other = next(item for item in global_records if item["summary"] == "unrelated fact")
    assert other["content"] == "keep me"


def test_compile_memory_remove_drops_matching_entry():
    existing = {
        "user": [],
        "global": [
            {"summary": "round1 pending review", "content": "task state noise", "confidence": 0.5},
            {"summary": "durable convention", "content": "keep me", "confidence": 0.9},
        ],
    }
    suggestions = [
        Suggestion(
            family="memory_updates",
            summary="housekeeping: drop stale task-state entry",
            details={
                "scope": "global",
                "action": "remove",
                "old_summary": "round1 pending",
            },
        ),
    ]
    records = compile_memory(suggestions, existing_index=existing)
    contents = [item["content"] for item in records["global"]]
    assert contents == ["keep me"]


def test_compile_memory_replace_skipped_when_old_summary_ambiguous():
    existing = {
        "user": [],
        "global": [
            {"summary": "C2 gap: workflow contract", "content": "first", "confidence": 0.7},
            {"summary": "C2 gap: timeout budget", "content": "second", "confidence": 0.8},
        ],
    }
    suggestions = [
        Suggestion(
            family="memory_updates",
            summary="replacement",
            details={
                "scope": "global",
                "action": "replace",
                "old_summary": "C2 gap",
                "content": "replacement content",
            },
        ),
    ]
    records = compile_memory(suggestions, existing_index=existing)
    # Ambiguous match: both existing entries survive, replacement is discarded.
    contents = sorted(item["content"] for item in records["global"])
    assert contents == ["first", "second"]


def test_compile_memory_replace_with_no_match_is_dropped_silently():
    existing = {
        "user": [],
        "global": [
            {"summary": "unrelated", "content": "keep", "confidence": 0.9},
        ],
    }
    suggestions = [
        Suggestion(
            family="memory_updates",
            summary="nothing to replace",
            details={
                "scope": "global",
                "action": "replace",
                "old_summary": "does not exist",
                "content": "would-be new content",
            },
        ),
    ]
    records = compile_memory(suggestions, existing_index=existing)
    contents = [item["content"] for item in records["global"]]
    # Existing entry untouched, phantom replacement not written as an add.
    assert contents == ["keep"]


def test_compile_memory_tolerates_malformed_existing_entries():
    existing = {
        "user": [
            {"summary": "u", "content": "ok"},
            "not a dict",
            {"summary": "blank", "content": "   "},
            {"content": "bad conf", "confidence": "not a number"},
        ],
        "global": [],
    }
    records = compile_memory([], existing_index=existing)
    contents = [item["content"] for item in records["user"]]
    assert "ok" in contents
    assert "bad conf" in contents
    assert all(content.strip() for content in contents)

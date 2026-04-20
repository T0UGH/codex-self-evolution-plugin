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

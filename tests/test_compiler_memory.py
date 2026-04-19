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

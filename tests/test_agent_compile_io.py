import json

import pytest

from codex_self_evolution.compiler.agent_io import (
    AGENT_COMPILE_SCHEMA_VERSION,
    AgentResponseError,
    build_agent_compile_payload,
    parse_agent_compile_response,
)
from codex_self_evolution.schemas import (
    RecallRecord,
    SkillManifestEntry,
    Suggestion,
    SuggestionEnvelope,
)


def _envelope() -> SuggestionEnvelope:
    return SuggestionEnvelope(
        schema_version=1,
        suggestion_id="sug-1",
        idempotency_key="idem-1",
        thread_id="thread-1",
        cwd="/tmp/repo",
        repo_fingerprint="fp-1",
        reviewer_timestamp="2026-04-20T00:00:00Z",
        suggestions=[
            Suggestion(family="memory_updates", summary="s", details={"content": "c"}),
        ],
        source_authority=[],
    )


def _manifest_entry(skill_id: str = "alpha") -> SkillManifestEntry:
    return SkillManifestEntry(
        skill_id=skill_id,
        action="create",
        title="Alpha",
        path=f"skills/managed/{skill_id}.md",
        status="active",
        owner="codex-self-evolution-plugin",
        managed=True,
        created_by="codex-self-evolution-plugin",
        updated_at="2026-01-01T00:00:00Z",
    )


def _context_with_assets() -> dict:
    return {
        "cwd": "/tmp/repo",
        "repo_fingerprint": "fp-1",
        "skills_dir": "/tmp/state/skills",
        "memory_dir": "/tmp/state/memory",
        "recall_dir": "/tmp/state/recall",
        "existing_manifest": [_manifest_entry()],
        "existing_user_memory": "# USER\n\nuser content\n",
        "existing_global_memory": "# MEMORY\n\nglobal content\n",
        "existing_memory_index": {
            "user": [{"summary": "u1", "content": "user entry"}],
            "global": [{"summary": "g1", "content": "global entry"}],
        },
        "existing_recall_records": [
            {
                "id": "r1",
                "summary": "old recall",
                "content": "old content",
                "source_paths": ["x"],
                "repo_fingerprint": "fp-1",
                "cwd": "/tmp/repo",
            }
        ],
        "existing_recall_markdown": "# Compiled Recall\n",
        "memory_paths": {
            "user": "/tmp/state/memory/USER.md",
            "global": "/tmp/state/memory/MEMORY.md",
            "index": "/tmp/state/memory/memory.json",
        },
        "recall_paths": {
            "index": "/tmp/state/recall/index.json",
            "compiled": "/tmp/state/recall/compiled.md",
        },
    }


def test_build_agent_compile_payload_includes_existing_assets_and_batch():
    payload = build_agent_compile_payload([_envelope()], _context_with_assets())

    assert payload["schema_version"] == AGENT_COMPILE_SCHEMA_VERSION
    assert payload["repo"]["repo_fingerprint"] == "fp-1"
    assert payload["repo"]["memory_dir"] == "/tmp/state/memory"
    assets = payload["existing_assets"]
    assert assets["manifest"][0]["skill_id"] == "alpha"
    assert "user content" in assets["memory"]["user_markdown"]
    assert assets["memory"]["index"]["global"][0]["content"] == "global entry"
    assert assets["recall"]["records"][0]["id"] == "r1"
    assert payload["batch"][0]["suggestion_id"] == "sug-1"
    assert payload["contract"]["schema_version"] == AGENT_COMPILE_SCHEMA_VERSION
    assert "response_schema" in payload["contract"]

    # payload must be serializable so the agent invoker can pipe it through JSON.
    round_trip = json.loads(json.dumps(payload))
    assert round_trip["batch"][0]["thread_id"] == "thread-1"


def test_build_agent_compile_payload_tolerates_minimal_context():
    payload = build_agent_compile_payload([], {})

    assert payload["existing_assets"]["manifest"] == []
    assert payload["existing_assets"]["memory"]["index"] == {"user": [], "global": []}
    assert payload["existing_assets"]["recall"]["records"] == []
    assert payload["batch"] == []


def test_parse_agent_compile_response_happy_path():
    raw = json.dumps(
        {
            "memory_records": {
                "user": [
                    {"summary": "u", "content": "user text", "confidence": 0.8},
                ],
                "global": [
                    {"summary": "g", "content": "global text"},
                ],
            },
            "recall_records": [
                {
                    "id": "rid",
                    "summary": "s",
                    "content": "c",
                    "source_paths": ["p"],
                    "repo_fingerprint": "fp",
                    "cwd": "/tmp",
                }
            ],
            "compiled_skills": [
                {
                    "skill_id": "alpha",
                    "title": "Alpha",
                    "description": "This skill should be used when compiling alpha workflows.",
                    "content": "body",
                    "action": "create",
                }
            ],
            "manifest_entries": [_manifest_entry().to_dict()],
            "discarded_items": [{"reason": "noop"}],
        }
    )

    result = parse_agent_compile_response(raw)

    assert result["memory_records"]["user"][0]["confidence"] == 0.8
    assert result["memory_records"]["user"][0]["scope"] == "user"
    assert isinstance(result["recall_records"][0], RecallRecord)
    assert result["compiled_skills"][0]["action"] == "create"
    assert (
        result["compiled_skills"][0]["description"]
        == "This skill should be used when compiling alpha workflows."
    )
    assert isinstance(result["manifest_entries"][0], SkillManifestEntry)
    assert result["discarded_items"][0]["reason"] == "noop"


def test_parse_agent_compile_response_rejects_empty_string():
    with pytest.raises(AgentResponseError):
        parse_agent_compile_response("")


def test_parse_agent_compile_response_rejects_invalid_json():
    with pytest.raises(AgentResponseError):
        parse_agent_compile_response("{not json")


def test_parse_agent_compile_response_rejects_non_object_top_level():
    with pytest.raises(AgentResponseError):
        parse_agent_compile_response(json.dumps([1, 2, 3]))


def test_parse_agent_compile_response_rejects_bad_skill_action():
    raw = json.dumps(
        {
            "compiled_skills": [
                {"skill_id": "x", "title": "X", "content": "c", "action": "delete"}
            ]
        }
    )
    with pytest.raises(AgentResponseError):
        parse_agent_compile_response(raw)


@pytest.mark.parametrize("action", ["create", "patch", "edit"])
def test_parse_agent_compile_response_rejects_publishable_skill_without_description(action):
    raw = json.dumps(
        {
            "compiled_skills": [
                {"skill_id": "x", "title": "X", "content": "c", "action": action}
            ]
        }
    )
    with pytest.raises(AgentResponseError):
        parse_agent_compile_response(raw)


def test_parse_agent_compile_response_allows_retire_without_description():
    raw = json.dumps(
        {
            "compiled_skills": [
                {"skill_id": "x", "title": "X", "content": "", "action": "retire"}
            ]
        }
    )

    result = parse_agent_compile_response(raw)

    assert result["compiled_skills"][0]["description"] == ""
    assert result["compiled_skills"][0]["action"] == "retire"


def test_parse_agent_compile_response_rejects_missing_memory_fields():
    raw = json.dumps({"memory_records": {"user": [{"summary": "only"}], "global": []}})
    with pytest.raises(AgentResponseError):
        parse_agent_compile_response(raw)


def test_parse_agent_compile_response_defaults_missing_sections_to_empty():
    result = parse_agent_compile_response(json.dumps({}))
    assert result["memory_records"] == {"user": [], "global": []}
    assert result["recall_records"] == []
    assert result["compiled_skills"] == []
    assert result["manifest_entries"] == []
    assert result["discarded_items"] == []

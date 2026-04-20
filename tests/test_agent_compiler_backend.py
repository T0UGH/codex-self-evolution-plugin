import json

import pytest

from codex_self_evolution.compiler.backends import AgentCompilerBackend
from codex_self_evolution.schemas import (
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


def _context() -> dict:
    return {
        "cwd": "/tmp/repo",
        "repo_fingerprint": "fp-1",
        "skills_dir": "/tmp/state/skills",
        "memory_dir": "/tmp/state/memory",
        "recall_dir": "/tmp/state/recall",
        "existing_manifest": [],
        "existing_user_memory": "",
        "existing_global_memory": "",
        "existing_memory_index": {"user": [], "global": []},
        "existing_recall_records": [],
        "existing_recall_markdown": "",
        "memory_paths": {},
        "recall_paths": {},
    }


def _manifest_dict(skill_id: str = "alpha") -> dict:
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
    ).to_dict()


def test_agent_backend_returns_parsed_artifacts_on_success():
    agent_output = {
        "memory_records": {
            "user": [{"summary": "u", "content": "merged user"}],
            "global": [{"summary": "g", "content": "merged global"}],
        },
        "recall_records": [
            {
                "id": "r1",
                "summary": "s",
                "content": "c",
                "source_paths": ["p"],
                "repo_fingerprint": "fp",
                "cwd": "/tmp",
            }
        ],
        "compiled_skills": [
            {"skill_id": "alpha", "title": "Alpha", "content": "body", "action": "create"}
        ],
        "manifest_entries": [_manifest_dict()],
        "discarded_items": [{"reason": "dedupe"}],
    }
    seen_payloads: list[dict] = []

    def invoker(payload, options):
        seen_payloads.append(payload)
        return json.dumps(agent_output)

    backend = AgentCompilerBackend(invoker=invoker)
    artifacts = backend.compile([_envelope()], _context(), {"allow_fallback": True})

    assert artifacts.backend_name == "agent:opencode"
    assert artifacts.fallback_backend is None
    assert artifacts.memory_records["user"][0]["content"] == "merged user"
    assert artifacts.compiled_skills[0]["skill_id"] == "alpha"
    assert artifacts.manifest_entries[0].skill_id == "alpha"
    assert artifacts.discarded_items == [{"reason": "dedupe"}]

    # Payload must include batch + existing_assets so the agent can merge.
    assert seen_payloads, "invoker should have been called"
    assert seen_payloads[0]["batch"][0]["suggestion_id"] == "sug-1"
    assert "existing_assets" in seen_payloads[0]


def test_agent_backend_falls_back_to_script_on_invoker_exception():
    def invoker(payload, options):
        raise RuntimeError("opencode exploded")

    backend = AgentCompilerBackend(invoker=invoker)
    artifacts = backend.compile([_envelope()], _context(), {"allow_fallback": True})

    assert artifacts.backend_name == "agent:opencode"
    assert artifacts.fallback_backend == "script"
    reasons = [item.get("reason") for item in artifacts.discarded_items]
    assert "agent_invoke_failed" in reasons
    failed_entry = next(item for item in artifacts.discarded_items if item.get("reason") == "agent_invoke_failed")
    assert "opencode exploded" in failed_entry.get("detail", "")


def test_agent_backend_falls_back_on_invalid_output():
    def invoker(payload, options):
        return "not json at all"

    backend = AgentCompilerBackend(invoker=invoker)
    artifacts = backend.compile([_envelope()], _context(), {"allow_fallback": True})

    assert artifacts.fallback_backend == "script"
    reasons = [item.get("reason") for item in artifacts.discarded_items]
    assert "agent_output_invalid" in reasons


def test_agent_backend_raises_when_fallback_disabled_and_invoker_fails():
    def invoker(payload, options):
        raise RuntimeError("boom")

    backend = AgentCompilerBackend(invoker=invoker)
    with pytest.raises(RuntimeError) as excinfo:
        backend.compile([_envelope()], _context(), {"allow_fallback": False})
    assert "agent_invoke_failed" in str(excinfo.value)


def test_agent_backend_raises_when_fallback_disabled_and_output_invalid():
    def invoker(payload, options):
        return "{bad json"

    backend = AgentCompilerBackend(invoker=invoker)
    with pytest.raises(RuntimeError) as excinfo:
        backend.compile([_envelope()], _context(), {"allow_fallback": False})
    assert "agent_output_invalid" in str(excinfo.value)

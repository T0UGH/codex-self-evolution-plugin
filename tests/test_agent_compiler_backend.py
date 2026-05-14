import json

import pytest

from pathlib import Path

from codex_self_evolution.compiler.backends import AgentCompileError, AgentCompilerBackend, PiAgentCompilerBackend
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


def _empty_envelope() -> SuggestionEnvelope:
    return SuggestionEnvelope(
        schema_version=1,
        suggestion_id="sug-empty",
        idempotency_key="idem-empty",
        thread_id="thread-1",
        cwd="/tmp/repo",
        repo_fingerprint="fp-1",
        reviewer_timestamp="2026-04-20T00:00:00Z",
        suggestions=[],
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


def _context_with_existing_memory() -> dict:
    context = _context()
    context["existing_memory_index"] = {
        "user": [{"summary": "existing user", "content": "keep user"}],
        "global": [{"summary": "existing global", "content": "keep global"}],
    }
    return context


def _recall_dict(recall_id: str = "r1") -> dict:
    return {
        "id": recall_id,
        "summary": "existing recall",
        "content": "Keep this reusable diagnostic path.",
        "source_paths": ["notes.md"],
        "repo_fingerprint": "fp-1",
        "cwd": "/tmp/repo",
        "thread_id": "thread-1",
        "turn_id": "turn-1",
        "source_updated_at": "2026-01-01T00:00:00Z",
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
            {
                "skill_id": "alpha",
                "title": "Alpha",
                "description": "This skill should be used when compiling alpha workflows.",
                "content": "body",
                "action": "create",
            }
        ],
        "manifest_entries": [_manifest_dict()],
        "discarded_items": [{"reason": "duplicate"}],
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
    assert artifacts.compiled_skills == []
    assert artifacts.manifest_entries == []
    assert artifacts.discarded_items == [{"reason": "duplicate"}]
    assert artifacts.compiler_observability["backend"] == "agent:opencode"
    assert artifacts.compiler_observability["input"]["suggestions"] == 1
    assert artifacts.compiler_observability["output"]["discarded_items"] == 1

    # Payload must include batch + existing_assets so the agent can merge.
    assert seen_payloads, "invoker should have been called"
    assert seen_payloads[0]["batch"][0]["suggestion_id"] == "sug-1"
    assert "existing_assets" in seen_payloads[0]


def test_agent_backend_retries_invoker_exception_then_uses_agent_output():
    calls = 0
    agent_output = {
        "memory_records": {"user": [], "global": [{"summary": "g", "content": "agent recovered"}]},
        "recall_records": [],
        "compiled_skills": [],
        "manifest_entries": [],
        "discarded_items": [],
    }

    def invoker(payload, options):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("opencode exploded")
        assert payload["retry_feedback"]["reason"] == "agent_invoke_failed"
        return json.dumps(agent_output)

    backend = AgentCompilerBackend(invoker=invoker)
    artifacts = backend.compile([_envelope()], _context(), {"allow_fallback": True})

    assert artifacts.backend_name == "agent:opencode"
    assert artifacts.fallback_backend is None
    assert artifacts.memory_records["global"][0]["content"] == "agent recovered"
    assert calls == 2


def test_agent_backend_raises_on_invalid_output_after_retry_budget():
    def invoker(payload, options):
        return "not json at all"

    backend = AgentCompilerBackend(invoker=invoker)
    with pytest.raises(AgentCompileError) as excinfo:
        backend.compile([_envelope()], _context(), {"allow_fallback": True})
    assert excinfo.value.reason == "agent_output_invalid"


def test_agent_backend_retries_when_output_drops_batch_without_discarding():
    bad_output = {
        "memory_records": {"user": [], "global": []},
        "recall_records": [],
        "compiled_skills": [],
        "manifest_entries": [],
        "discarded_items": [],
    }
    good_output = {
        "memory_records": {"user": [], "global": [{"summary": "s", "content": "c"}]},
        "recall_records": [],
        "compiled_skills": [],
        "manifest_entries": [],
        "discarded_items": [],
    }
    seen_payloads = []

    def invoker(payload, options):
        seen_payloads.append(payload)
        return json.dumps(bad_output if len(seen_payloads) == 1 else good_output)

    backend = AgentCompilerBackend(invoker=invoker)
    artifacts = backend.compile([_envelope()], _context(), {"allow_fallback": True})

    assert artifacts.backend_name == "agent:opencode"
    assert artifacts.fallback_backend is None
    assert artifacts.memory_records["global"][0]["summary"] == "s"
    assert seen_payloads[1]["retry_feedback"]["reason"] == "agent_output_empty_unaccounted"
    assert artifacts.compiler_observability["attempts"] == 2
    assert artifacts.compiler_observability["retry_feedback"][0]["reason"] == "agent_output_empty_unaccounted"


def test_agent_backend_accepts_fully_accounted_discarded_suggestions():
    all_discarded_output = {
        "memory_records": {"user": [], "global": []},
        "recall_records": [],
        "compiled_skills": [],
        "manifest_entries": [],
        "discarded_items": [{"suggestion_id": "sug-1", "reason": "weak_evidence"}],
    }
    seen_payloads = []

    def invoker(payload, options):
        seen_payloads.append(payload)
        return json.dumps(all_discarded_output)

    backend = AgentCompilerBackend(invoker=invoker)
    artifacts = backend.compile([_envelope()], _context(), {"allow_fallback": True})

    assert artifacts.memory_records == {"user": [], "global": []}
    assert artifacts.discarded_items == [{"suggestion_id": "sug-1", "reason": "weak_evidence"}]
    assert len(seen_payloads) == 1


def test_agent_backend_raises_when_output_drops_existing_memory_after_retry_budget():
    bad_output = {
        "memory_records": {"user": [], "global": []},
        "recall_records": [],
        "compiled_skills": [],
        "manifest_entries": [],
        "discarded_items": [],
    }

    def invoker(payload, options):
        return json.dumps(bad_output)

    backend = AgentCompilerBackend(invoker=invoker)
    with pytest.raises(AgentCompileError) as excinfo:
        backend.compile([_empty_envelope()], _context_with_existing_memory(), {"allow_fallback": True})
    assert excinfo.value.reason == "agent_output_dropped_existing_assets"


def test_agent_backend_raises_when_fallback_disabled_and_invoker_fails():
    def invoker(payload, options):
        raise RuntimeError("boom")

    backend = AgentCompilerBackend(invoker=invoker)
    with pytest.raises(AgentCompileError) as excinfo:
        backend.compile([_envelope()], _context(), {"allow_fallback": False})
    assert excinfo.value.reason == "agent_invoke_failed"


def test_agent_backend_raises_when_fallback_disabled_and_output_invalid():
    def invoker(payload, options):
        return "{bad json"

    backend = AgentCompilerBackend(invoker=invoker)
    with pytest.raises(AgentCompileError) as excinfo:
        backend.compile([_envelope()], _context(), {"allow_fallback": False})
    assert excinfo.value.reason == "agent_output_invalid"


def test_pi_backend_edit_mode_reads_agent_edited_workspace():
    def invoker(payload, options):
        workspace = Path(payload["workspace_dir"])
        memory_path = workspace / "assets" / "memory" / "memory.json"
        result_path = workspace / "compiler" / "result.json"
        memory_path.write_text(
            json.dumps(
                {
                    "user": [],
                    "global": [
                        {
                            "summary": "direct edit memory",
                            "content": "Pi edited the workspace file directly.",
                            "source_paths": ["review.md"],
                            "confidence": 0.9,
                            "provenance": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        result_path.write_text(
            json.dumps({"discarded_items": [{"suggestion_id": "old", "reason": "duplicate"}]}),
            encoding="utf-8",
        )
        return "DONE"

    backend = PiAgentCompilerBackend(invoker=invoker)
    artifacts = backend.compile([_envelope()], _context(), {"allow_fallback": True, "pi_mode": "edit"})

    assert artifacts.backend_name == "agent:pi"
    assert artifacts.memory_records["global"][0]["summary"] == "direct edit memory"
    assert artifacts.memory_records["global"][0]["content"] == "Pi edited the workspace file directly."
    assert artifacts.discarded_items == [{"suggestion_id": "old", "reason": "duplicate"}]
    assert artifacts.compiler_observability["provider"] == "kimi"
    assert artifacts.compiler_observability["model"] == "kimi-k2.6"
    assert artifacts.compiler_observability["mode"] == "edit"
    assert artifacts.compiler_observability["input"]["families"] == {
        "memory_updates": 1,
        "recall_candidate": 0,
        "skill_action": 0,
    }


def test_pi_backend_edit_mode_restores_existing_recall_when_agent_drops_index():
    context = _context()
    context["existing_recall_records"] = [_recall_dict()]

    def invoker(payload, options):
        workspace = Path(payload["workspace_dir"])
        memory_path = workspace / "assets" / "memory" / "memory.json"
        recall_path = workspace / "assets" / "recall" / "index.json"
        memory_path.write_text(
            json.dumps(
                {
                    "user": [],
                    "global": [
                        {
                            "summary": "new memory",
                            "content": "Keep the new reusable memory.",
                            "source_paths": ["review.md"],
                            "confidence": 0.9,
                            "provenance": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        recall_path.write_text(json.dumps({"records": []}), encoding="utf-8")
        return "DONE"

    backend = PiAgentCompilerBackend(invoker=invoker)
    artifacts = backend.compile([_envelope()], context, {"allow_fallback": True, "pi_mode": "edit"})

    assert artifacts.recall_records[0].id == "r1"
    assert artifacts.compiler_observability["output"]["recall_records"] == 1


def test_pi_backend_edit_mode_discards_invalid_optional_skill_metadata():
    def invoker(payload, options):
        workspace = Path(payload["workspace_dir"])
        memory_path = workspace / "assets" / "memory" / "memory.json"
        result_path = workspace / "compiler" / "result.json"
        memory_path.write_text(
            json.dumps(
                {
                    "user": [],
                    "global": [
                        {
                            "summary": "new memory",
                            "content": "Keep the new reusable memory.",
                            "source_paths": ["review.md"],
                            "confidence": 0.9,
                            "provenance": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        result_path.write_text(
            json.dumps(
                {
                    "compiled_skills": [
                        {
                            "skill_id": "bad",
                            "title": "Bad",
                            "description": "This skill should be used when metadata is invalid.",
                            "content": "Track invalid metadata without failing the whole compile.",
                            "action": "track",
                        }
                    ],
                    "discarded_items": [],
                }
            ),
            encoding="utf-8",
        )
        return "DONE"

    backend = PiAgentCompilerBackend(invoker=invoker)
    artifacts = backend.compile([_envelope()], _context(), {"allow_fallback": True, "pi_mode": "edit"})

    assert artifacts.compiled_skills == []
    assert artifacts.discarded_items[0]["reason"] == "weak_evidence"
    assert artifacts.discarded_items[0]["skill_id"] == "bad"
    assert "invalid_compiled_skill_metadata" in artifacts.discarded_items[0]["detail"]


def test_pi_backend_edit_mode_drops_malformed_memory_items_without_losing_valid_items():
    def invoker(payload, options):
        workspace = Path(payload["workspace_dir"])
        memory_path = workspace / "assets" / "memory" / "memory.json"
        memory_path.write_text(
            json.dumps(
                {
                    "user": [],
                    "global": [
                        {"summary": "", "content": ""},
                        {
                            "summary": "valid memory",
                            "content": "Keep the valid memory item even when a sibling item is malformed.",
                            "source_paths": ["review.md"],
                            "confidence": 0.9,
                            "provenance": [],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        return "DONE"

    backend = PiAgentCompilerBackend(invoker=invoker)
    artifacts = backend.compile([_envelope()], _context(), {"allow_fallback": True, "pi_mode": "edit"})

    assert [item["summary"] for item in artifacts.memory_records["global"]] == ["valid memory"]
    assert artifacts.discarded_items[0]["artifact_error"] is True
    assert "invalid_memory_record.global" in artifacts.discarded_items[0]["detail"]


def test_pi_backend_edit_mode_fills_recall_repo_context_fields():
    def invoker(payload, options):
        workspace = Path(payload["workspace_dir"])
        memory_path = workspace / "assets" / "memory" / "memory.json"
        recall_path = workspace / "assets" / "recall" / "index.json"
        memory_path.write_text(
            json.dumps(
                {
                    "user": [],
                    "global": [
                        {
                            "summary": "new memory",
                            "content": "Keep the new reusable memory.",
                            "source_paths": ["review.md"],
                            "confidence": 0.9,
                            "provenance": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        recall_record = _recall_dict()
        recall_record.pop("repo_fingerprint")
        recall_record.pop("cwd")
        recall_path.write_text(json.dumps({"records": [recall_record]}), encoding="utf-8")
        return "DONE"

    backend = PiAgentCompilerBackend(invoker=invoker)
    artifacts = backend.compile([_envelope()], _context(), {"allow_fallback": True, "pi_mode": "edit"})

    assert artifacts.recall_records[0].repo_fingerprint == "fp-1"
    assert artifacts.recall_records[0].cwd == "/tmp/repo"


def test_pi_backend_edit_mode_falls_back_from_invalid_manifest_metadata():
    context = _context()
    context["existing_manifest"] = [SkillManifestEntry.from_dict(_manifest_dict("alpha"))]

    def invoker(payload, options):
        workspace = Path(payload["workspace_dir"])
        memory_path = workspace / "assets" / "memory" / "memory.json"
        manifest_path = workspace / "assets" / "skills" / "manifest.json"
        result_path = workspace / "compiler" / "result.json"
        memory_path.write_text(
            json.dumps(
                {
                    "user": [],
                    "global": [
                        {
                            "summary": "new memory",
                            "content": "Keep the new reusable memory.",
                            "source_paths": ["review.md"],
                            "confidence": 0.9,
                            "provenance": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        invalid_manifest = _manifest_dict("broken")
        invalid_manifest["managed"] = "true"
        manifest_path.write_text(json.dumps({"skills": [invalid_manifest]}), encoding="utf-8")
        result_path.write_text(
            json.dumps({"manifest_entries": [{"action": "track"}], "discarded_items": []}),
            encoding="utf-8",
        )
        return "DONE"

    backend = PiAgentCompilerBackend(invoker=invoker)
    artifacts = backend.compile([_envelope()], context, {"allow_fallback": True, "pi_mode": "edit"})

    assert artifacts.manifest_entries[0].skill_id == "alpha"
    details = [item["detail"] for item in artifacts.discarded_items]
    assert any("invalid_workspace_manifest" in detail for detail in details)
    assert any("invalid_result_manifest_entries" in detail for detail in details)

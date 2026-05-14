from __future__ import annotations

import json
from pathlib import Path

from codex_self_evolution.compiler.backends import ScriptCompilerBackend, build_compile_context
from codex_self_evolution.compiler.engine import run_compile
from codex_self_evolution.config import build_paths
from codex_self_evolution.schemas import Suggestion, SuggestionEnvelope
from codex_self_evolution.storage import append_pending_suggestion


def _envelope(suggestions: list[Suggestion]) -> SuggestionEnvelope:
    return SuggestionEnvelope(
        schema_version=1,
        suggestion_id="s1",
        idempotency_key="i1",
        thread_id="t1",
        cwd="/repo",
        repo_fingerprint="repo",
        reviewer_timestamp="2026-05-13T00:00:00Z",
        suggestions=suggestions,
        source_authority=[],
    )


def _skill_suggestion() -> Suggestion:
    return Suggestion(
        family="skill_action",
        summary="create skill",
        details={
            "action": "create",
            "skill_id": "old",
            "title": "Old",
            "description": "Use when old flow repeats.",
            "content": "Workflow steps are old.",
        },
    )


def test_script_compiler_discards_skill_action_without_outputs(tmp_path: Path) -> None:
    paths = build_paths(repo_root=tmp_path, state_dir=tmp_path / "state")
    envelope = _envelope([_skill_suggestion()])
    context = build_compile_context(paths, [envelope])

    artifacts = ScriptCompilerBackend().compile([envelope], context, {})

    assert artifacts.compiled_skills == []
    assert artifacts.manifest_entries == []
    assert artifacts.discarded_items[0]["reason"] == "skill_action_disabled"


def test_run_compile_does_not_write_managed_skill_or_global_projection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CSEP_CODEX_SKILLS_DIR", str(tmp_path / "codex-skills"))
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    paths = build_paths(repo_root=repo, state_dir=state)
    append_pending_suggestion(paths, _envelope([_skill_suggestion()]))

    result = run_compile(repo_root=repo, state_dir=state, backend="script")

    assert result["status"] == "success"
    assert not (state / "skills" / "managed" / "old.md").exists()
    assert not (tmp_path / "codex-skills" / "csep-old").exists()
    receipt = json.loads((state / "compiler" / "last_receipt.json").read_text(encoding="utf-8"))
    assert receipt["managed_skills"] == 0
    assert any(item.get("reason") == "skill_action_disabled" for item in receipt["item_receipts"])

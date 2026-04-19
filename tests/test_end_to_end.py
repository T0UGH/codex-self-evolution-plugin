import json

from codex_self_evolution.compiler.engine import preflight_compile, run_compile
from codex_self_evolution.hooks.session_start import session_start
from codex_self_evolution.hooks.stop_review import stop_review
from codex_self_evolution.recall.workflow import build_focused_recall, evaluate_recall_trigger



def test_end_to_end_loop(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"

    session = session_start(cwd=repo, state_dir=state)
    assert "stable_background" in session
    assert "policy" in session["recall"]

    payload = tmp_path / "stop_payload.json"
    payload.write_text(
        json.dumps(
            {
                "thread_id": "thread-e2e",
                "turn_id": "turn-1",
                "cwd": str(repo),
                "transcript": "created a durable recall and skill",
                "thread_read_output": "repo specific detail",
                "reviewer_provider": "dummy",
                "provider_stub_response": {
                    "memory_updates": [
                        {"summary": "User preference", "details": {"content": "Prefer concise summaries", "scope": "user"}},
                        {"summary": "Keep pytest focused", "details": {"content": "Run focused pytest before full suite", "scope": "global"}},
                    ],
                    "recall_candidate": [{"summary": "Focused pytest", "details": {"content": "Run focused pytest before full suite", "source_paths": ["tests/test_end_to_end.py"]}}],
                    "skill_action": [{"summary": "Add test skill", "details": {"action": "create", "skill_id": "test-skill", "title": "Test Skill", "content": "Run focused tests before a broader regression pass."}}],
                },
            }
        ),
        encoding="utf-8",
    )
    stop = stop_review(hook_payload=payload, state_dir=state)
    assert stop["suggestion_count"] == 4
    assert preflight_compile(repo_root=repo, state_dir=state)["status"] == "run"

    compile_result = run_compile(repo_root=repo, state_dir=state, backend="agent:opencode")
    assert compile_result["processed_count"] == 1
    assert (state / "skills" / "managed" / "test-skill.md").exists()
    assert (state / "memory" / "USER.md").exists()
    assert (state / "memory" / "MEMORY.md").exists()
    assert "Prefer concise summaries" in (state / "memory" / "USER.md").read_text(encoding="utf-8")
    assert "Run focused pytest before full suite" in (state / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    receipt = json.loads((state / "compiler" / "last_receipt.json").read_text(encoding="utf-8"))
    assert receipt["fallback_backend"] == "script"
    assert (state / "suggestions" / "done").glob("*.json")

    trigger = evaluate_recall_trigger("remember focused pytest workflow")
    assert trigger["triggered"] is True
    focused = build_focused_recall(query="focused pytest", cwd=repo, state_dir=state)
    assert focused["results"]
    assert focused["results"][0]["summary"] == "Focused pytest"

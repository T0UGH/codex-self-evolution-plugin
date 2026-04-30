import json

from codex_self_evolution.compiler.engine import apply_compiler_outputs
from codex_self_evolution.hooks.session_start import session_start


def test_session_start_injects_memory_recall_policy_and_session_recall_skill(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    apply_compiler_outputs(
        memory_dir=state / "memory",
        recall_dir=state / "recall",
        skills_dir=state / "skills",
        memory_records={
            "user": [{"summary": "User pref", "content": "Be concise."}],
            "global": [{"summary": "Repo fact", "content": "Run focused tests first."}],
        },
        recall_records=[],
        compiled_skills=[],
        manifest_entries=[],
        existing_entries=[],
    )
    result = session_start(cwd=repo, state_dir=state)
    assert result["hook"] == "SessionStart"
    assert "recall" in result["recall"]["policy"].lower()
    assert "session_recall" == result["recall"]["skill"]["skill_id"]
    assert "csep recall" in result["recall"]["skill"]["content"].lower()
    assert result["runtime"]["session_context"]["thread_start_injected"] is True
    assert "Be concise." in result["stable_background"]["current_user_md"]
    assert "Run focused tests first." in result["stable_background"]["current_memory_md"]
    assert "# Stable Background" in result["stable_background"]["combined_prefix"]
    assert "## Recall Contract" in result["stable_background"]["combined_prefix"]
    assert "# Session Recall Skill" in result["stable_background"]["combined_prefix"]
    assert (state / "suggestions" / "pending").exists()
    json.dumps(result)

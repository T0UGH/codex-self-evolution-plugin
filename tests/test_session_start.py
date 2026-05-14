import json

from codex_self_evolution.hooks.session_start import session_start


def test_session_start_injects_memory_recall_policy_and_session_recall_skill(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    (state / "memory").mkdir(parents=True)
    (state / "memory" / "USER.md").write_text("# USER\n\nBe concise.\n", encoding="utf-8")
    (state / "memory" / "MEMORY.md").write_text("# MEMORY\n\nRun focused tests first.\n", encoding="utf-8")
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
    assert (state / "memory").exists()
    json.dumps(result)

import json

from codex_self_evolution.hooks.session_start import session_start


def test_session_start_injects_memory_and_short_recall_pointer(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    (state / "memory").mkdir(parents=True)
    (state / "memory" / "USER.md").write_text("# USER\n\nBe concise.\n", encoding="utf-8")
    (state / "memory" / "MEMORY.md").write_text("# MEMORY\n\nRun focused tests first.\n", encoding="utf-8")
    result = session_start(cwd=repo, state_dir=state)
    assert result["hook"] == "SessionStart"
    assert "recall" in result["recall"]["policy"].lower()
    assert "csep-session-recall" == result["recall"]["skill"]["skill_id"]
    assert result["recall"]["skill"]["provided_by"] == "plugin"
    assert "content" not in result["recall"]["skill"]
    assert result["runtime"]["session_context"]["thread_start_injected"] is True
    assert "Be concise." in result["stable_background"]["current_user_md"]
    assert "Run focused tests first." in result["stable_background"]["current_memory_md"]
    assert "# Stable Background" in result["stable_background"]["combined_prefix"]
    assert "## Recall Contract" not in result["stable_background"]["combined_prefix"]
    assert "# Session Recall Skill" not in result["stable_background"]["combined_prefix"]
    assert (state / "memory").exists()
    json.dumps(result)

import json

from codex_self_evolution.hooks.session_start import session_start


def test_session_start_injects_memory_and_short_recall_pointer(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    (state / "memory").mkdir(parents=True)
    (state / "memory" / "USER.md").write_text("# USER\n\nDo not inject me.\n", encoding="utf-8")
    (state / "memory" / "MEMORY.md").write_text("# MEMORY\n\nRun focused tests first.\n", encoding="utf-8")
    (state / "memory" / "refs").mkdir()
    (state / "memory" / "refs" / "detail.md").write_text("Cold detail.\n", encoding="utf-8")
    result = session_start(cwd=repo, state_dir=state)
    assert result["hook"] == "SessionStart"
    assert "recall" in result["recall"]["policy"].lower()
    assert "csep-session-recall" == result["recall"]["skill"]["skill_id"]
    assert result["recall"]["skill"]["provided_by"] == "plugin"
    assert "content" not in result["recall"]["skill"]
    assert result["runtime"]["session_context"]["thread_start_injected"] is True
    assert "Run focused tests first." in result["stable_background"]["current_memory_md"]
    assert "Do not inject me." not in result["stable_background"]["combined_prefix"]
    assert "Cold detail." not in result["stable_background"]["combined_prefix"]
    assert result["stable_background"]["legacy_user_md_ignored"] is True
    assert "# Stable Background" in result["stable_background"]["combined_prefix"]
    assert "## Recall Contract" not in result["stable_background"]["combined_prefix"]
    assert "# Session Recall Skill" not in result["stable_background"]["combined_prefix"]
    assert result["stable_background"]["memory_source"] == "MEMORY.md"
    assert result["stable_background"]["memory_fallback_used"] is True
    assert result["stable_background"]["memory_fallback_reason"] == "summary_missing"
    assert result["stable_background"]["memory_source_path"] == str(state / "memory" / "MEMORY.md")
    assert result["stable_background"]["memory_summary_path"] == str(state / "memory" / "memory_summary.md")
    assert result["stable_background"]["memory_summary_meta_path"] == str(state / "memory" / "memory_summary.meta.json")
    assert (state / "memory").exists()
    assert (state / "memory" / "refs").exists()
    json.dumps(result)

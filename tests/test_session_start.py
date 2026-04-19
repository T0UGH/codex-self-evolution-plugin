import json

from codex_self_evolution.hooks.session_start import session_start
from codex_self_evolution.writer import write_memory


def test_session_start_injects_memory_and_recall_policy(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    write_memory(
        state / "memory",
        {
            "user": [{"summary": "User pref", "content": "Be concise."}],
            "global": [{"summary": "Repo fact", "content": "Run focused tests first."}],
        },
    )
    result = session_start(cwd=repo, state_dir=state)
    assert result["hook"] == "SessionStart"
    assert "recall" in result["recall"]["policy"].lower()
    assert "Be concise." in result["stable_background"]["current_user_md"]
    assert "Run focused tests first." in result["stable_background"]["current_memory_md"]
    assert "# Stable Background" in result["stable_background"]["combined_prefix"]
    assert (state / "suggestions" / "pending").exists()
    json.dumps(result)

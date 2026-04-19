import json
from pathlib import Path

from codex_self_evolution.hooks.stop_review import stop_review



def test_stop_review_reconstructs_snapshot_and_writes_pending_artifact(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    transcript_path = tmp_path / "turn.txt"
    transcript_path.write_text("user asked for durable fix", encoding="utf-8")
    thread_path = tmp_path / "thread.txt"
    thread_path.write_text("diff mentions stable config", encoding="utf-8")
    payload = tmp_path / "payload.json"
    payload.write_text(
        json.dumps(
            {
                "thread_id": "thread-1",
                "turn_id": "turn-1",
                "cwd": str(repo),
                "transcript_path": str(transcript_path),
                "thread_read_path": str(thread_path),
                "reviewer_provider": "dummy",
                "provider_stub_response": {
                    "memory_updates": [{"summary": "remember config", "details": {"content": "Use stable config."}}],
                    "recall_candidate": [],
                    "skill_action": [],
                },
            }
        ),
        encoding="utf-8",
    )
    result = stop_review(hook_payload=payload, state_dir=state)
    assert result["hook"] == "Stop"
    pending_path = state / "suggestions" / "pending"
    files = list(pending_path.glob("*.json"))
    assert len(files) == 1
    stored = json.loads(files[0].read_text(encoding="utf-8"))
    assert stored["thread_id"] == "thread-1"
    assert stored["state"] == "pending"
    assert stored["review_snapshot_path"]
    snapshot = json.loads((state / "review" / "snapshots" / Path(stored["review_snapshot_path"]).name).read_text(encoding="utf-8"))
    assert snapshot["turn_snapshot"]["transcript"] == "user asked for durable fix"
    assert snapshot["turn_snapshot"]["thread_read_output"] == "diff mentions stable config"
    assert len(stored["suggestions"]) == 1

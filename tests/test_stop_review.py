import json
from pathlib import Path

import pytest

from codex_self_evolution.hooks.stop_review import stop_review
from codex_self_evolution.review.runner import ReviewerParseFailure



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



def test_stop_review_returns_suggestion_families_breakdown(tmp_path):
    """The per-family breakdown is what lets plugin.log distinguish a
    reviewer that's emitting 0 memory_updates (SKIP too strict) from one
    that's emitting 0 of anything at all (upstream failure). Downstream,
    cli._observability_extras reads this dict and forwards it into the log
    line so operators can see it without re-parsing done/ receipts."""
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    payload = tmp_path / "payload.json"
    payload.write_text(
        json.dumps(
            {
                "thread_id": "thread-fam",
                "turn_id": "turn-fam",
                "cwd": str(repo),
                "reviewer_provider": "dummy",
                "provider_stub_response": {
                    "memory_updates": [
                        {"summary": "m1", "details": {"content": "a"}},
                        {"summary": "m2", "details": {"content": "b", "scope": "user"}},
                    ],
                    "recall_candidate": [
                        {"summary": "r1", "details": {"content": "c"}},
                    ],
                    "skill_action": [],
                },
            }
        ),
        encoding="utf-8",
    )
    result = stop_review(hook_payload=payload, state_dir=state)
    assert result["suggestion_count"] == 3
    assert result["skipped_suggestion_count"] == 0
    assert result["suggestion_families"] == {
        "memory_updates": 2,
        "recall_candidate": 1,
        "skill_action": 0,
    }
    assert result["reviewer_provider"] == "dummy"


def test_stop_review_dumps_raw_text_when_reviewer_parse_fails(tmp_path):
    """Truncated-JSON scenarios (e.g. max_tokens cutoff) should land the
    reviewer's raw response on disk under review/failed/ so we can inspect
    where it broke instead of losing it to the background subprocess log."""
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    payload = tmp_path / "payload.json"
    truncated_raw = '{"memory_updates": [{"summary": "good", "details": {"content": "abc'  # unterminated
    payload.write_text(
        json.dumps(
            {
                "thread_id": "thread-trunc",
                "turn_id": "turn-trunc",
                "cwd": str(repo),
                "reviewer_provider": "dummy",
                "provider_stub_response": truncated_raw,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ReviewerParseFailure):
        stop_review(hook_payload=payload, state_dir=state)

    failed_dir = state / "review" / "failed"
    dumps = list(failed_dir.glob("*.txt"))
    assert len(dumps) == 1, "exactly one raw dump should be written"
    content = dumps[0].read_text(encoding="utf-8")
    # Header metadata must name the provider and attempt count.
    assert "# provider: dummy" in content
    assert "# attempts: 3" in content  # default parse_retries=2 → 1+2 attempts
    # And each attempt's body must include the truncated raw text itself.
    assert truncated_raw in content
    assert "--- attempt 1" in content
    assert "--- attempt 3" in content

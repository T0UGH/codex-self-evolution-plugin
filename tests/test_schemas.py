import pytest

from codex_self_evolution.schemas import ReviewerOutput, SchemaError, SuggestionEnvelope



def test_reviewer_output_accepts_three_fixed_families():
    output = ReviewerOutput.from_dict(
        {
            "memory_updates": [{"summary": "a", "details": {"content": "b"}, "confidence": 0.8}],
            "recall_candidate": [{"summary": "c", "details": {"content": "d"}}],
            "skill_action": [{"summary": "e", "details": {"action": "patch", "content": "enough words here", "skill_id": "x"}}],
        }
    )
    assert len(output.all_suggestions()) == 3



def test_reviewer_output_rejects_legacy_skill_action_vocabulary():
    with pytest.raises(SchemaError):
        ReviewerOutput.from_dict(
            {
                "memory_updates": [],
                "recall_candidate": [],
                "skill_action": [{"summary": "legacy", "details": {"action": "update", "content": "enough words here"}}],
            }
        )



def test_reviewer_output_rejects_unexpected_keys():
    with pytest.raises(SchemaError):
        ReviewerOutput.from_dict({"memory_updates": [], "other": []})



def test_suggestion_envelope_requires_schema_version_one_and_state():
    with pytest.raises(SchemaError):
        SuggestionEnvelope.from_dict(
            {
                "schema_version": 2,
                "suggestion_id": "s1",
                "idempotency_key": "i1",
                "thread_id": "t1",
                "cwd": "/tmp",
                "repo_fingerprint": "abc",
                "reviewer_timestamp": "2026-01-01T00:00:00Z",
                "suggestions": [],
                "source_authority": [],
            }
        )

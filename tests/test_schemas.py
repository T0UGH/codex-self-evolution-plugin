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



def test_memory_updates_accepts_optional_action_and_scope():
    output = ReviewerOutput.from_dict(
        {
            "memory_updates": [
                {
                    "summary": "updated fact",
                    "details": {
                        "scope": "global",
                        "action": "replace",
                        "old_summary": "old fact",
                        "content": "new content",
                    },
                }
            ],
            "recall_candidate": [],
            "skill_action": [],
        }
    )
    suggestion = output.memory_updates[0]
    assert suggestion.details["action"] == "replace"
    assert suggestion.details["old_summary"] == "old fact"


def test_memory_updates_rejects_invalid_action():
    with pytest.raises(SchemaError):
        ReviewerOutput.from_dict(
            {
                "memory_updates": [
                    {
                        "summary": "bad",
                        "details": {"scope": "global", "action": "upsert", "content": "x"},
                    }
                ],
                "recall_candidate": [],
                "skill_action": [],
            }
        )


def test_memory_updates_rejects_invalid_scope():
    with pytest.raises(SchemaError):
        ReviewerOutput.from_dict(
            {
                "memory_updates": [
                    {
                        "summary": "bad",
                        "details": {"scope": "team", "content": "x"},
                    }
                ],
                "recall_candidate": [],
                "skill_action": [],
            }
        )


def test_memory_updates_replace_requires_old_summary():
    with pytest.raises(SchemaError):
        ReviewerOutput.from_dict(
            {
                "memory_updates": [
                    {
                        "summary": "bad",
                        "details": {"scope": "global", "action": "replace", "content": "x"},
                    }
                ],
                "recall_candidate": [],
                "skill_action": [],
            }
        )


def test_memory_updates_remove_requires_old_summary():
    with pytest.raises(SchemaError):
        ReviewerOutput.from_dict(
            {
                "memory_updates": [
                    {
                        "summary": "bad",
                        "details": {"scope": "global", "action": "remove"},
                    }
                ],
                "recall_candidate": [],
                "skill_action": [],
            }
        )


def test_memory_updates_without_action_still_parses_as_add():
    # Legacy queued suggestions have no `action` key; they must still parse so
    # the pipeline doesn't strand pre-upgrade work in pending/.
    output = ReviewerOutput.from_dict(
        {
            "memory_updates": [
                {"summary": "a", "details": {"content": "b"}},
            ],
            "recall_candidate": [],
            "skill_action": [],
        }
    )
    assert output.memory_updates[0].details.get("action") is None


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

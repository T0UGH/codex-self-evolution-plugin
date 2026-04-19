import pytest

from codex_self_evolution.review.providers import ReviewProviderError, get_review_provider
from codex_self_evolution.review.runner import run_reviewer
from codex_self_evolution.schemas import SchemaError



def test_dummy_reviewer_provider_returns_structured_output():
    output, provider_result = run_reviewer(
        {"reviewer_provider": "dummy", "provider_stub_response": {"memory_updates": [{"summary": "a", "details": {"content": "b"}}], "recall_candidate": [], "skill_action": []}}
    )
    assert provider_result.provider == "dummy"
    assert len(output.memory_updates) == 1



def test_openai_and_anthropic_request_shapes_are_supported():
    openai_provider = get_review_provider("openai-compatible")
    anthropic_provider = get_review_provider("anthropic-style")
    snapshot = {"context": {"thread_id": "t1"}}
    openai_payload = openai_provider.build_request_payload(snapshot, "prompt", {"model": "x"})
    anthropic_payload = anthropic_provider.build_request_payload(snapshot, "prompt", {"model": "x"})
    assert openai_payload["messages"][0]["role"] == "system"
    assert anthropic_payload["system"] == "prompt"



def test_reviewer_rejects_malformed_json():
    with pytest.raises(SchemaError):
        run_reviewer({}, reviewer_output_override="not-json")



def test_http_provider_requires_api_base():
    with pytest.raises(ReviewProviderError):
        run_reviewer({"reviewer_provider": "openai-compatible"})

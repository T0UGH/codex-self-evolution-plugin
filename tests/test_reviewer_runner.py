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



def test_openai_anthropic_and_minimax_request_shapes_are_supported():
    openai_provider = get_review_provider("openai-compatible")
    anthropic_provider = get_review_provider("anthropic-style")
    minimax_provider = get_review_provider("minimax")
    snapshot = {"context": {"thread_id": "t1"}}
    openai_payload = openai_provider.build_request_payload(snapshot, "prompt", {"model": "x"})
    anthropic_payload = anthropic_provider.build_request_payload(snapshot, "prompt", {"model": "x"})
    minimax_payload = minimax_provider.build_request_payload(snapshot, "prompt", {"model": "x"})
    assert openai_payload["messages"][0]["role"] == "system"
    assert anthropic_payload["system"] == "prompt"
    assert minimax_payload["system"] == "prompt"



def test_provider_headers_match_dialect(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.setenv("MINIMAX_API_KEY", "minimax-key")
    openai_provider = get_review_provider("openai-compatible")
    anthropic_provider = get_review_provider("anthropic-style")
    minimax_provider = get_review_provider("minimax")
    openai_headers = openai_provider.build_headers({})
    anthropic_headers = anthropic_provider.build_headers({})
    minimax_headers = minimax_provider.build_headers({})
    assert openai_headers["Authorization"] == "Bearer openai-key"
    assert anthropic_headers["x-api-key"] == "anthropic-key"
    assert minimax_headers["Authorization"] == "Bearer minimax-key"
    assert "anthropic-version" in anthropic_headers
    assert "anthropic-version" not in minimax_headers



def test_minimax_default_endpoint_uses_messages_api(monkeypatch):
    monkeypatch.delenv("MINIMAX_BASE_URL", raising=False)
    monkeypatch.delenv("MINIMAX_REGION", raising=False)
    provider = get_review_provider("minimax")
    assert provider.default_api_base() == "https://api.minimax.io/anthropic/v1/messages"
    monkeypatch.setenv("MINIMAX_REGION", "cn")
    assert provider.default_api_base() == "https://api.minimaxi.com/anthropic/v1/messages"



def test_reviewer_rejects_malformed_json():
    with pytest.raises(SchemaError):
        run_reviewer({}, reviewer_output_override="not-json")



def test_http_provider_requires_api_key_when_env_missing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ReviewProviderError):
        run_reviewer({"reviewer_provider": "openai-compatible"}, provider_options={"api_base": "https://example.com"})



def test_minimax_provider_requires_api_key_when_env_missing(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    with pytest.raises(ReviewProviderError):
        run_reviewer({"reviewer_provider": "minimax"}, provider_options={"api_base": "https://api.minimax.io/anthropic/v1/messages"})

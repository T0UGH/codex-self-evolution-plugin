import pytest

from codex_self_evolution.review.providers import ReviewProviderError, get_review_provider
from codex_self_evolution.review.runner import run_reviewer
from codex_self_evolution.schemas import SchemaError



def test_dummy_reviewer_provider_returns_structured_output():
    output, provider_result, skipped = run_reviewer(
        {"reviewer_provider": "dummy", "provider_stub_response": {"memory_updates": [{"summary": "a", "details": {"content": "b"}}], "recall_candidate": [], "skill_action": []}}
    )
    assert provider_result.provider == "dummy"
    assert len(output.memory_updates) == 1
    assert skipped == []



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



def test_reviewer_lenient_drops_bad_suggestion_but_keeps_good_ones():
    stub = {
        "memory_updates": [
            {"summary": "good", "details": {"content": "valid text"}},
            {"summary": "bad-details", "details": "should be an object, not a string"},
        ],
        "recall_candidate": [
            # completely invalid top-level item (not an object) — skipped
            "raw string suggestion",
            {"summary": "good recall", "details": {"content": "valid recall"}},
        ],
        "skill_action": [],
    }
    output, provider_result, skipped = run_reviewer(
        {"reviewer_provider": "dummy", "provider_stub_response": stub}
    )
    # Good ones survive.
    assert len(output.memory_updates) == 1
    assert output.memory_updates[0].summary == "good"
    assert len(output.recall_candidate) == 1
    assert output.recall_candidate[0].summary == "good recall"
    # Skipped list reports both casualties with enough context to log them.
    families = sorted(entry["family"] for entry in skipped)
    assert families == ["memory_updates", "recall_candidate"]
    for entry in skipped:
        assert "reason" in entry and entry["reason"]
        assert "index" in entry



def test_reviewer_retries_on_top_level_parse_failure_then_succeeds(monkeypatch):
    from codex_self_evolution.review import runner as runner_module
    from codex_self_evolution.review.providers import ProviderResult

    call_count = {"value": 0}
    good_payload = '{"memory_updates": [{"summary": "s", "details": {"content": "c"}}], "recall_candidate": [], "skill_action": []}'

    class FlakyProvider:
        name = "flaky"

        def run(self, snapshot, prompt, options):
            call_count["value"] += 1
            if call_count["value"] == 1:
                return ProviderResult(provider=self.name, raw_text="not-json-the-first-time")
            return ProviderResult(provider=self.name, raw_text=good_payload)

    # v0.6.0 runner reaches the provider via build_review_provider_from_config.
    # Patch that entry point so the test injector still wins.
    monkeypatch.setattr(runner_module, "build_review_provider_from_config",
                        lambda name, config: FlakyProvider())
    output, result, skipped = run_reviewer({"reviewer_provider": "flaky"}, parse_retries=2)

    assert call_count["value"] == 2, "should have retried once"
    assert len(output.memory_updates) == 1
    assert skipped == []



def test_reviewer_gives_up_after_exhausting_parse_retries(monkeypatch):
    from codex_self_evolution.review import runner as runner_module
    from codex_self_evolution.review.providers import ProviderResult

    call_count = {"value": 0}

    class AlwaysBad:
        name = "always-bad"

        def run(self, snapshot, prompt, options):
            call_count["value"] += 1
            return ProviderResult(provider=self.name, raw_text="nope nope nope")

    monkeypatch.setattr(runner_module, "build_review_provider_from_config",
                        lambda name, config: AlwaysBad())
    with pytest.raises(SchemaError):
        run_reviewer({"reviewer_provider": "always-bad"}, parse_retries=2)

    # parse_retries=2 → 1 initial + 2 retries = 3 total calls
    assert call_count["value"] == 3



def test_http_provider_requires_api_key_when_env_missing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ReviewProviderError):
        run_reviewer({"reviewer_provider": "openai-compatible"}, provider_options={"api_base": "https://example.com"})



def test_minimax_provider_requires_api_key_when_env_missing(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    with pytest.raises(ReviewProviderError):
        run_reviewer({"reviewer_provider": "minimax"}, provider_options={"api_base": "https://api.minimax.io/anthropic/v1/messages"})

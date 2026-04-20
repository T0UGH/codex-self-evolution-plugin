from __future__ import annotations

from typing import Any

from ..config import PACKAGE_ROOT
from ..schemas import SchemaError
from .providers import (
    ProviderResult,
    ReviewProviderError,
    get_review_provider,
    parse_reviewer_output_lenient,
)


PROMPT_PATH = PACKAGE_ROOT / "review" / "prompt.md"

# How many times to re-call the provider when the response won't parse. We only
# retry on parse failures (SchemaError / empty output) — auth / network
# ReviewProviderError is not retried here since it'll keep failing the same way.
DEFAULT_PARSE_RETRIES = 2


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def run_reviewer(
    review_input: dict[str, Any],
    reviewer_output_override: str | None = None,
    provider_name: str | None = None,
    provider_options: dict[str, Any] | None = None,
    parse_retries: int = DEFAULT_PARSE_RETRIES,
) -> tuple[Any, ProviderResult, list[dict[str, Any]]]:
    """Run the reviewer and return ``(ReviewerOutput, provider_result, skipped)``.

    - Per-item malformed suggestions are dropped (see lenient parser); they are
      reported through ``skipped`` so the caller can log how many items died.
    - On a top-level parse failure we re-call the provider up to
      ``parse_retries`` additional times; this covers the minimax/openai-style
      quirk where the same prompt occasionally returns non-conformant JSON.
    - Auth / transport errors (``ReviewProviderError`` from the HTTP layer)
      are not retried — they are raised immediately.
    """
    prompt = load_prompt()
    options = dict(provider_options or {})
    if reviewer_output_override is not None:
        options["stub_response"] = reviewer_output_override
        provider_name = provider_name or "dummy"
    selected_provider = provider_name or str(review_input.get("reviewer_provider") or "dummy")
    provider = get_review_provider(selected_provider)

    attempts = max(1, parse_retries + 1)
    last_parse_error: SchemaError | ReviewProviderError | None = None
    for attempt in range(attempts):
        result = provider.run(review_input, prompt, options)
        try:
            reviewer_output, skipped = parse_reviewer_output_lenient(result.raw_text)
            return reviewer_output, result, skipped
        except (SchemaError, ReviewProviderError) as exc:
            last_parse_error = exc
            if attempt == attempts - 1:
                break
            continue
    assert last_parse_error is not None  # loop guarantees at least one attempt
    raise last_parse_error

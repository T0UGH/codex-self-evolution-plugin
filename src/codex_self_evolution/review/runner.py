from __future__ import annotations

from typing import Any

from ..config import PACKAGE_ROOT
from .providers import ProviderResult, get_review_provider, parse_reviewer_output


PROMPT_PATH = PACKAGE_ROOT / "review" / "prompt.md"


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def run_reviewer(
    review_input: dict[str, Any],
    reviewer_output_override: str | None = None,
    provider_name: str | None = None,
    provider_options: dict[str, Any] | None = None,
) -> tuple[Any, ProviderResult]:
    prompt = load_prompt()
    options = dict(provider_options or {})
    if reviewer_output_override is not None:
        options["stub_response"] = reviewer_output_override
        provider_name = provider_name or "dummy"
    selected_provider = provider_name or str(review_input.get("reviewer_provider") or "dummy")
    provider = get_review_provider(selected_provider)
    result = provider.run(review_input, prompt, options)
    reviewer_output = parse_reviewer_output(result.raw_text)
    return reviewer_output, result

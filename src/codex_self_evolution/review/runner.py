from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import PACKAGE_ROOT
from ..config_file import LoadResult, PluginConfig, load_config
from ..schemas import SchemaError
from .providers import (
    ProviderResult,
    ReviewProviderError,
    build_review_provider_from_config,
    get_review_provider,
    parse_reviewer_output_lenient,
)


PROMPT_PATH = PACKAGE_ROOT / "review" / "prompt.md"

# How many times to re-call the provider when the response won't parse. We only
# retry on parse failures (SchemaError / empty output) — auth / network
# ReviewProviderError is not retried here since it'll keep failing the same way.
DEFAULT_PARSE_RETRIES = 2


class ReviewerParseFailure(SchemaError):
    """Raised when the reviewer response fails to parse even after retries.

    Carries every attempt's ``raw_text`` so callers can dump them to disk for
    postmortem. Inherits from ``SchemaError`` so existing ``except SchemaError``
    callers still work transparently.
    """

    def __init__(self, message: str, *, raw_texts: list[str], provider_name: str) -> None:
        super().__init__(message)
        self.raw_texts = raw_texts
        self.provider_name = provider_name


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def run_reviewer(
    review_input: dict[str, Any],
    reviewer_output_override: str | None = None,
    provider_name: str | None = None,
    provider_options: dict[str, Any] | None = None,
    parse_retries: int = DEFAULT_PARSE_RETRIES,
    config: PluginConfig | None = None,
    home: Path | None = None,
) -> tuple[Any, ProviderResult, list[dict[str, Any]]]:
    """Run the reviewer and return ``(ReviewerOutput, provider_result, skipped)``.

    Resolution order for provider selection (highest wins):

    1. ``provider_name`` kwarg (explicit override; legacy callers still set this)
    2. ``review_input["reviewer_provider"]`` (legacy — from snapshot payload)
    3. ``config.reviewer.provider`` (new — config.toml + env)
    4. "dummy" as last resort (unit tests / dev scaffolding)

    Similarly, model / base_url / timeout / max_tokens / max_retries /
    retry_backoff are sourced from config.reviewer unless explicit
    ``provider_options`` overrides them.

    Behaviour for existing callers that don't pass ``config`` stays
    backward-compatible: we call :func:`load_config` to pick up whatever the
    user has in ``~/.codex-self-evolution/config.toml`` (or defaults if
    absent). Tests that want full isolation can pass ``config=PluginConfig()``
    directly.
    """
    prompt = load_prompt()
    # Options explicitly passed in still win — they are the most specific caller intent.
    options = dict(provider_options or {})
    if reviewer_output_override is not None:
        options["stub_response"] = reviewer_output_override
        provider_name = provider_name or "dummy"

    # Determine the source of truth for config-driven fields.
    if config is None:
        try:
            config = load_config(home=home).config
        except Exception:  # noqa: BLE001 — config load is a soft fallback
            config = PluginConfig()

    # Provider selection precedence.
    selected_provider = (
        provider_name
        or str(review_input.get("reviewer_provider") or "").strip()
        or config.reviewer.provider
        or "dummy"
    )

    # Pull config-driven defaults into options without overwriting caller
    # values. This is how HTTP provider picks up [reviewer] model/base_url/etc.
    options.setdefault("model", config.reviewer.model or None)
    options.setdefault("timeout_seconds", config.reviewer.timeout_seconds)
    options.setdefault("max_tokens", config.reviewer.max_tokens)
    if config.reviewer.base_url:
        options.setdefault("api_base", config.reviewer.base_url)

    # Drop None entries so downstream `options.get("model") or default` still resolves.
    options = {k: v for k, v in options.items() if v is not None}

    provider = build_review_provider_from_config(selected_provider, config)

    attempts = max(1, parse_retries + 1)
    raw_texts: list[str] = []
    last_parse_error: SchemaError | ReviewProviderError | None = None
    for attempt in range(attempts):
        result = provider.run(review_input, prompt, options)
        raw_texts.append(result.raw_text)
        try:
            reviewer_output, skipped = parse_reviewer_output_lenient(result.raw_text)
            return reviewer_output, result, skipped
        except (SchemaError, ReviewProviderError) as exc:
            last_parse_error = exc
            if attempt == attempts - 1:
                break
            continue
    assert last_parse_error is not None  # loop guarantees at least one attempt
    raise ReviewerParseFailure(
        str(last_parse_error),
        raw_texts=raw_texts,
        provider_name=selected_provider,
    ) from last_parse_error

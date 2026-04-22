from __future__ import annotations

import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from ..schemas import ReviewerOutput, SchemaError

OPENAI_DEFAULT_BASE = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_DEFAULT_BASE = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MINIMAX_GLOBAL_BASE = "https://api.minimax.io"
MINIMAX_CN_BASE = "https://api.minimaxi.com"
MINIMAX_MESSAGES_PATH = "/anthropic/v1/messages"

# HTTP status codes we treat as transient. 408 request timeout, 425 too early,
# 429 rate limited, 500-504 server errors, and 529 which MiniMax (and
# Anthropic-style providers) uses for "overloaded_error". Anything outside
# this set is considered user error (bad key, malformed request) and not
# retried — retrying a 401 won't change anything.
_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504, 529}

# Two extra attempts after the initial one (so at most 3 calls total).
# Backoff is conservative because the reviewer runs in a detached background
# process — adding 2-7s of extra latency is fine, but spending 30s on a
# sleep that might still fail is not.
_MAX_RETRIES = 2
_BACKOFF_SECONDS = (2.0, 5.0)

_logger = logging.getLogger(__name__)


def _is_timeout_error(exc: BaseException) -> bool:
    """Recognize socket-level timeouts across the Python version matrix.

    urllib sometimes wraps the timeout in URLError(reason=TimeoutError), and
    sometimes the raw TimeoutError / socket.timeout propagates. Both need
    the same retry treatment.
    """
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, urllib.error.URLError):
        return isinstance(exc.reason, (TimeoutError, socket.timeout))
    return False


class ReviewProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    raw_text: str
    response_payload: dict[str, Any] | None = None
    request_payload: dict[str, Any] | None = None


class ReviewProvider(Protocol):
    name: str

    def run(self, snapshot: dict[str, Any], prompt: str, options: dict[str, Any]) -> ProviderResult: ...


class DummyReviewProvider:
    name = "dummy"

    def run(self, snapshot: dict[str, Any], prompt: str, options: dict[str, Any]) -> ProviderResult:
        stub = options.get("stub_response")
        if stub is None:
            stub = snapshot.get("provider_stub_response")
        if stub is None:
            stub = {"memory_updates": [], "recall_candidate": [], "skill_action": []}
        raw_text = stub if isinstance(stub, str) else json.dumps(stub)
        return ProviderResult(provider=self.name, raw_text=raw_text, response_payload={"stub": True})


class HTTPReviewProvider:
    def __init__(self, name: str, dialect: str) -> None:
        self.name = name
        self.dialect = dialect

    def build_request_payload(self, snapshot: dict[str, Any], prompt: str, options: dict[str, Any]) -> dict[str, Any]:
        model = str(options.get("model") or self.default_model())
        # This is the *output* budget, not the 200k context window. Originally
        # 800, which got truncated mid-JSON in practice (unterminated strings
        # around char 2100). Set to 4096 — well within every supported model's
        # 8k output ceiling (Haiku/Sonnet/MiniMax-M2.7 all cap at 8192) and
        # leaves room for 10+ suggestions without pressure. Reviewer runs in
        # a background subprocess so extra latency doesn't block Codex.
        max_tokens = int(options.get("max_tokens", 4096))
        if self.dialect == "openai":
            return {
                "model": model,
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(snapshot, indent=2, sort_keys=True)},
                ],
                "temperature": 0,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
            }
        if self.dialect in {"anthropic", "minimax"}:
            return {
                "model": model,
                "system": prompt,
                "messages": [{"role": "user", "content": json.dumps(snapshot, indent=2, sort_keys=True)}],
                "temperature": 0,
                "max_tokens": max_tokens,
            }
        raise ReviewProviderError(f"unsupported reviewer dialect: {self.dialect}")

    def default_api_base(self) -> str:
        if self.dialect == "openai":
            return str(os.getenv("OPENAI_BASE_URL") or OPENAI_DEFAULT_BASE)
        if self.dialect == "anthropic":
            return str(os.getenv("ANTHROPIC_BASE_URL") or ANTHROPIC_DEFAULT_BASE)
        if self.dialect == "minimax":
            explicit = os.getenv("MINIMAX_BASE_URL")
            if explicit:
                return explicit.rstrip("/")
            region = str(os.getenv("MINIMAX_REGION") or "global").lower()
            host = MINIMAX_CN_BASE if region == "cn" else MINIMAX_GLOBAL_BASE
            return f"{host}{MINIMAX_MESSAGES_PATH}"
        raise ReviewProviderError(f"unsupported reviewer dialect: {self.dialect}")

    def default_model(self) -> str:
        if self.dialect == "openai":
            return str(os.getenv("OPENAI_REVIEW_MODEL") or "gpt-4.1-mini")
        if self.dialect == "anthropic":
            return str(os.getenv("ANTHROPIC_REVIEW_MODEL") or "claude-3-5-haiku-latest")
        if self.dialect == "minimax":
            return str(os.getenv("MINIMAX_REVIEW_MODEL") or "MiniMax-M2.7")
        raise ReviewProviderError(f"unsupported reviewer dialect: {self.dialect}")

    def resolve_api_key(self, options: dict[str, Any]) -> str:
        explicit = options.get("api_key")
        if explicit:
            return str(explicit)
        if self.dialect == "openai":
            env_var = "OPENAI_API_KEY"
        elif self.dialect == "anthropic":
            env_var = "ANTHROPIC_API_KEY"
        elif self.dialect == "minimax":
            env_var = "MINIMAX_API_KEY"
        else:
            raise ReviewProviderError(f"unsupported reviewer dialect: {self.dialect}")
        value = os.getenv(env_var)
        if value:
            return str(value)
        raise ReviewProviderError(f"{self.name} provider requires api_key or {env_var}")

    def build_headers(self, options: dict[str, Any]) -> dict[str, str]:
        if self.dialect == "openai":
            return {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.resolve_api_key(options)}",
            }
        if self.dialect == "anthropic":
            return {
                "Content-Type": "application/json",
                "x-api-key": self.resolve_api_key(options),
                "anthropic-version": str(options.get("anthropic_version") or ANTHROPIC_VERSION),
            }
        if self.dialect == "minimax":
            return {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.resolve_api_key(options)}",
            }
        raise ReviewProviderError(f"unsupported reviewer dialect: {self.dialect}")

    def run(self, snapshot: dict[str, Any], prompt: str, options: dict[str, Any]) -> ProviderResult:
        api_base = str(options.get("api_base") or self.default_api_base())
        payload = self.build_request_payload(snapshot, prompt, options)
        headers = self.build_headers(options)
        request_body = json.dumps(payload).encode("utf-8")
        timeout = float(options.get("timeout_seconds", 30))

        body = self._execute_with_retries(api_base, request_body, headers, timeout)
        parsed = json.loads(body)
        raw_text = self._extract_text(parsed)
        return ProviderResult(provider=self.name, raw_text=raw_text, response_payload=parsed, request_payload=payload)

    def _execute_with_retries(
        self,
        api_base: str,
        request_body: bytes,
        headers: dict[str, str],
        timeout: float,
    ) -> str:
        """POST to ``api_base`` with exponential-backoff retry on transient errors.

        Only 429 / 5xx / 529 / socket timeouts are retried; 4xx client errors
        and auth failures raise immediately since retrying them just wastes
        a real-time window and amplifies the user's eventual backlog of
        "this one turn's memory signal was dropped" complaints.

        Each retry rebuilds the :class:`Request` object — the previous one
        already had its body buffer consumed on failure in some cases, and
        rebuilding is essentially free compared to the HTTP round-trip.
        """
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            request = urllib.request.Request(
                api_base,
                data=request_body,
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return response.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                err_body = exc.read().decode("utf-8", errors="replace")
                if exc.code in _RETRYABLE_STATUS_CODES and attempt < _MAX_RETRIES:
                    _logger.info(
                        "%s retrying after HTTP %s (attempt %d/%d)",
                        self.name, exc.code, attempt + 1, _MAX_RETRIES + 1,
                    )
                    time.sleep(_BACKOFF_SECONDS[attempt])
                    last_exc = exc
                    continue
                raise ReviewProviderError(
                    f"{self.name} request failed: HTTP {exc.code}: {err_body[:500]}"
                ) from exc
            except urllib.error.URLError as exc:
                if _is_timeout_error(exc) and attempt < _MAX_RETRIES:
                    _logger.info(
                        "%s retrying after timeout (attempt %d/%d)",
                        self.name, attempt + 1, _MAX_RETRIES + 1,
                    )
                    time.sleep(_BACKOFF_SECONDS[attempt])
                    last_exc = exc
                    continue
                raise ReviewProviderError(f"{self.name} request failed: {exc}") from exc
            except (TimeoutError, socket.timeout) as exc:
                if attempt < _MAX_RETRIES:
                    _logger.info(
                        "%s retrying after raw timeout (attempt %d/%d)",
                        self.name, attempt + 1, _MAX_RETRIES + 1,
                    )
                    time.sleep(_BACKOFF_SECONDS[attempt])
                    last_exc = exc
                    continue
                raise ReviewProviderError(f"{self.name} request failed: {exc}") from exc
        # Loop only exits via return or raise; this line is unreachable, but
        # defensive in case the logic above is ever refactored incorrectly.
        assert last_exc is not None  # pragma: no cover
        raise ReviewProviderError(f"{self.name} request failed after retries: {last_exc}") from last_exc

    def _extract_text(self, payload: dict[str, Any]) -> str:
        if self.dialect == "openai":
            try:
                return str(payload["choices"][0]["message"]["content"])
            except Exception as exc:  # pragma: no cover - defensive
                raise ReviewProviderError("openai-compatible response missing choices[0].message.content") from exc
        if self.dialect in {"anthropic", "minimax"}:
            try:
                blocks = payload["content"]
                if isinstance(blocks, list):
                    return "\n".join(str(block.get("text", "")) for block in blocks if isinstance(block, dict)).strip()
            except Exception as exc:  # pragma: no cover - defensive
                raise ReviewProviderError(f"{self.name} response missing content blocks") from exc
        raise ReviewProviderError(f"unsupported reviewer dialect: {self.dialect}")


def get_review_provider(name: str) -> ReviewProvider:
    if name == "dummy":
        return DummyReviewProvider()
    if name == "openai-compatible":
        return HTTPReviewProvider(name=name, dialect="openai")
    if name == "anthropic-style":
        return HTTPReviewProvider(name=name, dialect="anthropic")
    if name == "minimax":
        return HTTPReviewProvider(name=name, dialect="minimax")
    raise ReviewProviderError(f"unknown review provider: {name}")


def _normalize_json_text(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def parse_reviewer_output(raw_text: str, max_chars: int = 100_000) -> ReviewerOutput:
    """Strict parse: raises on any structural problem, including per-item errors."""
    parsed = _load_reviewer_json(raw_text, max_chars=max_chars)
    return ReviewerOutput.from_dict(parsed)


def parse_reviewer_output_lenient(
    raw_text: str, max_chars: int = 100_000
) -> tuple[ReviewerOutput, list[dict[str, Any]]]:
    """Lenient parse: top-level structural errors still raise, but malformed
    individual suggestions are silently skipped and reported as ``skipped``.
    """
    parsed = _load_reviewer_json(raw_text, max_chars=max_chars)
    return ReviewerOutput.from_dict_lenient(parsed)


def _load_reviewer_json(raw_text: str, max_chars: int) -> dict[str, Any]:
    if not raw_text.strip():
        raise ReviewProviderError("reviewer returned empty output")
    if len(raw_text) > max_chars:
        raise ReviewProviderError("reviewer output exceeded max_chars")
    normalized = _normalize_json_text(raw_text)
    try:
        return json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise SchemaError(f"reviewer did not return valid JSON: {exc}") from exc

from __future__ import annotations

import json
import os
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
        request = urllib.request.Request(
            api_base,
            data=json.dumps(payload).encode("utf-8"),
            headers=self.build_headers(options),
            method="POST",
        )
        timeout = float(options.get("timeout_seconds", 30))
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ReviewProviderError(f"{self.name} request failed: HTTP {exc.code}: {body[:500]}") from exc
        except urllib.error.URLError as exc:
            raise ReviewProviderError(f"{self.name} request failed: {exc}") from exc
        parsed = json.loads(body)
        raw_text = self._extract_text(parsed)
        return ProviderResult(provider=self.name, raw_text=raw_text, response_payload=parsed, request_payload=payload)

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

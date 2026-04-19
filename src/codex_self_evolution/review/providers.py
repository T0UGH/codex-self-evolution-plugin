from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from ..schemas import ReviewerOutput, SchemaError


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
        model = str(options.get("model") or "reviewer-model")
        max_tokens = int(options.get("max_tokens", 800))
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
        if self.dialect == "anthropic":
            return {
                "model": model,
                "system": prompt,
                "messages": [{"role": "user", "content": json.dumps(snapshot, indent=2, sort_keys=True)}],
                "temperature": 0,
                "max_tokens": max_tokens,
            }
        raise ReviewProviderError(f"unsupported reviewer dialect: {self.dialect}")

    def run(self, snapshot: dict[str, Any], prompt: str, options: dict[str, Any]) -> ProviderResult:
        api_base = options.get("api_base")
        if not api_base:
            raise ReviewProviderError(f"{self.name} provider requires api_base")
        payload = self.build_request_payload(snapshot, prompt, options)
        request = urllib.request.Request(
            str(api_base),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {options['api_key']}"} if options.get("api_key") else {}),
            },
            method="POST",
        )
        timeout = float(options.get("timeout_seconds", 30))
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
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
        if self.dialect == "anthropic":
            try:
                blocks = payload["content"]
                if isinstance(blocks, list):
                    return "\n".join(str(block.get("text", "")) for block in blocks if isinstance(block, dict)).strip()
            except Exception as exc:  # pragma: no cover - defensive
                raise ReviewProviderError("anthropic-style response missing content blocks") from exc
        raise ReviewProviderError(f"unsupported reviewer dialect: {self.dialect}")


def get_review_provider(name: str) -> ReviewProvider:
    if name == "dummy":
        return DummyReviewProvider()
    if name == "openai-compatible":
        return HTTPReviewProvider(name=name, dialect="openai")
    if name == "anthropic-style":
        return HTTPReviewProvider(name=name, dialect="anthropic")
    raise ReviewProviderError(f"unknown review provider: {name}")


def parse_reviewer_output(raw_text: str, max_chars: int = 100_000) -> ReviewerOutput:
    if not raw_text.strip():
        raise ReviewProviderError("reviewer returned empty output")
    if len(raw_text) > max_chars:
        raise ReviewProviderError("reviewer output exceeded max_chars")
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SchemaError(f"reviewer did not return valid JSON: {exc}") from exc
    return ReviewerOutput.from_dict(parsed)

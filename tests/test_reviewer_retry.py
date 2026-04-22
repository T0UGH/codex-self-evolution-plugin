"""Transient-error retry for the HTTP reviewer providers.

Why this exists: MiniMax hands out plenty of ``HTTP 529 overloaded_error``
and occasional 30s socket timeouts in practice (verified in plugin.log on
2026-04-22). Without retries every one of those drops a turn's memory
signal on the floor. These tests pin the backoff policy: 2 extra attempts
after the initial, only retryable-status or socket-timeout errors retry,
and non-retryable 4xx errors raise immediately without burning the budget.
"""
from __future__ import annotations

import io
import socket
import urllib.error
from typing import Any

import pytest

from codex_self_evolution.review import providers
from codex_self_evolution.review.providers import (
    HTTPReviewProvider,
    ReviewProviderError,
    _is_timeout_error,
)


class _Response:
    """Minimal stand-in for ``http.client.HTTPResponse`` context manager."""

    def __init__(self, body: str) -> None:
        self._body = body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._body.encode("utf-8")


def _make_http_error(status: int, body: str = "{}") -> urllib.error.HTTPError:
    # HTTPError needs a file-like for .read(); body must be fresh per raise
    # because urllib consumes the buffer.
    return urllib.error.HTTPError(
        url="https://example.invalid",
        code=status,
        msg="err",
        hdrs={},  # type: ignore[arg-type]
        fp=io.BytesIO(body.encode("utf-8")),
    )


@pytest.fixture
def minimax_provider() -> HTTPReviewProvider:
    return HTTPReviewProvider(name="minimax", dialect="minimax")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make retry backoff instantaneous so tests don't block on 2+5s sleeps."""
    monkeypatch.setattr(providers.time, "sleep", lambda _: None)


def _minimax_happy_body() -> str:
    # Shape of a successful anthropic-style response; HTTPReviewProvider parses
    # this via _extract_text and returns raw_text to callers.
    return (
        '{"content":[{"text":"'
        '{\\"memory_updates\\":[],\\"recall_candidate\\":[],\\"skill_action\\":[]}'
        '"}]}'
    )


def _stub_urlopen_sequence(monkeypatch: pytest.MonkeyPatch, responses: list):
    """Hand urlopen the next entry from ``responses`` each call.

    An entry that's an Exception is raised; anything else is returned.
    Tracks the number of calls via the closure's ``calls`` list so tests
    can assert "we retried N times and no more".
    """
    calls = {"count": 0}

    def fake_urlopen(_request, timeout):  # noqa: ARG001
        idx = calls["count"]
        calls["count"] += 1
        if idx >= len(responses):
            raise AssertionError(f"urlopen called {idx + 1} times, expected <= {len(responses)}")
        item = responses[idx]
        if isinstance(item, Exception):
            raise item
        return _Response(item)

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_retry_succeeds_after_one_http_529(
    monkeypatch: pytest.MonkeyPatch,
    minimax_provider: HTTPReviewProvider,
) -> None:
    """The actual observed failure mode: MiniMax 529 on first try, 200 on second."""
    calls = _stub_urlopen_sequence(
        monkeypatch,
        [_make_http_error(529, '{"error":"overloaded"}'), _minimax_happy_body()],
    )
    result = minimax_provider.run(
        snapshot={},
        prompt="p",
        options={"api_key": "sk-test", "api_base": "https://example.invalid/api"},
    )
    assert calls["count"] == 2
    assert "memory_updates" in result.raw_text


def test_retry_succeeds_after_one_timeout(
    monkeypatch: pytest.MonkeyPatch,
    minimax_provider: HTTPReviewProvider,
) -> None:
    """Socket timeout from SSL read should be retried too (second observed failure)."""
    timeout = socket.timeout("The read operation timed out")
    calls = _stub_urlopen_sequence(monkeypatch, [timeout, _minimax_happy_body()])
    result = minimax_provider.run(
        snapshot={},
        prompt="p",
        options={"api_key": "sk-test", "api_base": "https://example.invalid/api"},
    )
    assert calls["count"] == 2
    assert "memory_updates" in result.raw_text


def test_timeout_wrapped_in_urlerror_also_retries(
    monkeypatch: pytest.MonkeyPatch,
    minimax_provider: HTTPReviewProvider,
) -> None:
    """Some Python/urllib combos wrap the timeout in URLError instead. Same handling."""
    wrapped = urllib.error.URLError(reason=TimeoutError("timed out"))
    calls = _stub_urlopen_sequence(monkeypatch, [wrapped, _minimax_happy_body()])
    result = minimax_provider.run(
        snapshot={},
        prompt="p",
        options={"api_key": "sk-test", "api_base": "https://example.invalid/api"},
    )
    assert calls["count"] == 2
    assert "memory_updates" in result.raw_text


def test_non_retryable_401_fails_immediately(
    monkeypatch: pytest.MonkeyPatch,
    minimax_provider: HTTPReviewProvider,
) -> None:
    """Auth failure must NOT retry — we'd just burn the backoff budget to no effect.

    This is the exact failure we saw from opencode's error event on launchd:
    retrying a missing-auth 401 would have hidden the real cause for 7 more seconds
    without fixing anything."""
    calls = _stub_urlopen_sequence(
        monkeypatch,
        [_make_http_error(401, '{"error":"unauthorized"}')],
    )
    with pytest.raises(ReviewProviderError) as exc:
        minimax_provider.run(
            snapshot={},
            prompt="p",
            options={"api_key": "sk-test", "api_base": "https://example.invalid/api"},
        )
    assert calls["count"] == 1
    assert "HTTP 401" in str(exc.value)


def test_gives_up_after_max_retries_on_persistent_529(
    monkeypatch: pytest.MonkeyPatch,
    minimax_provider: HTTPReviewProvider,
) -> None:
    """If MiniMax stays overloaded for all 3 attempts, surface the last error.

    Bounds the wasted time — don't let a transient-but-persistent backend
    incident loop forever.
    """
    calls = _stub_urlopen_sequence(
        monkeypatch,
        [
            _make_http_error(529, '{"error":"still overloaded"}'),
            _make_http_error(529, '{"error":"still overloaded"}'),
            _make_http_error(529, '{"error":"still overloaded"}'),
        ],
    )
    with pytest.raises(ReviewProviderError) as exc:
        minimax_provider.run(
            snapshot={},
            prompt="p",
            options={"api_key": "sk-test", "api_base": "https://example.invalid/api"},
        )
    # Initial attempt + 2 retries = 3 total calls, not 4.
    assert calls["count"] == 3
    assert "HTTP 529" in str(exc.value)


def test_is_timeout_error_recognizes_all_shapes() -> None:
    """Sanity check for the helper that classifies "is this a timeout?"."""
    assert _is_timeout_error(socket.timeout())
    assert _is_timeout_error(TimeoutError())
    assert _is_timeout_error(urllib.error.URLError(reason=TimeoutError("x")))
    assert not _is_timeout_error(urllib.error.URLError(reason=OSError("refused")))
    assert not _is_timeout_error(ValueError("other"))

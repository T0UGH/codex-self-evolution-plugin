from __future__ import annotations

import json
from typing import Any

import pytest

from codex_self_evolution.session_reflection.app_server import (
    AppServerError,
    ReflectionAppServerClient,
    StdioAppServerTransport,
)


class FakeTransport:
    """Test transport that records requests and returns queued results."""

    def __init__(self, results: list[dict[str, Any]]) -> None:
        """Store deterministic results for subsequent JSON-RPC requests."""
        self.results = results
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, params: dict[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
        """Record one app-server request and return the next fake result."""
        self.calls.append({"method": method, "params": params, "timeout_seconds": timeout_seconds})
        return self.results.pop(0)


def test_fork_thread_sends_required_params_and_returns_child_id() -> None:
    """thread/fork uses the reflection source and returns response ids."""
    transport = FakeTransport([{"threadId": "child-1", "extra": "raw"}])
    client = ReflectionAppServerClient(transport=transport, timeout_seconds=12)

    child_thread_id, raw = client.fork_thread(
        parent_thread_id="parent-1",
        transcript_path="/tmp/transcript.jsonl",
        model="gpt-5.3-codex-spark",
        cwd="/repo",
        ephemeral=True,
        sandbox="danger-full-access",
        approval_policy="never",
    )

    assert child_thread_id == "child-1"
    assert raw == {"threadId": "child-1", "extra": "raw"}
    assert transport.calls == [
        {
            "method": "thread/fork",
            "params": {
                "threadId": "parent-1",
                "path": "/tmp/transcript.jsonl",
                "model": "gpt-5.3-codex-spark",
                "cwd": "/repo",
                "threadSource": "memory_consolidation",
                "ephemeral": True,
                "sandbox": "danger-full-access",
                "approvalPolicy": "never",
            },
            "timeout_seconds": 12,
        }
    ]


def test_fork_thread_omits_empty_transcript_path() -> None:
    """thread/fork does not send a blank path field."""
    transport = FakeTransport([{"id": "child-1"}])
    client = ReflectionAppServerClient(transport=transport)

    client.fork_thread(
        parent_thread_id="parent-1",
        transcript_path="",
        model="model",
        cwd="/repo",
        ephemeral=False,
        sandbox="workspace-write",
        approval_policy="on-request",
    )

    assert "path" not in transport.calls[0]["params"]


def test_start_reflection_turn_sends_required_params_and_returns_turn_id() -> None:
    """turn/start sends prompt as a text input and sandboxPolicy mode."""
    transport = FakeTransport([{"turnId": "turn-1"}])
    client = ReflectionAppServerClient(transport=transport)

    turn_id, raw = client.start_reflection_turn(
        child_thread_id="child-1",
        cwd="/repo",
        model="gpt-5.3-codex-spark",
        approval_policy="never",
        sandbox="danger-full-access",
        prompt="reflect this",
    )

    assert turn_id == "turn-1"
    assert raw == {"turnId": "turn-1"}
    assert transport.calls == [
        {
            "method": "turn/start",
            "params": {
                "threadId": "child-1",
                "cwd": "/repo",
                "model": "gpt-5.3-codex-spark",
                "approvalPolicy": "never",
                "sandboxPolicy": {"mode": "danger-full-access"},
                "input": [{"type": "text", "text": "reflect this"}],
            },
            "timeout_seconds": 900.0,
        }
    ]


def test_client_raises_when_response_id_is_missing() -> None:
    """Missing ids at the client boundary fail loudly."""
    client = ReflectionAppServerClient(transport=FakeTransport([{"ok": True}]))

    with pytest.raises(AppServerError, match="missing child thread id"):
        client.fork_thread(
            parent_thread_id="parent-1",
            transcript_path="",
            model="model",
            cwd="/repo",
            ephemeral=True,
            sandbox="danger-full-access",
            approval_policy="never",
        )


def test_stdio_transport_raises_on_error_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real transport converts JSON-RPC error responses into AppServerError."""

    class RequestId:
        """Deterministic uuid fixture."""

        hex = "1"

    class Completed:
        """Minimal subprocess result fixture."""

        returncode = 0
        stdout = json.dumps({"jsonrpc": "2.0", "id": "1", "error": {"message": "bad"}})
        stderr = ""

    def fake_run(*args: object, **kwargs: object) -> Completed:
        """Return a successful process containing a JSON-RPC error."""
        return Completed()

    monkeypatch.setattr("uuid.uuid4", lambda: RequestId())
    monkeypatch.setattr("subprocess.run", fake_run)

    with pytest.raises(AppServerError, match="app-server error response"):
        StdioAppServerTransport().request("thread/fork", {}, timeout_seconds=1)


def test_stdio_transport_ignores_notifications_and_wrong_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real transport returns the result matching its generated request id."""

    class RequestId:
        """Deterministic uuid fixture."""

        hex = "request-1"

    class Completed:
        """Subprocess result with unrelated JSON-RPC messages first."""

        returncode = 0
        stdout = "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "method": "notice", "params": {}}),
                json.dumps({"jsonrpc": "2.0", "id": "wrong", "result": {"id": "wrong-child"}}),
                json.dumps({"jsonrpc": "2.0", "id": "request-1", "result": {"id": "child-1"}}),
            ]
        )
        stderr = ""

    def fake_run(*args: object, **kwargs: object) -> Completed:
        """Return mixed JSON-RPC output."""
        return Completed()

    monkeypatch.setattr("uuid.uuid4", lambda: RequestId())
    monkeypatch.setattr("subprocess.run", fake_run)

    result = StdioAppServerTransport().request("thread/fork", {}, timeout_seconds=1)

    assert result == {"id": "child-1"}


def test_stdio_transport_fails_when_no_matching_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real transport fails when stdout has JSON but no matching request id."""

    class RequestId:
        """Deterministic uuid fixture."""

        hex = "request-1"

    class Completed:
        """Subprocess result containing only unrelated messages."""

        returncode = 0
        stdout = json.dumps({"jsonrpc": "2.0", "id": "wrong", "result": {"id": "child-1"}})
        stderr = ""

    def fake_run(*args: object, **kwargs: object) -> Completed:
        """Return a wrong-id response."""
        return Completed()

    monkeypatch.setattr("uuid.uuid4", lambda: RequestId())
    monkeypatch.setattr("subprocess.run", fake_run)

    with pytest.raises(AppServerError, match="missing matching id request-1"):
        StdioAppServerTransport().request("thread/fork", {}, timeout_seconds=1)

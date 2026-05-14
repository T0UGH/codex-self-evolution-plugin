from __future__ import annotations

import json
import subprocess
import uuid
from typing import Any, Protocol


class AppServerError(RuntimeError):
    """Raised when the Codex app-server JSON-RPC boundary fails."""


class AppServerTransport(Protocol):
    """Transport boundary used by tests and the real app-server proxy."""

    def request(self, method: str, params: dict[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
        """Send one JSON-RPC request and return the result object."""
        ...


class StdioAppServerTransport:
    """JSON-RPC transport backed by `codex app-server proxy` over stdio."""

    def request(self, method: str, params: dict[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
        """Send one request through a short-lived app-server proxy process."""
        request_id = uuid.uuid4().hex
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        try:
            completed = subprocess.run(
                ["codex", "app-server", "proxy"],
                input=json.dumps(payload) + "\n",
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AppServerError(f"codex app-server proxy timed out after {timeout_seconds:g}s") from exc
        except OSError as exc:
            raise AppServerError(f"failed to execute codex app-server proxy: {exc}") from exc

        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            stdout = completed.stdout.strip()
            detail = stderr or stdout or "no output"
            raise AppServerError(f"codex app-server proxy exited {completed.returncode}: {detail}")
        return _result_from_stdout(completed.stdout, expected_id=request_id)


class ReflectionAppServerClient:
    """Typed client for the reflection worker app-server methods."""

    def __init__(self, transport: AppServerTransport | None = None, *, timeout_seconds: float = 900.0) -> None:
        """Create a client with an injectable transport and timeout."""
        self._transport = transport if transport is not None else StdioAppServerTransport()
        self._timeout_seconds = timeout_seconds

    def fork_thread(
        self,
        *,
        parent_thread_id: str,
        transcript_path: str,
        model: str,
        cwd: str,
        ephemeral: bool,
        sandbox: str,
        approval_policy: str,
    ) -> tuple[str, dict[str, Any]]:
        """Fork a memory-consolidation child thread and return its id."""
        params: dict[str, Any] = {
            "threadId": parent_thread_id,
            "model": model,
            "cwd": cwd,
            "threadSource": "memory_consolidation",
            "ephemeral": ephemeral,
            "sandbox": sandbox,
            "approvalPolicy": approval_policy,
        }
        if transcript_path:
            params["path"] = transcript_path
        raw = self._transport.request("thread/fork", params, timeout_seconds=self._timeout_seconds)
        child_thread_id = _first_text_path(
            raw,
            ("thread", "id"),
            ("id",),
            ("threadId",),
            ("sessionId",),
            ("thread", "sessionId"),
        )
        if not child_thread_id:
            raise AppServerError("thread/fork response missing child thread id")
        return child_thread_id, raw

    def start_reflection_turn(
        self,
        *,
        child_thread_id: str,
        cwd: str,
        model: str,
        approval_policy: str,
        sandbox: str,
        prompt: str,
    ) -> tuple[str, dict[str, Any]]:
        """Start the reflection prompt turn in an existing child thread."""
        params = {
            "threadId": child_thread_id,
            "cwd": cwd,
            "model": model,
            "approvalPolicy": approval_policy,
            "sandboxPolicy": {"mode": sandbox},
            "input": [{"type": "text", "text": prompt}],
        }
        raw = self._transport.request("turn/start", params, timeout_seconds=self._timeout_seconds)
        turn_id = _first_text_path(raw, ("turn", "id"), ("id",), ("turnId",), ("turn_id",))
        if not turn_id:
            raise AppServerError("turn/start response missing turn id")
        return turn_id, raw


def _result_from_stdout(stdout: str, *, expected_id: str) -> dict[str, Any]:
    """Parse proxy stdout and return the matching JSON-RPC result object."""
    response = _matching_json_response(stdout, expected_id=expected_id)
    if "error" in response and response["error"]:
        raise AppServerError(f"app-server error response: {response['error']}")
    if "result" not in response:
        raise AppServerError("app-server response missing result")
    result = response["result"]
    if not isinstance(result, dict):
        raise AppServerError("app-server result is not an object")
    return result


def _matching_json_response(stdout: str, *, expected_id: str) -> dict[str, Any]:
    """Return the first JSON-RPC response with the expected request id."""
    text = stdout.strip()
    if not text:
        raise AppServerError("app-server response was empty")
    saw_json = False
    for candidate in _json_candidates(text):
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if not isinstance(parsed, dict):
            continue
        saw_json = True
        if parsed.get("id") == expected_id:
            return parsed
    if not saw_json:
        raise AppServerError("app-server response was not valid JSON")
    raise AppServerError(f"app-server response missing matching id {expected_id}")


def _json_candidates(text: str) -> list[str]:
    """Return whole-output and line-delimited JSON candidates in output order."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) <= 1:
        return [text]
    return lines + [text]


def _first_text_path(payload: dict[str, Any], *paths: tuple[str, ...]) -> str:
    """Return the first non-empty string-like value from nested response paths."""
    for path in paths:
        value: Any = payload
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if value is not None and str(value):
            return str(value)
    return ""

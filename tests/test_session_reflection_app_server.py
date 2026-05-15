from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import tempfile
import threading
from pathlib import Path
from typing import Any

import pytest

from codex_self_evolution.session_reflection import app_server as app_server_module
from codex_self_evolution.session_reflection.app_server import (
    AppServerError,
    ReflectionAppServerClient,
    StdioAppServerTransport,
    UnixWebSocketAppServerTransport,
    app_server_proxy_status,
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


def test_fork_thread_accepts_schema_thread_response() -> None:
    """thread/fork accepts the real app-server v2 ThreadForkResponse shape."""
    transport = FakeTransport([{"thread": {"id": "child-1", "sessionId": "session-1"}}])
    client = ReflectionAppServerClient(transport=transport)

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
    assert raw == {"thread": {"id": "child-1", "sessionId": "session-1"}}


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
                "sandboxPolicy": {"type": "dangerFullAccess"},
                "input": [{"type": "text", "text": "reflect this"}],
            },
            "timeout_seconds": 900.0,
        }
    ]


def test_start_reflection_turn_accepts_schema_turn_response() -> None:
    """turn/start accepts the real app-server v2 TurnStartResponse shape."""
    transport = FakeTransport([{"turn": {"id": "turn-1", "status": "running"}}])
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
    assert raw == {"turn": {"id": "turn-1", "status": "running"}}


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


def test_unix_websocket_transport_initializes_and_returns_result() -> None:
    """Unix WebSocket transport performs app-server initialization before requests."""
    with tempfile.TemporaryDirectory(prefix="csep-ws-", dir="/tmp") as root:
        server = _FakeUnixWebSocketAppServer(Path(root) / "app.sock")
        server.start()
        server.wait_ready()
        transport = UnixWebSocketAppServerTransport(
            socket_path=server.socket_path,
            client_name="csep-test",
            client_version="0.0.0",
        )
        try:
            result = transport.request("thread/list", {}, timeout_seconds=2)
        finally:
            transport.close()
            server.join_and_raise()

    assert result == {"data": []}
    methods = [message["method"] for message in server.messages]
    assert methods == ["initialize", "initialized", "thread/list"]
    assert "jsonrpc" not in server.messages[0]
    assert server.messages[0]["params"]["capabilities"]["experimentalApi"] is True


def test_app_server_proxy_status_reports_missing_control_socket(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Proxy status reports why the default app-server control socket is unusable."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setattr(app_server_module.shutil, "which", lambda name: "/usr/bin/codex" if name == "codex" else None)

    status = app_server_proxy_status()

    assert status == {
        "available": True,
        "mode": "managed_app_server",
        "reason": "control_socket_missing",
        "socket_path": str(tmp_path / "codex-home" / "app-server-control" / "app-server-control.sock"),
    }


def test_app_server_proxy_status_reports_missing_codex_binary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Proxy status fails loudly when neither a socket nor Codex binary exists."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setattr(app_server_module.shutil, "which", lambda name: None)

    status = app_server_proxy_status()

    assert status == {
        "available": False,
        "mode": None,
        "reason": "codex_binary_missing",
        "socket_path": str(tmp_path / "codex-home" / "app-server-control" / "app-server-control.sock"),
    }


def test_app_server_proxy_status_accepts_existing_control_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proxy status treats an existing Unix socket as app-server proxy ready."""
    with tempfile.TemporaryDirectory(prefix="csep-codex-", dir="/tmp") as codex_home_raw:
        codex_home = Path(codex_home_raw)
        monkeypatch.setenv("CODEX_HOME", str(codex_home))
        socket_path = codex_home / "app-server-control" / "app-server-control.sock"
        socket_path.parent.mkdir(parents=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(socket_path))

            status = app_server_proxy_status()
        finally:
            server.close()

    assert status == {
        "available": True,
        "mode": "control_socket",
        "reason": None,
        "socket_path": str(socket_path),
    }


class _FakeUnixWebSocketAppServer:
    """Tiny Unix WebSocket server for app-server transport tests."""

    def __init__(self, socket_path: Path) -> None:
        """Prepare a fake server bound to one Unix socket path."""
        self.socket_path = socket_path
        self.messages: list[dict[str, Any]] = []
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        """Start the server thread."""
        self._thread.start()

    def wait_ready(self) -> None:
        """Wait until the socket is listening."""
        assert self._ready.wait(timeout=2)

    def join_and_raise(self) -> None:
        """Wait for the server and re-raise background failures."""
        self._thread.join(timeout=2)
        assert not self._thread.is_alive()
        if self._error is not None:
            raise self._error

    def _run(self) -> None:
        """Serve one initialized connection and one thread/list request."""
        try:
            self.socket_path.parent.mkdir(parents=True, exist_ok=True)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(self.socket_path))
                server.listen(1)
                self._ready.set()
                conn, _ = server.accept()
                with conn:
                    _accept_websocket(conn)
                    while len(self.messages) < 3:
                        message = _recv_websocket_text(conn)
                        parsed = json.loads(message)
                        self.messages.append(parsed)
                        method = parsed["method"]
                        if method == "initialize":
                            _send_websocket_text(
                                conn,
                                {
                                    "id": parsed["id"],
                                    "result": {
                                        "userAgent": "fake",
                                        "codexHome": "/tmp/codex",
                                        "platformFamily": "unix",
                                        "platformOs": "macos",
                                    },
                                },
                            )
                        elif method == "thread/list":
                            _send_websocket_text(
                                conn,
                                {
                                    "method": "thread/status/changed",
                                    "params": {"ignored": True},
                                },
                            )
                            _send_websocket_text(conn, {"id": parsed["id"], "result": {"data": []}})
        except BaseException as exc:  # noqa: BLE001 - surfaced in the test thread.
            self._error = exc
            self._ready.set()


def _accept_websocket(conn: socket.socket) -> None:
    """Read a WebSocket upgrade request and send a successful handshake."""
    request = b""
    while b"\r\n\r\n" not in request:
        chunk = conn.recv(4096)
        if not chunk:
            raise AssertionError("websocket handshake closed early")
        request += chunk
    headers = request.decode("utf-8").split("\r\n")
    key = ""
    for header in headers:
        if header.lower().startswith("sec-websocket-key:"):
            key = header.split(":", 1)[1].strip()
            break
    if not key:
        raise AssertionError("missing websocket key")
    accept = base64.b64encode(hashlib.sha1((key + _WEBSOCKET_GUID).encode("ascii")).digest()).decode("ascii")
    conn.sendall(
        (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "connection: Upgrade\r\n"
            "upgrade: websocket\r\n"
            f"sec-websocket-accept: {accept}\r\n"
            "\r\n"
        ).encode("ascii")
    )


def _recv_websocket_text(conn: socket.socket) -> str:
    """Receive one client text frame."""
    first = _recv_exact(conn, 2)
    first_byte, second_byte = first
    opcode = first_byte & 0x0F
    if opcode != 1:
        raise AssertionError(f"expected text frame, got opcode {opcode}")
    masked = bool(second_byte & 0x80)
    size = second_byte & 0x7F
    if size == 126:
        size = struct.unpack("!H", _recv_exact(conn, 2))[0]
    elif size == 127:
        size = struct.unpack("!Q", _recv_exact(conn, 8))[0]
    mask = _recv_exact(conn, 4) if masked else b""
    payload = _recv_exact(conn, size)
    if masked:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return payload.decode("utf-8")


def _send_websocket_text(conn: socket.socket, payload: dict[str, Any]) -> None:
    """Send one unmasked server text frame."""
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header = bytearray([0x81])
    if len(data) < 126:
        header.append(len(data))
    elif len(data) < 65536:
        header.append(126)
        header.extend(struct.pack("!H", len(data)))
    else:
        header.append(127)
        header.extend(struct.pack("!Q", len(data)))
    conn.sendall(bytes(header) + data)


def _recv_exact(conn: socket.socket, size: int) -> bytes:
    """Receive an exact number of bytes from a socket."""
    chunks = bytearray()
    while len(chunks) < size:
        chunk = conn.recv(size - len(chunks))
        if not chunk:
            raise AssertionError("websocket frame closed early")
        chunks.extend(chunk)
    return bytes(chunks)


_WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

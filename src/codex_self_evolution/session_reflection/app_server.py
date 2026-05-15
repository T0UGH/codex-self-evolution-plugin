from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Protocol

WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


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


class UnixWebSocketAppServerTransport:
    """JSON message transport over a Unix WebSocket app-server socket."""

    def __init__(
        self,
        *,
        socket_path: str | Path,
        client_name: str = "codex-self-evolution",
        client_version: str = "0.0.0",
    ) -> None:
        """Create a lazy WebSocket connection to one app-server Unix socket."""
        self.socket_path = Path(socket_path)
        self.client_name = client_name
        self.client_version = client_version
        self._socket: socket.socket | None = None
        self._initialized = False

    def request(self, method: str, params: dict[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
        """Send one initialized app-server request and return its result object."""
        self._ensure_initialized(timeout_seconds=timeout_seconds)
        request_id = uuid.uuid4().hex
        self._send_json({"id": request_id, "method": method, "params": params})
        return _result_from_response(self._receive_matching_response(request_id), expected_id=request_id)

    def close(self) -> None:
        """Close the WebSocket connection if it was opened."""
        if self._socket is None:
            return
        try:
            self._socket.close()
        finally:
            self._socket = None
            self._initialized = False

    def _ensure_initialized(self, *, timeout_seconds: float) -> None:
        """Connect and run the app-server initialize handshake once."""
        if self._initialized:
            assert self._socket is not None
            self._socket.settimeout(timeout_seconds)
            return
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.settimeout(timeout_seconds)
        try:
            self._socket.connect(str(self.socket_path))
            self._upgrade_to_websocket()
            init_id = uuid.uuid4().hex
            self._send_json(
                {
                    "id": init_id,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {
                            "name": self.client_name,
                            "version": self.client_version,
                        },
                        "capabilities": {"experimentalApi": True},
                    },
                }
            )
            _result_from_response(self._receive_matching_response(init_id), expected_id=init_id)
            self._send_json({"method": "initialized"})
            self._initialized = True
        except Exception:
            self.close()
            raise

    def _upgrade_to_websocket(self) -> None:
        """Perform the HTTP upgrade required by app-server Unix listeners."""
        assert self._socket is not None
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET / HTTP/1.1\r\n"
            "Host: localhost\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self._socket.sendall(request.encode("ascii"))
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = self._socket.recv(4096)
            if not chunk:
                raise AppServerError("app-server websocket handshake closed early")
            raw += chunk
        header_text = raw.decode("utf-8", errors="replace").split("\r\n\r\n", 1)[0]
        if " 101 " not in header_text.split("\r\n", 1)[0]:
            raise AppServerError(f"app-server websocket upgrade failed: {header_text[:400]}")
        expected_accept = base64.b64encode(hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()).decode(
            "ascii"
        )
        if expected_accept.lower() not in header_text.lower():
            raise AppServerError("app-server websocket upgrade returned an invalid accept key")

    def _send_json(self, payload: dict[str, Any]) -> None:
        """Send one masked client text frame containing a JSON object."""
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x81])
        if len(data) < 126:
            header.append(0x80 | len(data))
        elif len(data) < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", len(data)))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", len(data)))
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        assert self._socket is not None
        self._socket.sendall(bytes(header) + mask + masked)

    def _receive_matching_response(self, expected_id: str) -> dict[str, Any]:
        """Read frames until the response with the expected id arrives."""
        while True:
            text = self._receive_text_frame()
            try:
                parsed = json.loads(text)
            except ValueError as exc:
                raise AppServerError("app-server websocket response was not valid JSON") from exc
            if isinstance(parsed, dict) and parsed.get("id") == expected_id:
                return parsed

    def _receive_text_frame(self) -> str:
        """Receive one app-server text frame, answering pings while waiting."""
        assert self._socket is not None
        while True:
            first = _recv_exact(self._socket, 2)
            first_byte, second_byte = first
            opcode = first_byte & 0x0F
            masked = bool(second_byte & 0x80)
            size = second_byte & 0x7F
            if size == 126:
                size = struct.unpack("!H", _recv_exact(self._socket, 2))[0]
            elif size == 127:
                size = struct.unpack("!Q", _recv_exact(self._socket, 8))[0]
            mask = _recv_exact(self._socket, 4) if masked else b""
            payload = _recv_exact(self._socket, size)
            if masked:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 1:
                return payload.decode("utf-8")
            if opcode == 8:
                raise AppServerError("app-server websocket closed")
            if opcode == 9:
                self._send_control_frame(0x0A, payload)

    def _send_control_frame(self, opcode: int, payload: bytes) -> None:
        """Send a small unmasked control frame."""
        if len(payload) >= 126:
            raise AppServerError("websocket control frame payload is too large")
        assert self._socket is not None
        self._socket.sendall(bytes([0x80 | opcode, len(payload)]) + payload)


class ManagedAppServerTransport:
    """Start a temporary Codex app-server and speak to it over Unix WebSocket."""

    def __init__(
        self,
        *,
        codex_binary: str = "codex",
        startup_timeout_seconds: float = 5.0,
        client_name: str = "codex-self-evolution",
        client_version: str = "0.0.0",
    ) -> None:
        """Create a lazy managed app-server transport."""
        self.codex_binary = codex_binary
        self.startup_timeout_seconds = startup_timeout_seconds
        self.client_name = client_name
        self.client_version = client_version
        self._tempdir: tempfile.TemporaryDirectory[str] | None = None
        self._process: subprocess.Popen[str] | None = None
        self._transport: UnixWebSocketAppServerTransport | None = None

    def request(self, method: str, params: dict[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
        """Start the managed server when needed and send one request."""
        self._ensure_started()
        assert self._transport is not None
        return self._transport.request(method, params, timeout_seconds=timeout_seconds)

    def close(self) -> None:
        """Stop the managed app-server and remove its temporary directory."""
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)
            self._process = None
        if self._tempdir is not None:
            self._tempdir.cleanup()
            self._tempdir = None

    def __del__(self) -> None:
        """Best-effort cleanup for short-lived hook worker processes."""
        try:
            self.close()
        except Exception:
            pass

    def _ensure_started(self) -> None:
        """Launch Codex app-server and wait until its Unix socket exists."""
        if self._transport is not None:
            return
        if shutil.which(self.codex_binary) is None:
            raise AppServerError(f"codex app-server binary not found: {self.codex_binary}")
        self._tempdir = tempfile.TemporaryDirectory(prefix="csep-app-server-")
        root = Path(self._tempdir.name)
        socket_path = root / "app.sock"
        log_path = root / "app-server.log"
        log_handle = open(log_path, "w", encoding="utf-8")
        try:
            self._process = subprocess.Popen(  # noqa: S603 - trusted local CLI argv.
                [self.codex_binary, "app-server", "--listen", f"unix://{socket_path}"],
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        finally:
            log_handle.close()
        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            if socket_path.is_socket():
                self._transport = UnixWebSocketAppServerTransport(
                    socket_path=socket_path,
                    client_name=self.client_name,
                    client_version=self.client_version,
                )
                return
            if self._process.poll() is not None:
                break
            time.sleep(0.05)
        detail = ""
        try:
            detail = log_path.read_text(encoding="utf-8")[:400]
        except OSError:
            pass
        self.close()
        raise AppServerError(f"codex app-server did not create a Unix socket: {socket_path}; {detail}")


class AutoAppServerTransport:
    """Use an existing control socket when present, otherwise start app-server."""

    def __init__(self) -> None:
        """Create a lazy transport selector."""
        self._transport: AppServerTransport | None = None

    def request(self, method: str, params: dict[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
        """Select the best available app-server transport and send a request."""
        if self._transport is None:
            socket_path = default_app_server_control_socket()
            if socket_path.is_socket():
                self._transport = UnixWebSocketAppServerTransport(socket_path=socket_path)
            else:
                self._transport = ManagedAppServerTransport()
        return self._transport.request(method, params, timeout_seconds=timeout_seconds)

    def close(self) -> None:
        """Close the selected transport if it supports cleanup."""
        close = getattr(self._transport, "close", None)
        if callable(close):
            close()
        self._transport = None


def default_app_server_control_socket() -> Path:
    """Return the default Unix socket used by `codex app-server proxy`."""
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    return codex_home / "app-server-control" / "app-server-control.sock"


def app_server_proxy_status() -> dict[str, Any]:
    """Return whether the default app-server proxy boundary is currently usable."""
    socket_path = default_app_server_control_socket()
    if socket_path.is_socket():
        return {
            "available": True,
            "mode": "control_socket",
            "reason": None,
            "socket_path": str(socket_path),
        }
    if socket_path.exists():
        return {
            "available": False,
            "mode": None,
            "reason": "control_socket_not_socket",
            "socket_path": str(socket_path),
        }
    if shutil.which("codex") is not None:
        return {
            "available": True,
            "mode": "managed_app_server",
            "reason": "control_socket_missing",
            "socket_path": str(socket_path),
        }
    return {
        "available": False,
        "mode": None,
        "reason": "codex_binary_missing",
        "socket_path": str(socket_path),
    }


class ReflectionAppServerClient:
    """Typed client for the reflection worker app-server methods."""

    def __init__(self, transport: AppServerTransport | None = None, *, timeout_seconds: float = 900.0) -> None:
        """Create a client with an injectable transport and timeout."""
        self._transport = transport if transport is not None else AutoAppServerTransport()
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
            "sandboxPolicy": _sandbox_policy_for_turn_start(sandbox),
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
    return _result_from_response(response, expected_id=expected_id)


def _result_from_response(response: dict[str, Any], *, expected_id: str) -> dict[str, Any]:
    """Return a result object from one app-server response payload."""
    if "error" in response and response["error"]:
        raise AppServerError(f"app-server error response: {response['error']}")
    if "result" not in response:
        raise AppServerError("app-server response missing result")
    result = response["result"]
    if not isinstance(result, dict):
        raise AppServerError("app-server result is not an object")
    return result


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    """Receive exactly size bytes or fail if the app-server closes early."""
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise AppServerError("app-server websocket closed while reading frame")
        chunks.extend(chunk)
    return bytes(chunks)


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


def _sandbox_policy_for_turn_start(sandbox: str) -> dict[str, Any]:
    """Convert CLI sandbox names into app-server v2 sandboxPolicy objects."""
    if sandbox == "danger-full-access":
        return {"type": "dangerFullAccess"}
    if sandbox == "workspace-write":
        return {"type": "workspaceWrite"}
    if sandbox == "read-only":
        return {"type": "readOnly"}
    return {"type": sandbox}

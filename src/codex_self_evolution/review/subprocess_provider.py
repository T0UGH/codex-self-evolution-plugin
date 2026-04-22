"""Subprocess-based review provider: delegate the reviewer turn to a locally-
installed CLI (``codex`` / ``opencode`` / a custom binary).

Why this exists: opens the "no API key needed" path that lets users who
have a ChatGPT / Copilot / Gemini CLI already authenticated on their
machine run the plugin without handing out an API key. Design doc
``docs/design_v2.md`` §4.2.

Three axes of flexibility:

- **argv**: the full command to invoke. Built-in defaults for ``codex`` and
  ``opencode``; any other CLI can be plugged in via
  ``reviewer.subprocess.command`` in config.toml.
- **payload_mode**: how the review snapshot is handed to the child process
  (stdin / tempfile + path / inline append to prompt).
- **response_format**: how to parse the child's stdout (codex event stream
  / opencode event stream / a single JSON object).

Retries mirror the HTTP provider: transient failures (timeouts, crashes)
get retried with backoff; auth / path errors hard-fail so misconfiguration
surfaces immediately instead of burning backoff budget.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .providers import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_MAX_RETRIES,
    ProviderResult,
    ReviewProviderError,
)


_logger = logging.getLogger(__name__)


# Built-in argv defaults for the "provider = codex-cli / opencode-cli"
# shortcuts in config.toml. Users can still override via
# ``reviewer.subprocess.command = [...]``; these are the fall-backs.
DEFAULT_CODEX_CLI_ARGV: tuple[str, ...] = ("codex", "exec", "--json", "--skip-git-repo-check")
DEFAULT_OPENCODE_CLI_ARGV: tuple[str, ...] = (
    "opencode", "run", "--format", "json", "--dangerously-skip-permissions",
)


ALLOWED_PAYLOAD_MODES = {"stdin", "file", "inline"}
ALLOWED_RESPONSE_FORMATS = {"codex-events", "opencode-events", "raw-json"}


@dataclass
class _Invocation:
    """One call's resolved argv + stdin content + tempfile path (if any)."""

    argv: list[str]
    stdin_bytes: bytes | None
    tempfile_path: str | None


class SubprocessReviewProvider:
    """Dispatch a reviewer turn to an external CLI via ``subprocess.run``."""

    name: str

    def __init__(
        self,
        name: str,
        argv: list[str],
        payload_mode: str = "stdin",
        response_format: str = "codex-events",
        timeout: float = 90.0,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_seconds: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS,
    ) -> None:
        if not argv:
            raise ReviewProviderError(
                f"{name} subprocess provider requires a non-empty argv; "
                "set reviewer.subprocess.command in config.toml"
            )
        if payload_mode not in ALLOWED_PAYLOAD_MODES:
            raise ReviewProviderError(
                f"{name}: payload_mode={payload_mode!r} not in {sorted(ALLOWED_PAYLOAD_MODES)}"
            )
        if response_format not in ALLOWED_RESPONSE_FORMATS:
            raise ReviewProviderError(
                f"{name}: response_format={response_format!r} not in {sorted(ALLOWED_RESPONSE_FORMATS)}"
            )

        # Q1 decision: hard fail when argv[0] isn't on PATH. Silent fallback
        # would let a typo'd binary name silently disable the reviewer for
        # days before anyone notices pending/ stopped growing.
        resolved = shutil.which(argv[0])
        if resolved is None:
            raise ReviewProviderError(
                f"{name} subprocess provider: '{argv[0]}' not found on PATH. "
                "Install it or switch reviewer.provider in config.toml."
            )

        self.name = name
        self.argv = list(argv)
        self.payload_mode = payload_mode
        self.response_format = response_format
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        # Pad backoff the same way HTTPReviewProvider does, so config.toml
        # writers only need to set one of ``max_retries`` / ``retry_backoff``.
        if len(backoff_seconds) < self.max_retries and backoff_seconds:
            last = backoff_seconds[-1]
            backoff_seconds = tuple(backoff_seconds) + (last,) * (self.max_retries - len(backoff_seconds))
        self.backoff_seconds = tuple(backoff_seconds)

    # ---- Public API (matches ReviewProvider protocol) ------------------

    def run(self, snapshot: dict[str, Any], prompt: str, options: dict[str, Any]) -> ProviderResult:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            invocation = self._prepare(snapshot, prompt)
            try:
                raw_text = self._execute(invocation)
                return ProviderResult(
                    provider=self.name,
                    raw_text=raw_text,
                    response_payload={"argv": invocation.argv,
                                      "payload_mode": self.payload_mode,
                                      "response_format": self.response_format},
                    request_payload={"argv": invocation.argv},
                )
            except _RetryableSubprocessError as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    _logger.info(
                        "%s retrying after %s (attempt %d/%d)",
                        self.name, exc.reason, attempt + 1, self.max_retries + 1,
                    )
                    time.sleep(self.backoff_seconds[attempt])
                    continue
                raise ReviewProviderError(f"{self.name} failed after retries: {exc}") from exc
            finally:
                _safe_unlink(invocation.tempfile_path)
        assert last_exc is not None  # pragma: no cover
        raise ReviewProviderError(f"{self.name} failed after retries: {last_exc}") from last_exc

    # ---- Internals -----------------------------------------------------

    def _prepare(self, snapshot: dict[str, Any], prompt: str) -> _Invocation:
        """Materialise argv + stdin + tempfile according to ``payload_mode``.

        Kept as a pure function of (snapshot, prompt) so retries always
        rebuild from a known starting state — never reuse a half-consumed
        stdin buffer from a previous failed attempt.
        """
        snapshot_json = json.dumps(snapshot, ensure_ascii=False)
        argv = list(self.argv)

        if self.payload_mode == "stdin":
            # Feed {prompt}\n\n{snapshot} so the CLI sees the instruction
            # first and the structured input as the remainder. Most
            # tool-calling CLIs treat stdin as the user-message body.
            body = f"{prompt}\n\n--- snapshot ---\n{snapshot_json}\n"
            return _Invocation(argv=argv, stdin_bytes=body.encode("utf-8"), tempfile_path=None)

        if self.payload_mode == "file":
            # Write snapshot to a tempfile; attach path to prompt text and
            # append ``--file <path>`` to argv only if the CLI actually
            # accepts that flag (opencode does; codex historically does
            # not). We stay conservative: emit the path in the prompt so
            # the model can read it; leave argv untouched by default.
            fd, tmp_path = tempfile.mkstemp(prefix="csep-sub-", suffix=".json")
            os.close(fd)
            Path(tmp_path).write_text(snapshot_json, encoding="utf-8")
            # Many opencode-like CLIs take the prompt as a trailing
            # positional arg after ``--``; append if the argv shape looks
            # like it.
            full_prompt = f"{prompt}\n\nReview payload attached at: {tmp_path}"
            if "--" in argv:
                argv.append(full_prompt)
            elif "--file" in argv:
                # CLI may already have ``--file X`` template — leave it
                # untouched and just append prompt.
                argv.append(full_prompt)
            else:
                argv.extend(["--file", tmp_path, "--", full_prompt])
            return _Invocation(argv=argv, stdin_bytes=None, tempfile_path=tmp_path)

        if self.payload_mode == "inline":
            # Smallest-surface-area mode: embed snapshot directly into prompt.
            # Risky for very large snapshots (argv size cap) but predictable.
            full_prompt = f"{prompt}\n\n--- snapshot ---\n{snapshot_json}\n"
            if argv and argv[-1] == "--":
                argv.append(full_prompt)
            else:
                argv.append(full_prompt)
            return _Invocation(argv=argv, stdin_bytes=None, tempfile_path=None)

        raise ReviewProviderError(f"{self.name}: unhandled payload_mode={self.payload_mode!r}")

    def _execute(self, invocation: _Invocation) -> str:
        """Spawn the child, wait for it, parse its stdout, return raw text."""
        try:
            proc = subprocess.run(
                invocation.argv,
                input=invocation.stdin_bytes,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise _RetryableSubprocessError(reason="timeout") from exc
        except OSError as exc:
            # PATH issue / permission denied / exec format error — none
            # of these improve with a retry.
            raise ReviewProviderError(
                f"{self.name} subprocess failed to start: {exc}"
            ) from exc

        if proc.returncode != 0:
            stderr_snippet = proc.stderr.decode("utf-8", errors="replace").strip()[:500]
            # Non-zero exit can mean the CLI hit a transient API upstream
            # (rate limit, 529) — those usually print JSON error lines we
            # can classify in the parser. Let parser decide whether to
            # retry vs hard-fail by surfacing the output unchanged.
            stdout = proc.stdout.decode("utf-8", errors="replace")
            if _looks_like_transient(stderr_snippet, stdout):
                raise _RetryableSubprocessError(reason=f"nonzero_exit({proc.returncode})")
            raise ReviewProviderError(
                f"{self.name} subprocess exited {proc.returncode}: {stderr_snippet}"
            )

        stdout_text = proc.stdout.decode("utf-8", errors="replace")
        parsed = _parse_stdout(stdout_text, self.response_format, self.name)
        if not parsed:
            stderr_snippet = proc.stderr.decode("utf-8", errors="replace").strip()[:500]
            raise ReviewProviderError(
                f"{self.name} produced no parseable assistant text; stderr={stderr_snippet}"
            )
        return parsed


# ---- Helpers ------------------------------------------------------------


class _RetryableSubprocessError(Exception):
    """Internal marker for failures worth retrying. Callers above wrap it
    in ``ReviewProviderError`` after the retry budget runs out."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _safe_unlink(path: str | None) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


# stderr / stdout patterns that suggest upstream flakiness worth retrying.
# We intentionally keep this narrow — retrying a real auth error or a bad
# argv just wastes time. Extend only when a new flakiness mode is seen in
# plugin.log.
_TRANSIENT_INDICATORS = (
    "429", "529", "overloaded", "rate limit", "rate_limit",
    "timeout", "timed out", "temporarily unavailable",
    "connection reset", "connection refused", "EAI_AGAIN",
)


def _looks_like_transient(stderr: str, stdout: str) -> bool:
    haystack = f"{stderr}\n{stdout}".lower()
    return any(signal in haystack for signal in _TRANSIENT_INDICATORS)


def _parse_stdout(stdout: str, response_format: str, provider_name: str) -> str:
    """Extract the assistant text payload from ``stdout`` per format."""
    if response_format == "codex-events":
        return _parse_codex_events(stdout)
    if response_format == "opencode-events":
        return _parse_opencode_events(stdout)
    if response_format == "raw-json":
        # Assume whole stdout IS the JSON reviewer payload. Trim wrapping
        # prose or code fences before returning so the downstream lenient
        # parser doesn't choke.
        text = stdout.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return text
    raise ReviewProviderError(f"{provider_name}: unknown response_format={response_format!r}")


def _parse_codex_events(stdout: str) -> str:
    """Codex CLI emits a JSON-lines stream of event objects. The
    assistant-visible text lives in events with ``type == "item.completed"``
    whose ``item.item_type == "assistant_message"`` carries the final text,
    or in ``item.output[*].text`` chunks on some older versions. We
    aggregate both shapes and return the concatenation.

    Silent on non-JSON lines so the trailing "Session started..." banner
    or progress prints don't derail parsing.
    """
    chunks: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        # item.completed → item.item_type == assistant_message
        if event.get("type") in {"item.completed", "agent_message"}:
            item = event.get("item") or {}
            text = item.get("text") or item.get("content") or ""
            if isinstance(text, str) and text:
                chunks.append(text)
                continue
            # Some codex versions emit content as a list of parts.
            parts = item.get("output") or []
            if isinstance(parts, list):
                for part in parts:
                    if isinstance(part, dict):
                        ptext = part.get("text")
                        if isinstance(ptext, str) and ptext:
                            chunks.append(ptext)
        # Direct message shape (some older versions).
        elif event.get("type") == "message" and isinstance(event.get("text"), str):
            chunks.append(event["text"])
    return "\n".join(chunks).strip()


def _parse_opencode_events(stdout: str) -> str:
    """opencode's ``--format json`` shape: ``{"type":"text","part":{"text":"..."}}``.
    Reuse the same logic as the existing compile backend extractor so a
    bug fix in one helps the other. Errors (``type:"error"``) become a
    retryable condition by looking like transient text in parent checks.
    """
    chunks: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") != "text":
            continue
        part = event.get("part") or {}
        text = part.get("text")
        if isinstance(text, str) and text:
            chunks.append(text)
    return "".join(chunks).strip()

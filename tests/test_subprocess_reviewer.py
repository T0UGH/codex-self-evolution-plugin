"""Tests for the subprocess reviewer provider — codex-cli / opencode-cli / custom.

Why the density: the no-API-key path is our "anyone can try the plugin"
story. If the subprocess adapter flakes on a real codex/opencode output
update, every fresh user hits a cold-path failure with no fallback. These
tests pin:

- Provider init (argv missing from PATH → hard fail, per design Q1)
- Three payload modes: stdin / file / inline
- Three response formats: codex-events / opencode-events / raw-json
- Retry classification: timeout / transient stderr hints / auth-y errors
- Tempfile hygiene
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from codex_self_evolution.review import subprocess_provider as mod
from codex_self_evolution.review.providers import ReviewProviderError
from codex_self_evolution.review.subprocess_provider import (
    DEFAULT_CODEX_CLI_ARGV,
    DEFAULT_OPENCODE_CLI_ARGV,
    SubprocessReviewProvider,
    _looks_like_transient,
    _parse_codex_events,
    _parse_opencode_events,
    _parse_stdout,
)


# ---- Test doubles -------------------------------------------------------


class _FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    *,
    responses: list,  # list of _FakeCompletedProcess or Exception to raise
) -> dict:
    """Install a deterministic ``subprocess.run`` replacement and record calls."""
    state = {"count": 0, "argv_history": [], "input_history": []}

    def fake_run(argv, **kwargs):
        state["argv_history"].append(list(argv))
        state["input_history"].append(kwargs.get("input"))
        idx = state["count"]
        state["count"] += 1
        if idx >= len(responses):
            raise AssertionError(f"subprocess.run called {idx + 1} times, expected <= {len(responses)}")
        item = responses[idx]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod.shutil, "which", lambda name: f"/usr/bin/{name}")  # always on PATH
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)  # no retry backoff delays
    return state


# ---- Provider init ------------------------------------------------------


def test_missing_binary_on_path_hard_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Design Q1: codex-cli when codex isn't installed → immediate error."""
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    with pytest.raises(ReviewProviderError) as exc:
        SubprocessReviewProvider(name="codex-cli", argv=["codex", "exec"])
    assert "not found on PATH" in str(exc.value)


def test_empty_argv_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/x")
    with pytest.raises(ReviewProviderError):
        SubprocessReviewProvider(name="custom", argv=[])


def test_invalid_payload_mode_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/x")
    with pytest.raises(ReviewProviderError):
        SubprocessReviewProvider(name="x", argv=["x"], payload_mode="magic")


def test_invalid_response_format_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/x")
    with pytest.raises(ReviewProviderError):
        SubprocessReviewProvider(name="x", argv=["x"], response_format="xml")


# ---- Happy path: each payload_mode × response_format combination -------


def test_stdin_payload_codex_events_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    good_codex_event = (
        b'{"type":"item.completed","item":{"item_type":"assistant_message",'
        b'"text":"{\\"memory_updates\\":[],\\"recall_candidate\\":[],\\"skill_action\\":[]}"}}\n'
    )
    _patch_subprocess(monkeypatch, responses=[_FakeCompletedProcess(stdout=good_codex_event)])

    provider = SubprocessReviewProvider(
        name="codex-cli",
        argv=list(DEFAULT_CODEX_CLI_ARGV),
        payload_mode="stdin",
        response_format="codex-events",
    )
    result = provider.run(snapshot={"x": 1}, prompt="review this", options={})
    assert '"memory_updates"' in result.raw_text
    assert result.provider == "codex-cli"


def test_file_payload_opencode_events_happy_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    opencode_event = (
        b'{"type":"text","part":{"text":"{\\"memory_updates\\":[],\\"recall_candidate\\":[],\\"skill_action\\":[]}"}}\n'
    )
    state = _patch_subprocess(monkeypatch, responses=[_FakeCompletedProcess(stdout=opencode_event)])

    provider = SubprocessReviewProvider(
        name="opencode-cli",
        argv=list(DEFAULT_OPENCODE_CLI_ARGV),
        payload_mode="file",
        response_format="opencode-events",
    )
    result = provider.run(snapshot={"a": 1}, prompt="hi", options={})
    assert "memory_updates" in result.raw_text
    # file mode must have appended a path to argv containing the prompt.
    argv_used = state["argv_history"][0]
    assert any("snapshot" in str(arg) or arg.endswith(".json") for arg in argv_used)


def test_inline_payload_raw_json_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = b'```json\n{"memory_updates": [], "recall_candidate": [], "skill_action": []}\n```\n'
    _patch_subprocess(monkeypatch, responses=[_FakeCompletedProcess(stdout=raw)])

    provider = SubprocessReviewProvider(
        name="custom",
        argv=["my-cli"],
        payload_mode="inline",
        response_format="raw-json",
    )
    result = provider.run(snapshot={}, prompt="go", options={})
    # Code fence stripped, JSON preserved.
    assert result.raw_text.startswith("{")
    assert result.raw_text.endswith("}")


# ---- Retry classification ----------------------------------------------


def test_timeout_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    good = b'{"type":"item.completed","item":{"text":"{\\"memory_updates\\":[],\\"recall_candidate\\":[],\\"skill_action\\":[]}"}}\n'
    timeout_exc = subprocess.TimeoutExpired(cmd=["codex"], timeout=5.0)
    state = _patch_subprocess(monkeypatch, responses=[timeout_exc, _FakeCompletedProcess(stdout=good)])

    provider = SubprocessReviewProvider(
        name="codex-cli",
        argv=list(DEFAULT_CODEX_CLI_ARGV),
        max_retries=2,
    )
    result = provider.run(snapshot={}, prompt="p", options={})
    assert state["count"] == 2
    assert "memory_updates" in result.raw_text


def test_transient_stderr_nonzero_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """A nonzero exit whose stderr mentions a transient condition
    (rate limit / 529 / timeout) should be retried, not hard-failed."""
    good = b'{"type":"item.completed","item":{"text":"{\\"memory_updates\\":[],\\"recall_candidate\\":[],\\"skill_action\\":[]}"}}\n'
    flaky = _FakeCompletedProcess(returncode=1, stderr=b"upstream returned HTTP 529 overloaded")
    state = _patch_subprocess(monkeypatch, responses=[flaky, _FakeCompletedProcess(stdout=good)])

    provider = SubprocessReviewProvider(
        name="codex-cli",
        argv=list(DEFAULT_CODEX_CLI_ARGV),
        max_retries=2,
    )
    result = provider.run(snapshot={}, prompt="p", options={})
    assert state["count"] == 2
    assert "memory_updates" in result.raw_text


def test_nonzero_exit_without_transient_hint_raises_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auth failures / missing files / argv typos don't get retried."""
    auth_err = _FakeCompletedProcess(returncode=2, stderr=b"Error: not authenticated; run `codex auth`")
    state = _patch_subprocess(monkeypatch, responses=[auth_err])

    provider = SubprocessReviewProvider(
        name="codex-cli",
        argv=list(DEFAULT_CODEX_CLI_ARGV),
        max_retries=2,
    )
    with pytest.raises(ReviewProviderError) as exc:
        provider.run(snapshot={}, prompt="p", options={})
    assert state["count"] == 1  # no retries
    assert "not authenticated" in str(exc.value)


def test_persistent_timeout_exhausts_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _patch_subprocess(
        monkeypatch,
        responses=[
            subprocess.TimeoutExpired(cmd=["codex"], timeout=5.0),
            subprocess.TimeoutExpired(cmd=["codex"], timeout=5.0),
            subprocess.TimeoutExpired(cmd=["codex"], timeout=5.0),
        ],
    )
    provider = SubprocessReviewProvider(
        name="codex-cli",
        argv=list(DEFAULT_CODEX_CLI_ARGV),
        max_retries=2,
    )
    with pytest.raises(ReviewProviderError):
        provider.run(snapshot={}, prompt="p", options={})
    # Initial + 2 retries
    assert state["count"] == 3


def test_empty_stdout_surfaces_stderr_snippet(monkeypatch: pytest.MonkeyPatch) -> None:
    """If parse yields "" and exit was 0, we still want the stderr in the
    error message so users can see what the CLI complained about."""
    _patch_subprocess(monkeypatch, responses=[_FakeCompletedProcess(stdout=b"", stderr=b"some diagnostic")])
    provider = SubprocessReviewProvider(
        name="codex-cli",
        argv=list(DEFAULT_CODEX_CLI_ARGV),
        max_retries=0,  # no retries to keep the assertion simple
    )
    with pytest.raises(ReviewProviderError) as exc:
        provider.run(snapshot={}, prompt="p", options={})
    assert "some diagnostic" in str(exc.value)


# ---- OSError at spawn time --------------------------------------------


def test_spawn_oserror_raises_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """Permission denied / exec format error → ReviewProviderError, not retry."""
    _patch_subprocess(monkeypatch, responses=[OSError("permission denied")])
    provider = SubprocessReviewProvider(
        name="codex-cli",
        argv=list(DEFAULT_CODEX_CLI_ARGV),
        max_retries=5,
    )
    with pytest.raises(ReviewProviderError) as exc:
        provider.run(snapshot={}, prompt="p", options={})
    assert "failed to start" in str(exc.value)


# ---- Pure parser helpers ----------------------------------------------


def test_parse_codex_events_aggregates_text_parts() -> None:
    stream = "\n".join(
        [
            '{"type":"item.completed","item":{"text":"part1 "}}',
            '{"type":"item.completed","item":{"text":"part2"}}',
            '{"type":"progress","message":"noise"}',
            "non-json-banner",
        ]
    )
    assert _parse_codex_events(stream) == "part1 \npart2"


def test_parse_opencode_events_ignores_error_type() -> None:
    """Opencode emits type:"error" for upstream failures — those don't
    contribute to assistant text, but parsing must keep working."""
    stream = "\n".join(
        [
            '{"type":"error","error":{"data":{"message":"401"}}}',
            '{"type":"text","part":{"text":"{\\"ok\\":true}"}}',
        ]
    )
    assert _parse_opencode_events(stream) == '{"ok":true}'


def test_raw_json_strips_code_fence() -> None:
    text = "```json\n{\"hello\": \"world\"}\n```"
    assert _parse_stdout(text, "raw-json", "x") == '{"hello": "world"}'


def test_looks_like_transient_matches_common_upstream_failures() -> None:
    assert _looks_like_transient("HTTP 529 overloaded_error", "")
    assert _looks_like_transient("rate limit exceeded", "")
    assert _looks_like_transient("", "network connection reset")
    # Auth errors are NOT transient.
    assert not _looks_like_transient("invalid API key", "")
    assert not _looks_like_transient("", "404 not found")


# ---- stdin payload delivery format ------------------------------------


def test_stdin_payload_includes_snapshot_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stdin body the child sees must contain the full snapshot JSON
    so the model can read it — otherwise the reviewer has no context."""
    good = b'{"type":"item.completed","item":{"text":"{\\"memory_updates\\":[]}"}}\n'
    state = _patch_subprocess(monkeypatch, responses=[_FakeCompletedProcess(stdout=good)])
    provider = SubprocessReviewProvider(
        name="codex-cli",
        argv=list(DEFAULT_CODEX_CLI_ARGV),
        payload_mode="stdin",
    )
    provider.run(snapshot={"marker": "LOOK_FOR_ME"}, prompt="review", options={})
    body = state["input_history"][0].decode("utf-8")
    assert "LOOK_FOR_ME" in body
    assert "review" in body  # prompt is also included

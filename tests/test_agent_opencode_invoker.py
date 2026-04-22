"""Tests for the opencode 1.4.0 subprocess adapter.

Before this, the default `opencode run --stdin-json --stdout-json` command
didn't match any real opencode CLI — every agent:opencode compile silently
fell back to the script backend. These tests lock in the new adapter shape
(payload via `--file`, `--format json` event stream) and the defensive text
cleanup so a future opencode upgrade can't regress the integration unnoticed.
"""
from __future__ import annotations

import json
import os

import pytest

from codex_self_evolution.compiler import backends
from codex_self_evolution.compiler.backends import (
    AgentCompilerBackend,
    _build_compile_prompt,
    _build_default_opencode_command,
    _cleanup_agent_text,
    _extract_assistant_text,
    _extract_first_json_object,
    _write_payload_tempfile,
)
from codex_self_evolution.schemas import Suggestion, SuggestionEnvelope


# --- small shared fixtures ---------------------------------------------------


def _envelope() -> SuggestionEnvelope:
    return SuggestionEnvelope(
        schema_version=1,
        suggestion_id="sug-1",
        idempotency_key="idem-1",
        thread_id="thread-1",
        cwd="/tmp/repo",
        repo_fingerprint="fp-1",
        reviewer_timestamp="2026-04-21T00:00:00Z",
        suggestions=[
            Suggestion(family="memory_updates", summary="s", details={"content": "c"}),
        ],
        source_authority=[],
    )


def _context() -> dict:
    return {
        "cwd": "/tmp/repo",
        "repo_fingerprint": "fp-1",
        "skills_dir": "/tmp/state/skills",
        "memory_dir": "/tmp/state/memory",
        "recall_dir": "/tmp/state/recall",
        "existing_manifest": [],
        "existing_user_memory": "",
        "existing_global_memory": "",
        "existing_memory_index": {"user": [], "global": []},
        "existing_recall_records": [],
        "existing_recall_markdown": "",
        "memory_paths": {},
        "recall_paths": {},
    }


# --- pure helpers ------------------------------------------------------------


def test_extract_assistant_text_concatenates_text_parts_and_skips_noise():
    stream = "\n".join(
        [
            '{"type":"step_start","timestamp":1,"part":{"type":"step-start"}}',
            '{"type":"text","timestamp":2,"part":{"type":"text","text":"{\\"memory_records\\":"}}',
            '{"type":"text","timestamp":3,"part":{"type":"text","text":"{\\"user\\":[],\\"global\\":[]},"}}',
            '{"type":"text","timestamp":4,"part":{"type":"text","text":"\\"recall_records\\":[]}"}}',
            '{"type":"step_finish","timestamp":5,"part":{"reason":"stop"}}',
            "Shell cwd was reset to /tmp",  # non-JSON trailing line
            "",  # blank
        ]
    )
    out = _extract_assistant_text(stream)
    assert json.loads(out) == {"memory_records": {"user": [], "global": []}, "recall_records": []}


def test_extract_assistant_text_tolerates_garbled_lines():
    # If opencode ever emits a partial line (network hiccup mid-stream), the
    # parser must keep going rather than abort — we'd rather finish with
    # whatever text came through than throw a RuntimeError.
    stream = "\n".join(
        [
            '{"type":"step_start","part":{}}',
            "not-json-garbage",
            '{"type":"text","part":{"text":"{\\"ok\\":true}"}}',
        ]
    )
    assert _extract_assistant_text(stream) == '{"ok":true}'


def test_extract_assistant_text_raises_on_error_event_without_text():
    """Regression for the launchd-env 401 bug (2026-04-22):

    opencode emits ``{"type":"error","error":{"name":"APIError","data":{
    "message":"login fail ...","statusCode":401}}}`` when its API key is
    missing, exits 0, and the old extractor just returned '' — so every
    receipt said "opencode produced no assistant text" with an empty stderr,
    hiding the real cause (MiniMax 401). We now raise with the upstream
    message inline so the fallback receipt carries an actionable diagnostic.
    """
    stream = "\n".join(
        [
            '{"type":"step_start","timestamp":1,"part":{"type":"step-start"}}',
            '{"type":"error","timestamp":2,"error":{"name":"APIError","data":{"message":"login fail: missing Authorization","statusCode":401}}}',
        ]
    )
    with pytest.raises(RuntimeError) as exc:
        _extract_assistant_text(stream)
    assert "401" in str(exc.value)
    assert "login fail" in str(exc.value)


def test_extract_assistant_text_returns_text_when_error_also_present():
    """If opencode surfaces an error event AND valid text (partial success
    scenario — some streams can have a transient error followed by a retry
    that does produce text), return the text. The error is informative but
    not authoritative when real text made it through."""
    stream = "\n".join(
        [
            '{"type":"error","error":{"name":"TransientError","data":{"message":"retrying"}}}',
            '{"type":"text","part":{"text":"{\\"ok\\":true}"}}',
        ]
    )
    assert _extract_assistant_text(stream) == '{"ok":true}'


def test_cleanup_agent_text_strips_code_fence():
    raw = "```json\n{\"hello\":\"world\"}\n```"
    assert _cleanup_agent_text(raw) == '{"hello":"world"}'


def test_cleanup_agent_text_extracts_first_object_from_prose():
    raw = (
        "Sure, here is the merged result:\n"
        '{"memory_records":{"user":[],"global":[]}}\n'
        "Let me know if you need anything else."
    )
    cleaned = _cleanup_agent_text(raw)
    # Must be valid JSON the downstream parser can eat.
    assert json.loads(cleaned) == {"memory_records": {"user": [], "global": []}}


def test_extract_first_json_object_honors_strings_with_braces():
    # Nested braces inside string values must NOT prematurely close the scan.
    text = 'prefix {"s": "contains {like} this", "n": 1} trailing'
    extracted = _extract_first_json_object(text)
    assert extracted == '{"s": "contains {like} this", "n": 1}'
    assert json.loads(extracted) == {"s": "contains {like} this", "n": 1}


def test_extract_first_json_object_returns_none_for_unbalanced():
    assert _extract_first_json_object("no braces at all") is None
    assert _extract_first_json_object("{unclosed") is None


# --- command construction ---------------------------------------------------


def test_build_default_opencode_command_uses_file_attach_and_separator():
    cmd = _build_default_opencode_command("/tmp/payload.json", options={})
    # opencode 1.4.0 shape: message is positional, payload attached via --file,
    # JSON stream via --format json. If opencode ever changes these flags,
    # this test fails loudly rather than the integration falling back silently.
    assert cmd[:2] == ["opencode", "run"]
    assert "--format" in cmd and cmd[cmd.index("--format") + 1] == "json"
    assert "--file" in cmd and cmd[cmd.index("--file") + 1] == "/tmp/payload.json"
    assert "--dangerously-skip-permissions" in cmd  # needed for headless tools
    assert "--" in cmd
    # Prompt is the last arg and references the payload path so the agent
    # knows which file to read.
    assert cmd[-1] == _build_compile_prompt("/tmp/payload.json")
    assert "/tmp/payload.json" in cmd[-1]


def test_build_default_opencode_command_honors_env_overrides(monkeypatch):
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_OPENCODE_MODEL", "anthropic/claude-4-7")
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_OPENCODE_AGENT", "compile-only")
    cmd = _build_default_opencode_command("/tmp/p.json", options={})
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "anthropic/claude-4-7"
    assert "--agent" in cmd and cmd[cmd.index("--agent") + 1] == "compile-only"


def test_build_compile_prompt_mentions_schema_keys_and_path():
    prompt = _build_compile_prompt("/tmp/p.json")
    assert "/tmp/p.json" in prompt
    # Schema keys must appear verbatim; the parser keys them.
    for key in ("memory_records", "recall_records", "compiled_skills", "manifest_entries", "discarded_items"):
        assert key in prompt
    # "NOTHING else" wording is load-bearing: it's what keeps models from
    # prepending "Sure, here's your JSON:" prose. Keep the test sensitive
    # to its removal.
    assert "NOTHING else" in prompt


# --- payload temp-file lifecycle --------------------------------------------


def test_write_payload_tempfile_round_trip_and_cleanup():
    payload = {"schema_version": 1, "batch": [{"x": 1}], "unicode": "中文"}
    path = _write_payload_tempfile(payload)
    try:
        assert os.path.exists(path)
        assert path.endswith(".json")
        assert "csep-compile-" in os.path.basename(path)
        with open(path, encoding="utf-8") as fh:
            assert json.load(fh) == payload
    finally:
        os.unlink(path)


# --- integration: subprocess adapter against a mocked opencode --------------


def test_subprocess_invoker_happy_path_parses_real_event_stream(monkeypatch):
    """End-to-end shape check: AgentCompilerBackend → _subprocess_invoker →
    subprocess.run → event-stream parser → agent_io parser → artifacts.

    Uses a fake subprocess.run that returns a realistic opencode stream so the
    whole path runs (minus the actual LLM). This is the test the integration
    was missing before — a regression in any of the five steps above would
    have silently fallen back to script and never been noticed.
    """
    agent_output = {
        "memory_records": {
            "user": [{"summary": "u", "content": "from opencode"}],
            "global": [],
        },
        "recall_records": [],
        "compiled_skills": [],
        "manifest_entries": [],
        "discarded_items": [],
    }
    # opencode splits assistant text across 2 text events here to prove the
    # concatenator stitches them back together.
    raw = json.dumps(agent_output)
    mid = len(raw) // 2
    stream_lines = [
        json.dumps({"type": "step_start", "part": {"type": "step-start"}}),
        json.dumps({"type": "text", "part": {"type": "text", "text": raw[:mid]}}),
        json.dumps({"type": "text", "part": {"type": "text", "text": raw[mid:]}}),
        json.dumps({"type": "step_finish", "part": {"reason": "stop"}}),
        "Shell cwd was reset to /tmp",
    ]
    fake_stdout = "\n".join(stream_lines) + "\n"

    captured = {}

    class FakeProc:
        returncode = 0
        stdout = fake_stdout
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        # Prove the tempfile with the real payload was attached.
        file_idx = cmd.index("--file")
        payload_path = cmd[file_idx + 1]
        with open(payload_path, encoding="utf-8") as fh:
            captured["payload_on_disk"] = json.load(fh)
        return FakeProc()

    monkeypatch.setattr(backends.subprocess, "run", fake_run)
    # shutil.which must pass so the backend enters the real invoker path
    # (otherwise it short-circuits to the `opencode_unavailable` fallback).
    monkeypatch.setattr(backends.shutil, "which", lambda _: "/fake/opencode")

    backend = AgentCompilerBackend()
    artifacts = backend.compile([_envelope()], _context(), {"allow_fallback": True})

    assert artifacts.backend_name == "agent:opencode"
    assert artifacts.fallback_backend is None
    assert artifacts.memory_records["user"][0]["content"] == "from opencode"

    # Command shape assertions — if opencode's CLI ever changes, this fires.
    cmd = captured["cmd"]
    assert cmd[:2] == ["opencode", "run"]
    assert "--format" in cmd
    assert "--file" in cmd
    assert "--" in cmd

    # Payload content round-tripped through the temp file, proving we aren't
    # quietly sending an empty or truncated payload.
    assert captured["payload_on_disk"]["batch"][0]["suggestion_id"] == "sug-1"

    # Temp file must be cleaned up after a successful run.
    payload_path = cmd[cmd.index("--file") + 1]
    assert not os.path.exists(payload_path), "temp payload file was not cleaned up"


def test_subprocess_invoker_cleans_up_tempfile_on_non_zero_exit(monkeypatch):
    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "opencode: authentication required"

    captured_path = {}

    def fake_run(cmd, **kwargs):
        captured_path["path"] = cmd[cmd.index("--file") + 1]
        return FakeProc()

    monkeypatch.setattr(backends.subprocess, "run", fake_run)
    monkeypatch.setattr(backends.shutil, "which", lambda _: "/fake/opencode")

    backend = AgentCompilerBackend()
    artifacts = backend.compile([_envelope()], _context(), {"allow_fallback": True})

    # Falls back to script (that path is tested elsewhere — just assert it
    # didn't crash and the RuntimeError message got captured).
    assert artifacts.fallback_backend == "script"
    reasons = [item.get("reason") for item in artifacts.discarded_items]
    assert "agent_invoke_failed" in reasons
    # Temp file must be cleaned up even on failure, otherwise a flaky opencode
    # could fill /tmp over time.
    assert not os.path.exists(captured_path["path"])


def test_subprocess_invoker_treats_empty_text_output_as_failure(monkeypatch):
    # opencode sometimes emits only step events (e.g. model refused the
    # request). An empty assistant text is a genuine failure, not "agent
    # said to do nothing" — the latter would be an explicit empty-schema
    # JSON object. We surface empty text as agent_invoke_failed so fallback
    # kicks in.
    class FakeProc:
        returncode = 0
        stdout = json.dumps({"type": "step_finish", "part": {"reason": "stop"}}) + "\n"
        stderr = ""

    monkeypatch.setattr(backends.subprocess, "run", lambda cmd, **kw: FakeProc())
    monkeypatch.setattr(backends.shutil, "which", lambda _: "/fake/opencode")

    backend = AgentCompilerBackend()
    artifacts = backend.compile([_envelope()], _context(), {"allow_fallback": True})

    assert artifacts.fallback_backend == "script"
    reasons = [item.get("reason") for item in artifacts.discarded_items]
    assert "agent_invoke_failed" in reasons
    detail = next(item for item in artifacts.discarded_items if item.get("reason") == "agent_invoke_failed")["detail"]
    assert "no assistant text" in detail


def test_subprocess_invoker_strips_code_fence_from_opencode_output(monkeypatch):
    # Even with our "no code fence" prompt, some providers still wrap output.
    # The adapter must cope — otherwise a wrapped valid JSON still gets
    # rejected and we fall back to script unnecessarily.
    wrapped = "```json\n" + json.dumps(
        {
            "memory_records": {"user": [], "global": []},
            "recall_records": [],
            "compiled_skills": [],
            "manifest_entries": [],
            "discarded_items": [],
        }
    ) + "\n```"
    stream = "\n".join(
        [
            json.dumps({"type": "text", "part": {"type": "text", "text": wrapped}}),
            json.dumps({"type": "step_finish", "part": {"reason": "stop"}}),
        ]
    )

    class FakeProc:
        returncode = 0
        stdout = stream
        stderr = ""

    monkeypatch.setattr(backends.subprocess, "run", lambda cmd, **kw: FakeProc())
    monkeypatch.setattr(backends.shutil, "which", lambda _: "/fake/opencode")

    backend = AgentCompilerBackend()
    artifacts = backend.compile([_envelope()], _context(), {"allow_fallback": True})

    assert artifacts.fallback_backend is None
    assert artifacts.backend_name == "agent:opencode"

"""Codex SessionStart hook wiring: format helper + --from-stdin CLI path.

P0-0 research (see docs/todo.md 2026-04-21) confirmed codex-cli 0.122.0
honors ``hookSpecificOutput.additionalContext`` as ``DeveloperInstructions``
injected into the session. These tests lock in:

1. The ``format_session_start_for_codex`` helper produces the exact JSON
   shape Codex expects — a regression here means ``additionalContext``
   silently stops being injected.
2. The ``--from-stdin`` CLI path reads cwd from the Codex hook payload
   (falling back to ``--cwd`` for shell testing), and never returns
   non-zero / invalid JSON even on malformed input — SessionStart hooks
   must NEVER block session startup.
"""
from __future__ import annotations

import json
import sys
from io import StringIO

import pytest

from codex_self_evolution import cli
from codex_self_evolution.compiler.engine import apply_compiler_outputs
from codex_self_evolution.hooks.session_start import (
    format_session_start_for_codex,
    session_start,
)


# ---- format helper ---------------------------------------------------------


def _seed_state(tmp_path, *, user="Prefer concise answers.", memory="Focused tests first."):
    state = tmp_path / "state"
    apply_compiler_outputs(
        memory_dir=state / "memory",
        recall_dir=state / "recall",
        skills_dir=state / "skills",
        memory_records={
            "user": [{"summary": "User pref", "content": user}],
            "global": [{"summary": "Repo fact", "content": memory}],
        },
        recall_records=[],
        compiled_skills=[],
        manifest_entries=[],
        existing_entries=[],
    )
    return state


def test_format_wraps_into_codex_hookSpecificOutput_shape(tmp_path):
    state = _seed_state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    session_result = session_start(cwd=repo, state_dir=state)

    codex_output = format_session_start_for_codex(session_result)

    # Exact shape Codex's hook output_parser reads. Any typo here means
    # Codex falls back to the "unknown JSON" path and the context is
    # dropped — see codex-rs/hooks/src/events/session_start.rs line 193-198.
    assert set(codex_output.keys()) == {"hookSpecificOutput"}
    hso = codex_output["hookSpecificOutput"]
    assert hso["hookEventName"] == "SessionStart"
    assert isinstance(hso["additionalContext"], str)
    # Valid JSON end-to-end so codex's JSON parser doesn't fall back to the
    # "invalid session start JSON" Failed branch.
    json.loads(json.dumps(codex_output))


def test_format_includes_user_memory_and_recall_policy(tmp_path):
    state = _seed_state(tmp_path, user="Prefer concise.", memory="Run focused tests first.")
    repo = tmp_path / "repo"
    repo.mkdir()
    result = session_start(cwd=repo, state_dir=state)

    ac = format_session_start_for_codex(result)["hookSpecificOutput"]["additionalContext"]

    # Stable background (USER.md + MEMORY.md + session_recall skill)
    assert "Prefer concise." in ac
    assert "Run focused tests first." in ac
    assert "Session Recall Skill" in ac
    assert "Recall Contract" in ac
    # Recall policy is appended so the model knows recall is on-demand and
    # knows the exact CLI invocation — design doc §4.1 requires both the
    # "stable prefix" and the "recall control layer" to reach the session.
    assert "Recall Policy" in ac
    assert "csep recall" in ac


def test_format_handles_empty_session_gracefully():
    # Fresh machine, no memory or policy loaded yet. Helper must not crash
    # and must still emit valid Codex shape (additionalContext just empty).
    codex_output = format_session_start_for_codex({})
    assert codex_output == {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "",
        }
    }


def test_format_tolerates_none_sub_objects():
    # Defensive: in case session_start() ever returns None for a subtree.
    codex_output = format_session_start_for_codex({"stable_background": None, "recall": None})
    assert codex_output["hookSpecificOutput"]["additionalContext"] == ""


# ---- --from-stdin CLI path -------------------------------------------------


def _codex_session_start_payload(cwd: str) -> dict:
    # Matches Codex CLI 0.122.0's SessionStart input schema
    # (codex-rs/hooks/schema/generated/session-start.command.input.schema.json).
    return {
        "session_id": "019daf12-3456-7000-89ab-cdef01234567",
        "transcript_path": "/tmp/codex-transcript.jsonl",
        "cwd": cwd,
        "hook_event_name": "SessionStart",
        "model": "gpt-5.4",
        "source": "startup",
    }


def test_from_stdin_reads_cwd_from_codex_payload(monkeypatch, capsys, tmp_path):
    state = _seed_state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _codex_session_start_payload(str(repo))
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(payload)))

    exit_code = cli.main(["session-start", "--from-stdin", "--state-dir", str(state)])
    assert exit_code == 0

    out = json.loads(capsys.readouterr().out.strip())
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Prefer concise" in out["hookSpecificOutput"]["additionalContext"]


def test_recall_trigger_defaults_to_markdown(monkeypatch, capsys, tmp_path):
    state = _seed_state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))

    exit_code = cli.main([
        "recall-trigger",
        "--query",
        "remember previous workflow",
        "--cwd",
        str(repo),
        "--state-dir",
        str(state),
    ])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert out.startswith("## Focused Recall")
    assert "Status: no_match" in out


def test_recall_trigger_json_format(monkeypatch, capsys, tmp_path):
    state = _seed_state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))

    cli.main([
        "recall-trigger",
        "--query",
        "remember previous workflow",
        "--cwd",
        str(repo),
        "--state-dir",
        str(state),
        "--format",
        "json",
    ])

    out = json.loads(capsys.readouterr().out)
    assert out["triggered"] is True
    assert out["count"] == 0


def test_from_stdin_falls_back_to_cli_cwd_when_payload_missing_cwd(monkeypatch, capsys, tmp_path):
    # Covers the shell-test case: `echo '{}' | codex-self-evolution session-start
    # --from-stdin --cwd /path/to/repo`. Real Codex always sends cwd, but this
    # fallback is what makes the hook easy to smoke-test manually.
    state = _seed_state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(sys, "stdin", StringIO("{}"))

    cli.main(["session-start", "--from-stdin", "--cwd", str(repo), "--state-dir", str(state)])

    out = json.loads(capsys.readouterr().out.strip())
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_from_stdin_returns_continue_true_on_malformed_json(monkeypatch, capsys):
    # A SessionStart hook that blocks breaks `codex` startup for the user.
    # We MUST emit valid JSON + continue:true on any parse error so Codex
    # treats this as "no context injected" rather than "hook failed".
    monkeypatch.setattr(sys, "stdin", StringIO("not json at all"))

    exit_code = cli.main(["session-start", "--from-stdin"])
    assert exit_code == 0

    out = json.loads(capsys.readouterr().out.strip())
    assert out["continue"] is True
    assert "warning" in out


def test_from_stdin_returns_continue_true_when_payload_is_not_object(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", StringIO("[1,2,3]"))

    cli.main(["session-start", "--from-stdin"])

    out = json.loads(capsys.readouterr().out.strip())
    assert out["continue"] is True


def test_from_stdin_returns_continue_true_when_no_cwd_anywhere(monkeypatch, capsys):
    # Pathological case: Codex sent a payload with no cwd (shouldn't happen in
    # practice, but if the field is ever renamed we don't want to crash).
    monkeypatch.setattr(sys, "stdin", StringIO("{}"))

    cli.main(["session-start", "--from-stdin"])

    out = json.loads(capsys.readouterr().out.strip())
    assert out["continue"] is True
    assert "no cwd" in out["warning"]


def test_from_stdin_returns_continue_true_when_session_start_raises(monkeypatch, capsys, tmp_path):
    # If build_paths fails / memory files unreadable / anything raises,
    # the hook still returns continue:true. Otherwise codex session is
    # blocked — unacceptable for a "peripheral" plugin.
    monkeypatch.setattr(
        "codex_self_evolution.cli.session_start",
        lambda **_: (_ for _ in ()).throw(RuntimeError("disk on fire")),
    )
    payload = _codex_session_start_payload(str(tmp_path))
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(payload)))

    cli.main(["session-start", "--from-stdin"])

    out = json.loads(capsys.readouterr().out.strip())
    assert out["continue"] is True
    assert "disk on fire" in out["warning"]


def test_session_start_without_cwd_and_without_from_stdin_errors():
    # Old CLI shape required --cwd. We relaxed that so --from-stdin works
    # without --cwd, but forgetting both must still be an error rather than
    # silently using cwd=None (which would pick up pytest's cwd in tests
    # and write to the wrong bucket).
    with pytest.raises(SystemExit):
        cli.main(["session-start"])

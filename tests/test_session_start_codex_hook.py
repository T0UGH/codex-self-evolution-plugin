"""Codex SessionStart hook wiring: format helper + --from-stdin CLI path.

Local verification against codex-cli 0.122.0 confirmed that
``hookSpecificOutput.additionalContext`` is injected as
``DeveloperInstructions``. These tests lock in:

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

from codex_self_evolution import cli, csep
from codex_self_evolution.hooks.session_start import (
    format_session_start_for_codex,
    session_start,
)


# ---- format helper ---------------------------------------------------------


def _seed_state(tmp_path, *, user="Prefer concise answers.", memory="Focused tests first."):
    """Seed retained memory files for SessionStart tests."""
    state = tmp_path / "state"
    (state / "memory").mkdir(parents=True)
    (state / "memory" / "USER.md").write_text(f"# USER\n\n{user}\n", encoding="utf-8")
    (state / "memory" / "MEMORY.md").write_text(f"# MEMORY\n\n{memory}\n", encoding="utf-8")
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


def test_format_includes_stable_memory_and_strong_recall_policy(tmp_path):
    state = _seed_state(tmp_path, user="Prefer concise.", memory="Run focused tests first.")
    repo = tmp_path / "repo"
    repo.mkdir()
    result = session_start(cwd=repo, state_dir=state)

    ac = format_session_start_for_codex(result)["hookSpecificOutput"]["additionalContext"]

    # Stable background is memory plus a startup policy that makes recall a
    # first-class self-check, while still leaving execution to the agent.
    assert "Prefer concise." not in ac
    assert "Run focused tests first." in ac
    assert "Session Recall Skill" not in ac
    assert "Recall Contract" not in ac
    assert "Recall Policy" in ac
    assert "csep-session-recall" in ac
    assert "Before answering or taking action" in ac
    assert "When unsure, use recall" in ac
    assert "Skip recall only when" in ac


def test_format_omits_disabled_stable_memory_and_recall(tmp_path):
    state = _seed_state(tmp_path, memory="Do not inject when disabled.")
    (state / "config.toml").write_text(
        """
schema_version = 2

[stable_memory]
enabled = false

[session_recall]
enabled = false
""",
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    repo.mkdir()

    result = session_start(cwd=repo, state_dir=state)
    ac = format_session_start_for_codex(result)["hookSpecificOutput"]["additionalContext"]

    assert ac == ""


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
    assert "Focused tests first" in out["hookSpecificOutput"]["additionalContext"]
    assert "Prefer concise" not in out["hookSpecificOutput"]["additionalContext"]


def test_csep_recall_defaults_to_markdown(monkeypatch, capsys, tmp_path):
    state = _seed_state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps({"role": "user", "content": "remember previous workflow from session recall"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))

    assert csep.main([
        "session-archive",
        "--transcript-path",
        str(transcript),
        "--cwd",
        str(repo),
        "--session-id",
        "s1",
        "--state-dir",
        str(state),
    ]) == 0
    capsys.readouterr()

    exit_code = csep.main([
        "recall",
        "remember previous workflow",
        "--cwd",
        str(repo),
        "--state-dir",
        str(state),
    ])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert out.startswith("## Focused Recall")
    assert "Status: matched" in out
    assert "remember previous workflow from session recall" in out


def test_csep_recall_json_format(monkeypatch, capsys, tmp_path):
    state = _seed_state(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps({"role": "user", "content": "remember previous workflow json"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))

    assert csep.main([
        "session-archive",
        "--transcript-path",
        str(transcript),
        "--cwd",
        str(repo),
        "--session-id",
        "s1",
        "--state-dir",
        str(state),
    ]) == 0
    capsys.readouterr()

    csep.main([
        "recall",
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
    assert out["count"] == 1
    assert out["results"][0]["messages"][0]["content"] == "remember previous workflow json"


def test_from_stdin_falls_back_to_cli_cwd_when_payload_missing_cwd(monkeypatch, capsys, tmp_path):
    # Covers the shell-test case: `echo '{}' | csep session-start
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

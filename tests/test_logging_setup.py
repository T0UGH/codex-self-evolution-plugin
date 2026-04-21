"""Plugin-wide structured logging.

These tests guard three properties that matter when an install is actually
running unattended (Stop hook / launchd scheduler), because that's exactly
when users stop having a terminal to read stderr:

1. **Log lines are valid JSON** — consumers pipe through ``jq`` / log
   shippers; a malformed line corrupts everything after it.
2. **Every CLI invocation leaves exactly one summary line** — if this
   regresses (dupes from reconfiguring, or silent drops from a crashed
   handler), the "did the scheduler run?" question becomes unanswerable.
3. **Failed commands get logged BEFORE re-raising** — the CLI is typically
   invoked by a hook/launchd runner that captures stderr somewhere lossy.
   The persistent file log must be the primary evidence trail.
"""
from __future__ import annotations

import json
import logging
from io import StringIO

import pytest

from codex_self_evolution import cli, logging_setup
from codex_self_evolution.config import HOME_DIR_ENV


def _log_path(tmp_path):
    # _isolate_plugin_logs fixture already sets HOME to tmp_path/csep-test-home.
    return tmp_path / "csep-test-home" / "logs" / "plugin.log"


# ---------- JsonFormatter ------------------------------------------------


def test_json_formatter_emits_valid_jsonl():
    record = logging.LogRecord(
        name="codex_self_evolution",
        level=logging.INFO,
        pathname="/fake",
        lineno=10,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    # `extra=` fields land as record attributes; simulate that.
    record.kind = "stop-review"
    record.exit_code = 0
    record.duration_ms = 42

    out = logging_setup.JsonFormatter().format(record)
    parsed = json.loads(out)

    assert parsed["msg"] == "hello world"
    assert parsed["level"] == "INFO"
    assert parsed["kind"] == "stop-review"
    assert parsed["exit_code"] == 0
    assert parsed["duration_ms"] == 42
    # ts field must be ISO-8601 with Z suffix — scheduler grep-ability relies
    # on this stable format. Changing it breaks downstream log shippers.
    assert parsed["ts"].endswith("Z")


def test_json_formatter_stringifies_non_json_extras():
    # Non-serializable extras (like a Path or a custom object) mustn't break
    # the line — the fallback is str(value). Better an imperfect log line
    # than a crash that loses the whole record.
    record = logging.LogRecord(
        name="codex_self_evolution", level=logging.INFO,
        pathname="/fake", lineno=1, msg="x", args=(), exc_info=None,
    )
    record.path = object()  # not JSON-serializable

    line = logging_setup.JsonFormatter().format(record)
    parsed = json.loads(line)
    assert isinstance(parsed["path"], str)


def test_json_formatter_includes_exc_info():
    try:
        raise RuntimeError("detonation")
    except RuntimeError:
        import sys
        record = logging.LogRecord(
            name="codex_self_evolution", level=logging.ERROR,
            pathname="/fake", lineno=1, msg="boom", args=(), exc_info=sys.exc_info(),
        )
    out = logging_setup.JsonFormatter().format(record)
    parsed = json.loads(out)
    # The exception traceback is embedded — without this, debugging a silent
    # reviewer failure means reading the Stop hook's subprocess output log
    # which may not exist.
    assert "detonation" in parsed["exc"]


# ---------- configure() idempotency --------------------------------------


def test_configure_installs_rotating_file_handler(tmp_path, monkeypatch):
    monkeypatch.setenv(HOME_DIR_ENV, str(tmp_path))
    logger = logging_setup.configure()
    # Exactly one handler (no dupes from repeated configure() calls).
    assert len(logger.handlers) == 1
    handler = logger.handlers[0]
    assert isinstance(handler, logging.handlers.TimedRotatingFileHandler)
    # Log destination is under <home>/logs/, not in the user's real home.
    # If this assertion fails, pytest is writing logs to the actual user's
    # machine — that's how we caught the original bug.
    assert str(tmp_path / "logs" / "plugin.log") == handler.baseFilename


def test_configure_is_idempotent_no_handler_leak(tmp_path, monkeypatch):
    monkeypatch.setenv(HOME_DIR_ENV, str(tmp_path))
    logger = logging_setup.configure()
    logger = logging_setup.configure()
    logger = logging_setup.configure()
    # Still exactly one — regressions here cause one log line to be written
    # N times per invocation, which silently inflates log volume.
    assert len(logger.handlers) == 1


def test_configure_falls_back_to_stderr_when_log_dir_unwritable(tmp_path, monkeypatch):
    # /dev/null/xxx can't become a directory. Logger must not crash the CLI;
    # it should degrade to stderr so output isn't completely lost.
    monkeypatch.setenv(HOME_DIR_ENV, "/dev/null/cannot-create")
    logger = logging_setup.configure()
    # StreamHandler, not TimedRotatingFileHandler — we're in fallback mode.
    assert any(
        isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.handlers.TimedRotatingFileHandler)
        for h in logger.handlers
    )


# ---------- cli.main writes exactly one summary line --------------------


def test_cli_main_logs_success_summary(tmp_path, capsys):
    # session-start is the simplest no-arg path. Read the log file after to
    # confirm one line got emitted with kind=session-start and exit_code=0.
    repo = tmp_path / "repo"
    repo.mkdir()
    exit_code = cli.main(["session-start", "--cwd", str(repo)])
    assert exit_code == 0
    # Drain stdout so pytest's capfd doesn't complain about unread output.
    capsys.readouterr()

    log = _log_path(tmp_path)
    lines = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    # Exactly one summary record for this invocation. If this starts
    # returning >1, some code path is double-logging; if 0, log capture
    # broke.
    assert len(lines) == 1
    assert lines[0]["kind"] == "session-start"
    assert lines[0]["exit_code"] == 0
    assert "duration_ms" in lines[0]


def test_cli_main_logs_failure_before_reraising(tmp_path, monkeypatch, capsys):
    # Force session_start to blow up — the log MUST capture the error
    # summary even though main() re-raises. This is the property that
    # makes the log file the primary post-mortem trail for silent
    # hook/scheduler failures.
    def boom(**_):
        raise RuntimeError("simulated disk failure")

    monkeypatch.setattr("codex_self_evolution.cli.session_start", boom)

    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(RuntimeError, match="simulated disk failure"):
        cli.main(["session-start", "--cwd", str(repo)])

    log = _log_path(tmp_path)
    # At least one line should show the failure. The main() invocation
    # happened and died; the log must reflect that.
    lines = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0]["exit_code"] == 1
    assert lines[0]["error_type"] == "RuntimeError"
    assert "simulated disk failure" in lines[0]["error_message"]


def test_cli_main_does_not_log_argparse_systemexit(tmp_path):
    # Bad args → argparse prints usage to stderr and exits 2. We SHOULD NOT
    # log anything because argparse already handled user communication and
    # the user's actual command hasn't executed.
    with pytest.raises(SystemExit):
        cli.main(["session-start"])  # missing --cwd AND --from-stdin

    log = _log_path(tmp_path)
    # File may not even exist (configure() does create the dir, but no log
    # was emitted). Either way it should be empty.
    if log.exists():
        assert log.read_text().strip() == ""


def test_cli_main_logs_from_stdin_variant(tmp_path, monkeypatch):
    # --from-stdin takes a different code path (returns early), so ensure it
    # also leaves a log trail. Silent from_stdin runs would hide the
    # "did my Stop hook fire?" signal for the entire plugin.
    payload = json.dumps({
        "cwd": str(tmp_path), "hook_event_name": "SessionStart",
        "session_id": "s", "model": "m", "source": "startup",
    })
    monkeypatch.setattr("sys.stdin", StringIO(payload))

    cli.main(["session-start", "--from-stdin"])

    log = _log_path(tmp_path)
    lines = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0]["kind"] == "session-start"
    assert lines[0]["exit_code"] == 0

"""Plugin-wide structured logging — append one JSON line per CLI invocation.

Why this exists: before P1-6 the plugin emitted almost no log output beyond
the final JSON result on stdout. When a Stop hook reviewer failed mid-flight
(401, parse error, model refused, etc.), the evidence vanished with the
subprocess — users only noticed that pending/ stopped growing.

Design decisions:

- **Single destination file** ``<home>/logs/plugin.log``, rotated daily
  (``TimedRotatingFileHandler``, 14 days retention). One file is easier
  to ``tail`` / ``jq`` than a maze of per-command files, and scheduler
  frequency (5 min default) is low enough that even a busy install fits
  comfortably in one day's log.
- **JSON lines** — readable by humans with ``tail``, trivially filterable
  with ``jq '. | select(.kind=="stop-review")'``. No log-framework
  dependency; stdlib ``logging`` + a 20-line formatter.
- **Logger reset on every ``configure()`` call** so tests can redirect
  logs per-tmp_path without fixture gymnastics. The CLI is short-lived
  (< a second per invocation) so rebuilding the handler costs nothing.
- **Record one summary line per CLI command** in ``cli.main()`` — that's
  where the high-signal "did this invocation work?" information lives.
  Deeper per-step logging (inside compile, reviewer, etc.) is intentionally
  NOT added yet — start with the boundary, push inward only if an actual
  investigation needs more.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import get_home_dir

LOGGER_NAME = "codex_self_evolution"
LOG_FILENAME = "plugin.log"
DEFAULT_RETENTION_DAYS = 14

# Fields that every logging.LogRecord carries but are noise to us; we strip
# them from the emitted JSON so only the intentional `extra=` fields and the
# core {ts,level,kind,msg} survive.
_RESERVED_LOGRECORD_ATTRS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "taskName",
})


class JsonFormatter(logging.Formatter):
    """Serialize log records as compact JSON objects, one per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            ),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        # Anything passed via logger.info(..., extra={"foo": 1}) ends up as
        # a record attribute — copy whichever extras we can JSON-encode.
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOGRECORD_ATTRS or key in payload:
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                value = str(value)
            payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure(home: str | Path | None = None) -> logging.Logger:
    """Install (or reset) a JSON-lines file handler for the plugin logger.

    Idempotent: subsequent calls close any existing handlers first so test
    harnesses and repeated CLI invocations don't accumulate handlers. The
    ``home`` arg exists for tests to point logs at ``tmp_path`` instead
    of the user's real home dir.
    """
    logger = logging.getLogger(LOGGER_NAME)
    # Swap handlers atomically; don't leak FDs from previous invocations.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001 — cleanup must never raise
            pass

    home_dir = Path(home).expanduser().resolve() if home else get_home_dir()
    log_dir = home_dir / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Disk full / permission denied: log to stderr rather than blowing
        # up the whole CLI. The plugin itself still works.
        stderr = logging.StreamHandler()
        stderr.setFormatter(JsonFormatter())
        logger.addHandler(stderr)
        logger.setLevel(logging.INFO)
        return logger

    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_dir / LOG_FILENAME,
        when="midnight",
        backupCount=DEFAULT_RETENTION_DAYS,
        encoding="utf-8",
    )
    file_handler.setFormatter(JsonFormatter())
    logger.addHandler(file_handler)
    logger.setLevel(logging.INFO)
    # Don't bubble up to the root logger — avoids double-printing if the
    # host process (tests, Codex hook runner) attaches its own stderr handler.
    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    """Return the plugin logger. Safe to call before ``configure()`` —
    logging.getLogger is a noop without handlers, so early log calls just
    vanish rather than crashing."""
    return logging.getLogger(LOGGER_NAME)

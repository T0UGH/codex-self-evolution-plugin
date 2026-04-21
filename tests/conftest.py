"""Shared pytest fixtures.

``_isolate_plugin_logs`` (autouse): redirects the plugin's file logger to a
tmp path for every test, so running ``pytest`` doesn't scribble 100+ entries
into the user's real ``~/.codex-self-evolution/logs/plugin.log``. Without
this the test suite quietly pollutes whatever machine it runs on.
"""
from __future__ import annotations

import logging

import pytest

from codex_self_evolution.config import HOME_DIR_ENV
from codex_self_evolution.logging_setup import LOGGER_NAME


@pytest.fixture(autouse=True)
def _isolate_plugin_logs(tmp_path, monkeypatch):
    # Point the plugin at a tmp home so any CLI invocation during the test
    # writes logs into tmp_path instead of the user's real home. Tests that
    # need a different home override it explicitly via their own
    # monkeypatch.setenv — the last-set wins.
    monkeypatch.setenv(HOME_DIR_ENV, str(tmp_path / "csep-test-home"))
    yield
    # Close any file handler the test opened; otherwise pytest's tmp_path
    # cleanup fails on Windows (and leaks fds even on Unix).
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001
            pass

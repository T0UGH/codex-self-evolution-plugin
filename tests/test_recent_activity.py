"""status.recent_activity rolls plugin.log into a single health snapshot.

Without this the user has to grep/jq plugin.log every time they want to
answer "did Phase 1 actually work last week". These tests pin the roll-up
semantics: foreground stop-review ACKs don't double-count, window-cutoff
discards older lines, malformed lines survive, retry INFO lines feed the
retry bucket.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from codex_self_evolution.diagnostics import collect_status


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _write_log(home: Path, entries: list[dict]) -> None:
    log_dir = home / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "plugin.log").write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n",
        encoding="utf-8",
    )


def test_recent_activity_empty_when_no_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    status = collect_status()
    activity = status["recent_activity"]
    assert activity["log_available"] is False
    assert activity["stop_review"]["total"] == 0
    assert activity["scan"]["total"] == 0
    assert activity["retries"]["total"] == 0


def test_recent_activity_skips_from_stdin_foreground_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The foreground ``mode=from_stdin`` hook acknowledgement always exits 0
    and never touches MiniMax — counting it would falsely inflate the
    reviewer-success rate. Only the background child carries real signal.
    """
    now = datetime.now(timezone.utc)
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    _write_log(
        tmp_path,
        [
            {"ts": _iso(now), "level": "INFO", "msg": "done",
             "kind": "stop-review", "exit_code": 0, "duration_ms": 5, "mode": "from_stdin"},
            {"ts": _iso(now), "level": "INFO", "msg": "done",
             "kind": "stop-review", "exit_code": 0, "duration_ms": 18000,
             "reviewer_provider": "minimax", "suggestion_count": 3,
             "suggestion_families": {"memory_updates": 2, "recall_candidate": 1, "skill_action": 0},
             "skipped_suggestion_count": 0},
        ],
    )
    activity = collect_status()["recent_activity"]
    assert activity["stop_review"]["total"] == 1  # foreground skipped
    assert activity["stop_review"]["succeeded"] == 1
    assert activity["stop_review"]["suggestions_emitted"] == 3
    assert activity["stop_review"]["families"] == {
        "memory_updates": 2, "recall_candidate": 1, "skill_action": 0,
    }


def test_recent_activity_buckets_errors_by_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    _write_log(
        tmp_path,
        [
            {"ts": _iso(now), "level": "INFO", "msg": "err",
             "kind": "stop-review", "exit_code": 1, "duration_ms": 30000,
             "error_type": "TimeoutError"},
            {"ts": _iso(now), "level": "INFO", "msg": "err",
             "kind": "stop-review", "exit_code": 1, "duration_ms": 800,
             "error_type": "ReviewProviderError"},
            {"ts": _iso(now), "level": "INFO", "msg": "err",
             "kind": "stop-review", "exit_code": 1, "duration_ms": 900,
             "error_type": "ReviewProviderError"},
        ],
    )
    stop = collect_status()["recent_activity"]["stop_review"]
    assert stop["failed"] == 3
    assert stop["succeeded"] == 0
    assert stop["by_error_type"] == {"TimeoutError": 1, "ReviewProviderError": 2}


def test_recent_activity_counts_retries_by_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry lines are INFO messages from the provider module, not keyed by
    ``kind``. We classify them by the HTTP code or ``timeout`` substring
    so the dashboard can tell 529 storms apart from network flakiness."""
    now = datetime.now(timezone.utc)
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    _write_log(
        tmp_path,
        [
            {"ts": _iso(now), "level": "INFO",
             "msg": "minimax retrying after HTTP 529 (attempt 1/3)"},
            {"ts": _iso(now), "level": "INFO",
             "msg": "minimax retrying after HTTP 529 (attempt 2/3)"},
            {"ts": _iso(now), "level": "INFO",
             "msg": "minimax retrying after raw timeout (attempt 1/3)"},
        ],
    )
    retries = collect_status()["recent_activity"]["retries"]
    assert retries["total"] == 3
    assert retries["by_reason"] == {"HTTP 529": 2, "timeout": 1}


def test_recent_activity_rolls_up_scan_aggregates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    _write_log(
        tmp_path,
        [
            # Skip-empty scan: counted as total but not in with_processed.
            {"ts": _iso(now), "level": "INFO", "msg": "done",
             "kind": "scan", "exit_code": 0, "duration_ms": 5},
            # Scan with actual work.
            {"ts": _iso(now), "level": "INFO", "msg": "done",
             "kind": "scan", "exit_code": 0, "duration_ms": 5000,
             "aggregate": {
                 "buckets_processed": 2, "buckets_with_fallback": 1,
                 "total_memory_suggestions": 5, "total_discarded": 1,
                 "actions": {"add": 3, "replace": 1, "remove": 1},
                 "scopes": {"user": 1, "global": 4},
             }},
            # Another scan: no fallback this time, scope distribution grows.
            {"ts": _iso(now), "level": "INFO", "msg": "done",
             "kind": "scan", "exit_code": 0, "duration_ms": 3000,
             "aggregate": {
                 "buckets_processed": 3, "buckets_with_fallback": 0,
                 "total_memory_suggestions": 2, "total_discarded": 0,
                 "actions": {"add": 2, "replace": 0, "remove": 0},
                 "scopes": {"user": 0, "global": 2},
             }},
        ],
    )
    scan = collect_status()["recent_activity"]["scan"]
    assert scan["total"] == 3
    assert scan["with_processed_buckets"] == 2
    assert scan["with_fallback"] == 1
    assert scan["suggestions"] == 7
    assert scan["discarded"] == 1
    assert scan["memory_actions"] == {"add": 5, "replace": 1, "remove": 1}
    assert scan["scopes"] == {"user": 1, "global": 6}


def test_recent_activity_ignores_entries_older_than_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 24h default window means a stop-review from 2 days ago MUST NOT be
    counted — otherwise the dashboard shows stale fallback noise long after
    the underlying bug was fixed."""
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=48)
    fresh = now - timedelta(hours=1)
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    _write_log(
        tmp_path,
        [
            {"ts": _iso(old), "level": "INFO", "msg": "old",
             "kind": "stop-review", "exit_code": 1, "error_type": "OldError"},
            {"ts": _iso(fresh), "level": "INFO", "msg": "new",
             "kind": "stop-review", "exit_code": 0, "suggestion_count": 2,
             "suggestion_families": {"memory_updates": 2, "recall_candidate": 0, "skill_action": 0}},
        ],
    )
    stop = collect_status()["recent_activity"]["stop_review"]
    assert stop["total"] == 1
    assert stop["succeeded"] == 1
    assert "OldError" not in stop["by_error_type"]


def test_recent_activity_tolerates_malformed_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One corrupt line must not abort the whole summary."""
    now = datetime.now(timezone.utc)
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    log_path = tmp_path / "logs" / "plugin.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "\n".join([
            "garbled-nonsense-first-line",
            json.dumps({"ts": _iso(now), "level": "INFO", "msg": "ok",
                        "kind": "stop-review", "exit_code": 0,
                        "suggestion_count": 1,
                        "suggestion_families": {"memory_updates": 1, "recall_candidate": 0, "skill_action": 0}}),
            "{unclosed-json",
        ]) + "\n",
        encoding="utf-8",
    )
    stop = collect_status()["recent_activity"]["stop_review"]
    assert stop["total"] == 1
    assert stop["succeeded"] == 1
    assert stop["suggestions_emitted"] == 1


def test_status_bucket_surfaces_memory_action_stats(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each bucket's last_receipt summary now carries the action stats too,
    so a single ``status`` call shows both recent-activity aggregate AND
    the per-bucket last-observed breakdown."""
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path))
    bucket = tmp_path / "projects" / "-tmp-repo" / "compiler"
    bucket.mkdir(parents=True)
    (bucket / "last_receipt.json").write_text(json.dumps({
        "run_status": "success",
        "backend": "agent:opencode",
        "processed_count": 1,
        "archived_count": 1,
        "memory_records": 2,
        "recall_records": 0,
        "managed_skills": 0,
        "memory_action_stats": {
            "total": 2,
            "by_action": {"add": 1, "replace": 1, "remove": 0},
            "by_scope": {"user": 1, "global": 1},
        },
    }))
    status = collect_status()
    bucket_entry = next(b for b in status["buckets"] if b["project"] == "-tmp-repo")
    assert bucket_entry["last_receipt"]["memory_action_stats"] == {
        "total": 2,
        "by_action": {"add": 1, "replace": 1, "remove": 0},
        "by_scope": {"user": 1, "global": 1},
    }

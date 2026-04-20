"""Compile lock protection: pid liveness, negative age, and hard stale cap."""

import json
import os

from codex_self_evolution.config import DEFAULT_LOCK_STALE_SECONDS, build_paths
from codex_self_evolution.storage import (
    CompileLockError,
    compiler_lock_path,
    file_lock,
    lock_status,
)


def _write_lock(paths, *, pid: int, created_at: str) -> None:
    lock_path = compiler_lock_path(paths)
    lock_path.write_text(json.dumps({"created_at": created_at, "pid": pid}), encoding="utf-8")


def test_lock_status_reports_unlocked_when_no_file(tmp_path):
    paths = build_paths(repo_root=tmp_path / "repo", state_dir=tmp_path / "state")
    status = lock_status(paths)
    assert status["locked"] is False
    assert status["stale"] is False


def test_lock_status_negative_age_is_stale(tmp_path):
    paths = build_paths(repo_root=tmp_path / "repo", state_dir=tmp_path / "state")
    _write_lock(paths, pid=os.getpid(), created_at="2099-01-01T00:00:00Z")
    status = lock_status(paths)
    assert status["locked"] is True
    assert status["stale"] is True
    assert status["stale_reason"] == "negative_age"


def test_lock_status_dead_pid_is_stale_immediately(tmp_path):
    paths = build_paths(repo_root=tmp_path / "repo", state_dir=tmp_path / "state")
    # PID 2**31 - 1 is effectively impossible to match a live process on macOS/Linux.
    _write_lock(paths, pid=2_147_483_646, created_at="2026-04-20T00:00:00Z")
    status = lock_status(paths)
    assert status["locked"] is True
    assert status["stale"] is True
    assert status["pid_alive"] is False
    assert status["stale_reason"] == "pid_not_alive"


def test_lock_status_live_pid_with_recent_timestamp_is_not_stale(tmp_path):
    paths = build_paths(repo_root=tmp_path / "repo", state_dir=tmp_path / "state")
    # Use the current test process PID (definitely alive) and a timestamp
    # within the 30-minute window.
    from codex_self_evolution.storage import utc_now

    ts = utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _write_lock(paths, pid=os.getpid(), created_at=ts)
    status = lock_status(paths)
    assert status["locked"] is True
    assert status["stale"] is False
    assert status["pid_alive"] is True
    assert status["stale_reason"] is None


def test_lock_status_exceeds_max_age_is_stale(tmp_path):
    paths = build_paths(repo_root=tmp_path / "repo", state_dir=tmp_path / "state")
    _write_lock(paths, pid=os.getpid(), created_at="2000-01-01T00:00:00Z")
    status = lock_status(paths)
    assert status["locked"] is True
    assert status["stale"] is True
    assert status["stale_reason"] == "exceeded_max_age"


def test_file_lock_reclaims_stale_lock_from_dead_pid(tmp_path):
    paths = build_paths(repo_root=tmp_path / "repo", state_dir=tmp_path / "state")
    _write_lock(paths, pid=2_147_483_646, created_at="2026-04-20T00:00:00Z")
    with file_lock(paths) as active_path:
        assert active_path.exists()
        # New lock owns the current pid.
        stored = json.loads(active_path.read_text(encoding="utf-8"))
        assert stored["pid"] == os.getpid()
    assert not active_path.exists()


def test_file_lock_refuses_to_steal_from_live_pid_in_window(tmp_path):
    paths = build_paths(repo_root=tmp_path / "repo", state_dir=tmp_path / "state")
    from codex_self_evolution.storage import utc_now

    ts = utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _write_lock(paths, pid=os.getpid(), created_at=ts)
    try:
        with file_lock(paths):
            raise AssertionError("file_lock should not have granted a second lock to the same live owner")
    except CompileLockError as exc:
        assert "locked" in str(exc)


def test_default_lock_stale_seconds_is_thirty_minutes():
    assert DEFAULT_LOCK_STALE_SECONDS == 30 * 60

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from .config import DEFAULT_LOCK_STALE_SECONDS, Paths
from .schemas import SuggestionEnvelope


SUGGESTION_STATES = ("pending", "processing", "done", "failed", "discarded")


class CompileLockError(RuntimeError):
    pass


def ensure_runtime_dirs(paths: Paths) -> None:
    for directory in (
        paths.state_dir,
        paths.suggestions_dir,
        paths.suggestions_pending_dir,
        paths.suggestions_processing_dir,
        paths.suggestions_done_dir,
        paths.suggestions_failed_dir,
        paths.suggestions_discarded_dir,
        paths.memory_dir,
        paths.recall_dir,
        paths.skills_dir,
        paths.managed_skills_dir,
        paths.compiler_dir,
        paths.review_dir,
        paths.review_snapshots_dir,
        paths.scheduler_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def utc_now() -> datetime:
    return datetime.now(UTC)


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temp_name = handle.name
    os.replace(temp_name, path)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temp_name = handle.name
    os.replace(temp_name, path)


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def repo_fingerprint(repo_root: Path) -> str:
    return hashlib.sha1(str(repo_root.resolve()).encode("utf-8")).hexdigest()


def compute_stable_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def _state_dir(paths: Paths, state: str) -> Path:
    return getattr(paths, f"suggestions_{state}_dir")


def list_suggestions(paths: Paths, state: str) -> list[Path]:
    ensure_runtime_dirs(paths)
    return sorted(_state_dir(paths, state).glob("*.json"))


def _all_suggestion_paths(paths: Paths) -> list[Path]:
    output: list[Path] = []
    for state in SUGGESTION_STATES:
        output.extend(list_suggestions(paths, state))
    return output


def find_suggestion_by_idempotency(paths: Paths, idempotency_key: str) -> Path | None:
    for path in _all_suggestion_paths(paths):
        raw = load_json(path)
        if isinstance(raw, dict) and raw.get("idempotency_key") == idempotency_key:
            return path
    return None


def append_pending_suggestion(paths: Paths, envelope: SuggestionEnvelope) -> Path:
    ensure_runtime_dirs(paths)
    existing = find_suggestion_by_idempotency(paths, envelope.idempotency_key)
    if existing is not None:
        return existing
    destination = paths.suggestions_pending_dir / f"{envelope.suggestion_id}.json"
    atomic_write_json(destination, envelope.to_dict())
    return destination


def update_suggestion(path: Path, envelope: SuggestionEnvelope) -> Path:
    atomic_write_json(path, envelope.to_dict())
    return path


def move_suggestion(paths: Paths, source: Path, envelope: SuggestionEnvelope, state: str) -> Path:
    if state not in SUGGESTION_STATES:
        raise ValueError(f"unsupported state: {state}")
    destination = _state_dir(paths, state) / source.name
    atomic_write_json(destination, envelope.to_dict())
    if source.exists() and source.resolve() != destination.resolve():
        source.unlink()
    return destination


def claim_suggestions(paths: Paths, batch_size: int, max_attempts: int = 3) -> list[tuple[Path, SuggestionEnvelope]]:
    claimed: list[tuple[Path, SuggestionEnvelope]] = []
    candidates = list_suggestions(paths, "pending")
    retryable_failed = []
    for failed_path in list_suggestions(paths, "failed"):
        envelope = SuggestionEnvelope.from_dict(load_json(failed_path))
        if envelope.attempt_count < max_attempts:
            retryable_failed.append(failed_path)
    for source in (candidates + retryable_failed)[:batch_size]:
        envelope = SuggestionEnvelope.from_dict(load_json(source))
        transition = {
            "at": utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "from": envelope.state,
            "to": "processing",
            "reason": "claimed",
        }
        updated = replace(
            envelope,
            state="processing",
            attempt_count=envelope.attempt_count + 1,
            failure_reason=None,
            transition_log=[*envelope.transition_log, transition],
        )
        destination = move_suggestion(paths, source, updated, "processing")
        claimed.append((destination, updated))
    return claimed


def finalize_suggestion(paths: Paths, source: Path, envelope: SuggestionEnvelope, state: str, reason: str | None = None) -> Path:
    transition = {
        "at": utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "from": envelope.state,
        "to": state,
        "reason": reason or state,
    }
    updated = replace(
        envelope,
        state=state,
        failure_reason=reason if state in {"failed", "discarded"} else None,
        transition_log=[*envelope.transition_log, transition],
    )
    return move_suggestion(paths, source, updated, state)


def read_text_if_exists(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def load_memory_files(paths: Paths) -> dict[str, str]:
    return {
        "USER.md": read_text_if_exists(paths.memory_dir / "USER.md"),
        "MEMORY.md": read_text_if_exists(paths.memory_dir / "MEMORY.md"),
    }


def has_pending_work(paths: Paths) -> bool:
    if list_suggestions(paths, "pending"):
        return True
    return any(SuggestionEnvelope.from_dict(load_json(path)).attempt_count < 3 for path in list_suggestions(paths, "failed"))


def compiler_lock_path(paths: Paths, name: str = "compile.lock") -> Path:
    ensure_runtime_dirs(paths)
    return paths.compiler_dir / name


def _pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we lack signal permission — treat as alive.
        return True
    except OSError:
        return False
    return True


def lock_status(paths: Paths, stale_after_seconds: int = DEFAULT_LOCK_STALE_SECONDS, name: str = "compile.lock") -> dict[str, object]:
    path = compiler_lock_path(paths, name=name)
    if not path.exists():
        return {"locked": False, "stale": False, "path": str(path)}
    raw = load_json(path)
    created_at = datetime.fromisoformat(str(raw["created_at"]).replace("Z", "+00:00"))
    age = (utc_now() - created_at).total_seconds()
    owner_pid = raw.get("pid")
    pid_alive = _pid_alive(owner_pid)
    # Three ways to become stale:
    #   1. pid is gone (SIGKILL / reboot / crash) — immediate
    #   2. age exceeds hard upper bound (process is hung past tolerance)
    #   3. negative age (clock skew / NTP rollback) — never trust a lock from the future
    stale = (not pid_alive) or age > stale_after_seconds or age < 0
    stale_reason: str | None = None
    if not pid_alive:
        stale_reason = "pid_not_alive"
    elif age < 0:
        stale_reason = "negative_age"
    elif age > stale_after_seconds:
        stale_reason = "exceeded_max_age"
    return {
        "locked": True,
        "stale": stale,
        "stale_reason": stale_reason,
        "path": str(path),
        "age_seconds": age,
        "owner_pid": owner_pid,
        "pid_alive": pid_alive,
    }


@contextmanager
def file_lock(paths: Paths, name: str = "compile.lock", stale_after_seconds: int = DEFAULT_LOCK_STALE_SECONDS) -> Iterator[Path]:
    ensure_runtime_dirs(paths)
    lock_path = compiler_lock_path(paths, name=name)
    status = lock_status(paths, stale_after_seconds=stale_after_seconds, name=name)
    if status["locked"] and not status["stale"]:
        raise CompileLockError(f"compile already locked: {lock_path}")
    if status["locked"] and status["stale"] and lock_path.exists():
        lock_path.unlink()
    payload = {
        "created_at": utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "pid": os.getpid(),
    }
    atomic_write_json(lock_path, payload)
    try:
        yield lock_path
    finally:
        if lock_path.exists():
            lock_path.unlink()

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import SESSION_REFLECTION_SUBDIR, get_home_dir
from ..storage import compute_stable_id


@dataclass(frozen=True)
class SessionReflectionPaths:
    home: Path
    root: Path
    jobs_dir: Path
    job_path: Path
    run_dir: Path
    receipt_path: Path
    child_threads_dir: Path
    locks_dir: Path
    triggers_dir: Path


@dataclass(frozen=True)
class SessionReflectionTriggerPaths:
    """Filesystem paths for one session's trigger sidecar state."""

    home: Path
    root: Path
    session_dir: Path
    state_path: Path
    decisions_path: Path
    lock_path: Path


def build_session_reflection_paths(
    home: str | Path | None = None,
    job_id: str = "",
) -> SessionReflectionPaths:
    """Resolve session reflection paths without creating directories."""
    home_dir = Path(home).expanduser().resolve() if home else get_home_dir()
    root = home_dir / SESSION_REFLECTION_SUBDIR
    jobs_dir = root / "jobs"
    run_dir = root / "runs" / job_id if job_id else root / "runs"
    return SessionReflectionPaths(
        home=home_dir,
        root=root,
        jobs_dir=jobs_dir,
        job_path=jobs_dir / f"{job_id}.json" if job_id else jobs_dir,
        run_dir=run_dir,
        receipt_path=run_dir / "receipt.json",
        child_threads_dir=root / "child_threads",
        locks_dir=root / "locks",
        triggers_dir=root / "triggers",
    )


def build_session_reflection_trigger_paths(
    session_id: str,
    home: str | Path | None = None,
) -> SessionReflectionTriggerPaths:
    """Resolve sidecar trigger paths for one parent session id."""
    home_dir = Path(home).expanduser().resolve() if home else get_home_dir()
    root = home_dir / SESSION_REFLECTION_SUBDIR / "triggers"
    session_dir = root / compute_stable_id(session_id or "unknown-session")
    return SessionReflectionTriggerPaths(
        home=home_dir,
        root=root,
        session_dir=session_dir,
        state_path=session_dir / "state.json",
        decisions_path=session_dir / "decisions.jsonl",
        lock_path=session_dir / "trigger.lock",
    )

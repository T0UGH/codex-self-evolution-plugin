from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import SESSION_REFLECTION_SUBDIR, get_home_dir


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
    )

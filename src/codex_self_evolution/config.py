from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_BATCH_SIZE = 100
# Hard upper bound for how long a compile lock may live before the next
# preflight treats it as stale and reclaims it. Set to 30 minutes: a normal
# compile should finish well under this (typical target 5-10 minutes); exceeding
# it means the owning process is stuck and should be evicted.
DEFAULT_LOCK_STALE_SECONDS = 30 * 60
PACKAGE_ROOT = Path(__file__).resolve().parent
PLUGIN_OWNER = "codex-self-evolution-plugin"
MANAGED_SKILLS_DIRNAME = "managed"


@dataclass(frozen=True)
class Paths:
    repo_root: Path
    plugin_root: Path
    state_dir: Path
    suggestions_dir: Path
    suggestions_pending_dir: Path
    suggestions_processing_dir: Path
    suggestions_done_dir: Path
    suggestions_failed_dir: Path
    suggestions_discarded_dir: Path
    memory_dir: Path
    recall_dir: Path
    skills_dir: Path
    managed_skills_dir: Path
    compiler_dir: Path
    review_dir: Path
    review_snapshots_dir: Path
    scheduler_dir: Path


def resolve_repo_root(cwd: str | Path | None = None) -> Path:
    if cwd:
        return Path(cwd).resolve()
    return Path.cwd().resolve()


def build_paths(repo_root: str | Path | None = None, state_dir: str | Path | None = None) -> Paths:
    resolved_repo = resolve_repo_root(repo_root)
    plugin_root = resolved_repo / ".codex-plugin"
    resolved_state = Path(state_dir).resolve() if state_dir else resolved_repo / "data"
    suggestions_dir = resolved_state / "suggestions"
    skills_dir = resolved_state / "skills"
    review_dir = resolved_state / "review"
    return Paths(
        repo_root=resolved_repo,
        plugin_root=plugin_root,
        state_dir=resolved_state,
        suggestions_dir=suggestions_dir,
        suggestions_pending_dir=suggestions_dir / "pending",
        suggestions_processing_dir=suggestions_dir / "processing",
        suggestions_done_dir=suggestions_dir / "done",
        suggestions_failed_dir=suggestions_dir / "failed",
        suggestions_discarded_dir=suggestions_dir / "discarded",
        memory_dir=resolved_state / "memory",
        recall_dir=resolved_state / "recall",
        skills_dir=skills_dir,
        managed_skills_dir=skills_dir / MANAGED_SKILLS_DIRNAME,
        compiler_dir=resolved_state / "compiler",
        review_dir=review_dir,
        review_snapshots_dir=review_dir / "snapshots",
        scheduler_dir=resolved_state / "scheduler",
    )

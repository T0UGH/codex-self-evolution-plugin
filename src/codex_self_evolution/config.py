from __future__ import annotations

import os
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

# Where per-project state (suggestions, memory, recall, review) lives by default.
# Mirrors Claude Code's `~/.claude/projects/<mangled-path>/` convention so each
# repo has an isolated bucket but users' source trees stay clean — no more
# auto-created `data/` appearing under every repo Codex runs in. Override with
# CODEX_SELF_EVOLUTION_HOME (useful for tests or shared hosts).
HOME_DIR_ENV = "CODEX_SELF_EVOLUTION_HOME"
DEFAULT_HOME_DIRNAME = ".codex-self-evolution"
PROJECTS_SUBDIR = "projects"


def get_home_dir() -> Path:
    """Resolve the root dir that holds per-project state and user config.

    Precedence: ``$CODEX_SELF_EVOLUTION_HOME`` → ``~/.codex-self-evolution``.
    """
    override = os.environ.get(HOME_DIR_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / DEFAULT_HOME_DIRNAME


def mangle_project_path(path: Path) -> str:
    """Encode an absolute repo path as a single directory name.

    Same scheme as Claude Code: drop leading slash, then ``/`` → ``-``. So
    ``/Users/alice/code/repo`` becomes ``-Users-alice-code-repo``. Reversible
    visually by eye, which is enough for discoverability (``ls`` shows every
    repo that has been reviewed).
    """
    return str(path).replace("/", "-")


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
    review_failed_dir: Path
    scheduler_dir: Path


def resolve_repo_root(cwd: str | Path | None = None) -> Path:
    if cwd:
        return Path(cwd).resolve()
    return Path.cwd().resolve()


def build_paths(repo_root: str | Path | None = None, state_dir: str | Path | None = None) -> Paths:
    resolved_repo = resolve_repo_root(repo_root)
    plugin_root = resolved_repo / ".codex-plugin"
    if state_dir:
        resolved_state = Path(state_dir).resolve()
    else:
        resolved_state = get_home_dir() / PROJECTS_SUBDIR / mangle_project_path(resolved_repo)
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
        review_failed_dir=review_dir / "failed",
        scheduler_dir=resolved_state / "scheduler",
    )

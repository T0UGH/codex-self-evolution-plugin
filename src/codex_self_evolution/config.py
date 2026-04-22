from __future__ import annotations

import os
import subprocess
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


def unmangle_bucket_name(name: str) -> Path:
    """Reverse of :func:`mangle_project_path`.

    The result is a nominal path — it may or may not still exist on disk. Used
    by the worktree migration to figure out which bucket corresponds to which
    original cwd before deciding whether to consolidate.
    """
    return Path(name.replace("-", "/"))


# Suffix marking buckets that have been archived by the worktree-migration
# flow. The scheduler's ``scan_all_projects`` explicitly skips anything under
# this suffix so old buckets stop accumulating compile receipts after consolidation.
ARCHIVED_BUCKET_SUFFIX = ".archived"


def is_archived_bucket(bucket_name: str) -> bool:
    """True when ``bucket_name`` matches the ``<name>.archived.<ts>`` pattern
    produced by worktree consolidation (or the bare ``.archived`` suffix an
    older migration might have used). Used by scheduler scan and diagnostics
    to skip tombstone buckets consistently."""
    return bucket_name.endswith(ARCHIVED_BUCKET_SUFFIX) or f"{ARCHIVED_BUCKET_SUFFIX}." in bucket_name

# Sidecar file that stores the bucket's canonical cwd. Written lazily by
# :func:`build_paths`; read by the worktree migration to identify a bucket's
# original working directory without relying on the lossy
# :func:`unmangle_bucket_name` round-trip (paths containing ``-`` collide with
# the ``/``→``-`` mangling). Legacy buckets that predate this marker fall
# back to unmangling.
CANONICAL_CWD_MARKER = ".canonical_cwd"


def _maybe_write_canonical_cwd(state_dir: Path, cwd: Path) -> None:
    """Write the canonical-cwd marker if the bucket directory exists.

    Intentionally a no-op when ``state_dir`` is not yet created — the first
    write into a fresh bucket (e.g. a stop-review snapshot) creates the dir,
    and the next ``build_paths`` call for that bucket materialises the
    marker. That two-phase materialisation keeps this helper side-effect-free
    on pristine checkouts that call ``build_paths`` without actually writing
    anything (notably, many unit tests).
    """
    if not state_dir.is_dir():
        return
    marker = state_dir / CANONICAL_CWD_MARKER
    if marker.exists():
        return
    try:
        marker.write_text(f"{cwd}\n", encoding="utf-8")
    except OSError:
        # Marker is an optimisation for the migration tool — best-effort only.
        pass


def detect_repo_identity(path: Path, timeout: float = 2.0) -> Path | None:
    """Return the canonical repo root for ``path``, or ``None`` if not in git.

    Git worktrees of the same logical repository share a single ``.git``
    *common directory*. Running ``git rev-parse --git-common-dir`` in any
    linked worktree returns the main worktree's ``.git`` (or ``.git`` itself
    from the main worktree); the parent of that common dir is the canonical
    working-tree root. All linked worktrees collapse to the same identity.

    Returns the resolved absolute path of the canonical working tree. Caller
    is expected to fall back to ``path`` itself when this returns ``None`` —
    e.g. ``path`` is not in a git repo, ``git`` is not installed, or the call
    timed out. That preserves the pre-worktree-aware behaviour as a safe
    default and keeps non-git use cases working.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    raw = result.stdout.strip()
    if not raw:
        return None
    # Relative paths (".git") resolve against the query path; absolute paths
    # pass through Path.resolve() to normalise symlinks.
    common_dir = Path(raw)
    if not common_dir.is_absolute():
        common_dir = (path / common_dir)
    try:
        resolved = common_dir.resolve()
    except (OSError, RuntimeError):
        return None
    # Normal git repo: common dir is "<repo>/.git" → canonical root is parent.
    # Bare repo: common dir is the repo itself → use as-is. We detect this by
    # checking whether the basename is exactly ".git".
    if resolved.name == ".git":
        return resolved.parent
    return resolved


def resolve_bucket_key(repo_root: Path) -> Path:
    """Canonical bucket-key path for a cwd, worktree-aware.

    Worktrees of the same repo resolve to the same canonical path; unrelated
    repos (different clones, separate origins) stay isolated. Non-git dirs
    fall back to the cwd itself — this keeps out-of-tree usage working and
    preserves the pre-migration bucket layout when the user has set up state
    that way.
    """
    identity = detect_repo_identity(repo_root)
    return identity if identity is not None else repo_root


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
        bucket_key = resolve_bucket_key(resolved_repo)
        resolved_state = get_home_dir() / PROJECTS_SUBDIR / mangle_project_path(bucket_key)
        _maybe_write_canonical_cwd(resolved_state, bucket_key)
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

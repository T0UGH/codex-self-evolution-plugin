from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def _git(cwd: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def collect_repo_metadata(cwd: str | Path) -> dict[str, str]:
    resolved = Path(cwd).expanduser().resolve()
    worktree = _git(resolved, "rev-parse", "--show-toplevel")
    common = _git(resolved, "rev-parse", "--git-common-dir")
    branch = _git(resolved, "branch", "--show-current")
    origin = _git(resolved, "config", "--get", "remote.origin.url")

    worktree_root = Path(worktree).resolve() if worktree else resolved
    if common:
        common_path = Path(common)
        if not common_path.is_absolute():
            common_path = (worktree_root / common_path).resolve()
        stable = str(common_path)
        repo_root = str(common_path.parent if common_path.name == ".git" else common_path)
    else:
        stable = str(resolved)
        repo_root = str(resolved)

    if origin:
        stable = f"{origin}|{stable}"

    return {
        "cwd": str(resolved),
        "repo_root": repo_root,
        "worktree_root": str(worktree_root),
        "repo_fingerprint": hashlib.sha1(stable.encode("utf-8")).hexdigest(),
        "git_branch": branch,
    }


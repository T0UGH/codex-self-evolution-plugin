from __future__ import annotations

from pathlib import Path

from ..config import build_paths
from ..storage import ensure_runtime_dirs, load_memory_files, repo_fingerprint


def session_start(cwd: str | Path | None = None, state_dir: str | Path | None = None) -> dict:
    paths = build_paths(repo_root=cwd, state_dir=state_dir)
    ensure_runtime_dirs(paths)
    policy = (Path(__file__).resolve().parent.parent / "recall" / "policy.md").read_text(encoding="utf-8")
    memory_files = load_memory_files(paths)
    combined_prefix = "\n\n".join(
        section
        for section in [
            "# Stable Background",
            "## USER.md\n" + (memory_files["USER.md"] or "_No entries yet._\n"),
            "## MEMORY.md\n" + (memory_files["MEMORY.md"] or "_No entries yet._\n"),
        ]
        if section
    )
    return {
        "hook": "SessionStart",
        "cwd": str(paths.repo_root),
        "repo_fingerprint": repo_fingerprint(paths.repo_root),
        "state_dir": str(paths.state_dir),
        "stable_background": {
            "current_user_md": memory_files["USER.md"],
            "current_memory_md": memory_files["MEMORY.md"],
            "combined_prefix": combined_prefix,
        },
        "recall": {
            "policy": policy,
            "trigger_defaults": {"same_repo_first": True, "same_cwd_first": True, "auto_trigger": True},
        },
        "runtime": {
            "managed_skills_manifest_path": str(paths.skills_dir / "manifest.json"),
            "review_snapshots_dir": str(paths.review_snapshots_dir),
        },
    }

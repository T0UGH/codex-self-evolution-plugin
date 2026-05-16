"""End-to-end tests for the worktree consolidation migration.

We build two real git worktrees under ``tmp_path``, seed both buckets with
fake memory, then run the migration and verify:

- The feature worktree's bucket is archived, not deleted.
- The main worktree's bucket absorbs memory entries.
- Dedup of (scope, content) holds across the merge.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from codex_self_evolution.config import (
    ARCHIVED_BUCKET_SUFFIX,
    CANONICAL_CWD_MARKER,
    mangle_project_path,
)
from codex_self_evolution.migrate import plan_migration, run_migration


requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git not installed",
)


def _git(cwd: Path, *args: str) -> None:
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "test"
    env["GIT_AUTHOR_EMAIL"] = "test@example.com"
    env["GIT_COMMITTER_NAME"] = "test"
    env["GIT_COMMITTER_EMAIL"] = "test@example.com"
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def _seed_bucket(
    bucket: Path,
    memory_entries: dict,
    canonical_cwd: Path | None = None,
) -> None:
    """Seed a migration bucket with legacy memory state."""
    (bucket / "memory").mkdir(parents=True, exist_ok=True)
    (bucket / "memory" / "memory.json").write_text(json.dumps(memory_entries))
    # Tests use pytest tmp_path which contains dashes (pytest-of-xxx, pytest-N),
    # so the mangle/unmangle round-trip is lossy. Write the canonical_cwd
    # marker that real build_paths would lay down on first bucket access — the
    # migration plans by reading this file, not by unmangling.
    if canonical_cwd is not None:
        (bucket / CANONICAL_CWD_MARKER).write_text(str(canonical_cwd))


@pytest.fixture
def worktree_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    """Build main + linked worktree, return (home_dir, main_repo, linked_repo)."""
    home = tmp_path / "state"
    home.mkdir()
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))

    main_repo = tmp_path / "main"
    main_repo.mkdir()
    _git(main_repo, "init", "-b", "main", "-q")
    (main_repo / "README").write_text("main\n")
    _git(main_repo, "add", "README")
    _git(main_repo, "commit", "-m", "init", "-q")

    linked = tmp_path / "linked"
    _git(main_repo, "worktree", "add", "-b", "feature", str(linked))

    return home.resolve(), main_repo.resolve(), linked.resolve()


@requires_git
def test_plan_migration_identifies_linked_worktree_as_source(worktree_setup: tuple[Path, Path, Path]) -> None:
    home, main_repo, linked = worktree_setup
    projects = home / "projects"
    main_bucket = projects / mangle_project_path(main_repo)
    linked_bucket = projects / mangle_project_path(linked)

    # Seed both buckets with minimal content so plan_migration has something
    # to count. (The identify-source logic doesn't require any content.)
    main_bucket.mkdir(parents=True)
    linked_bucket.mkdir(parents=True)
    (main_bucket / CANONICAL_CWD_MARKER).write_text(str(main_repo))
    (linked_bucket / CANONICAL_CWD_MARKER).write_text(str(linked))

    plan = plan_migration(home=home)
    assert len(plan.plans) == 1
    assert plan.plans[0].source_bucket == linked_bucket.name
    assert plan.plans[0].target_bucket == main_bucket.name
    assert plan.plans[0].target_cwd == main_repo
    # Main bucket should skip with an informative reason.
    assert any("already the canonical worktree" in s.reason for s in plan.skipped)


@requires_git
def test_run_migration_merges_memory(worktree_setup: tuple[Path, Path, Path]) -> None:
    home, main_repo, linked = worktree_setup
    projects = home / "projects"
    main_bucket = projects / mangle_project_path(main_repo)
    linked_bucket = projects / mangle_project_path(linked)

    _seed_bucket(
        main_bucket,
        {
            "user": [],
            "global": [
                {"summary": "shared convention", "content": "prefer atomic commits", "confidence": 0.9},
            ],
        },
        canonical_cwd=main_repo,
    )
    _seed_bucket(
        linked_bucket,
        {
            "user": [
                {"summary": "user style", "content": "terse confirmations", "confidence": 1.0},
            ],
            "global": [
                {"summary": "shared convention", "content": "prefer atomic commits", "confidence": 0.5},  # duplicate
                {"summary": "feature-specific", "content": "branch feature work in progress", "confidence": 0.7},
            ],
        },
        canonical_cwd=linked,
    )

    result = run_migration(home=home, apply=True)
    assert result["applied"] is True
    assert result["counts"]["to_migrate"] == 1
    assert result["apply_results"][0]["source_bucket"] == linked_bucket.name

    # Target bucket now has user entry + dedup'd globals + feature-specific
    merged = json.loads((main_bucket / "memory" / "memory.json").read_text())
    assert [item["content"] for item in merged["user"]] == ["terse confirmations"]
    global_contents = sorted(item["content"] for item in merged["global"])
    assert global_contents == ["branch feature work in progress", "prefer atomic commits"]
    # Target keeps the higher-confidence version of the shared entry.
    shared = next(item for item in merged["global"] if item["content"] == "prefer atomic commits")
    assert shared["confidence"] == 0.9
    rendered_memory = (main_bucket / "memory" / "MEMORY.md").read_text()
    assert "terse confirmations" in rendered_memory
    assert "prefer atomic commits" in rendered_memory
    assert not (main_bucket / "memory" / "USER.md").exists()

    # Source bucket is archived (renamed), not deleted.
    assert not linked_bucket.exists()
    archived = [p for p in projects.iterdir() if p.name.startswith(linked_bucket.name) and ARCHIVED_BUCKET_SUFFIX in p.name]
    assert len(archived) == 1
    assert (archived[0] / "memory" / "memory.json").exists()


@requires_git
def test_migration_dry_run_does_not_rename(worktree_setup: tuple[Path, Path, Path]) -> None:
    home, main_repo, linked = worktree_setup
    projects = home / "projects"
    main_bucket = projects / mangle_project_path(main_repo)
    linked_bucket = projects / mangle_project_path(linked)

    _seed_bucket(main_bucket, {"user": [], "global": []}, canonical_cwd=main_repo)
    _seed_bucket(linked_bucket, {"user": [], "global": []}, canonical_cwd=linked)

    result = run_migration(home=home, apply=False)
    assert result["applied"] is False
    assert result["counts"]["to_migrate"] == 1
    # Linked bucket still there — dry-run means no renames.
    assert linked_bucket.exists()

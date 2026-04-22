"""Integration tests for the worktree-aware bucket keying.

These tests actually invoke ``git`` to spin up real worktrees; the production
codepath shells out to ``git rev-parse --git-common-dir`` so mocking would
skip the part that could regress. Kept fast by using ``tmp_path`` throughout
and only running a couple of git commands per test.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from codex_self_evolution.config import (
    build_paths,
    detect_repo_identity,
    mangle_project_path,
    resolve_bucket_key,
    unmangle_bucket_name,
)


requires_git = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git not installed",
)


def _git(cwd: Path, *args: str) -> None:
    env = os.environ.copy()
    # Keep the test hermetic: fixed committer, no external hooks, no prompts.
    env["GIT_AUTHOR_NAME"] = "test"
    env["GIT_AUTHOR_EMAIL"] = "test@example.com"
    env["GIT_COMMITTER_NAME"] = "test"
    env["GIT_COMMITTER_EMAIL"] = "test@example.com"
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env=env,
    )


@pytest.fixture
def main_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "main"
    repo.mkdir()
    _git(repo, "init", "-b", "main", "-q")
    (repo / "README.md").write_text("hi\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init", "-q")
    return repo.resolve()


@requires_git
def test_detect_repo_identity_returns_main_worktree_from_main(main_repo: Path) -> None:
    assert detect_repo_identity(main_repo) == main_repo


@requires_git
def test_detect_repo_identity_collapses_linked_worktrees_to_main(main_repo: Path, tmp_path: Path) -> None:
    linked = tmp_path / "linked"
    _git(main_repo, "worktree", "add", "-b", "feature", str(linked))
    assert detect_repo_identity(linked.resolve()) == main_repo


@requires_git
def test_detect_repo_identity_collapses_multiple_worktrees_to_same_root(main_repo: Path, tmp_path: Path) -> None:
    a = tmp_path / "feature_a"
    b = tmp_path / "feature_b"
    _git(main_repo, "worktree", "add", "-b", "fa", str(a))
    _git(main_repo, "worktree", "add", "-b", "fb", str(b))
    id_main = detect_repo_identity(main_repo)
    id_a = detect_repo_identity(a.resolve())
    id_b = detect_repo_identity(b.resolve())
    assert id_main == id_a == id_b == main_repo


def test_detect_repo_identity_returns_none_outside_git(tmp_path: Path) -> None:
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert detect_repo_identity(plain) is None


def test_resolve_bucket_key_falls_back_to_cwd_when_not_in_git(tmp_path: Path) -> None:
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert resolve_bucket_key(plain) == plain


@requires_git
def test_build_paths_routes_linked_worktree_to_main_bucket(
    main_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "state"
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))

    linked = tmp_path / "linked"
    _git(main_repo, "worktree", "add", "-b", "feature", str(linked))

    main_paths = build_paths(repo_root=main_repo)
    linked_paths = build_paths(repo_root=linked.resolve())

    # Both worktrees of the same repo land in the bucket keyed by the main
    # worktree — the whole point of the consolidation.
    assert main_paths.state_dir == linked_paths.state_dir
    assert main_paths.state_dir.name == mangle_project_path(main_repo)


@requires_git
def test_build_paths_keeps_independent_repos_in_separate_buckets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "state"
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))

    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    repo_a.mkdir()
    repo_b.mkdir()
    _git(repo_a, "init", "-b", "main", "-q")
    _git(repo_b, "init", "-b", "main", "-q")
    (repo_a / "f").write_text("a\n")
    (repo_b / "f").write_text("b\n")
    _git(repo_a, "add", "f")
    _git(repo_b, "add", "f")
    _git(repo_a, "commit", "-m", "init", "-q")
    _git(repo_b, "commit", "-m", "init", "-q")

    paths_a = build_paths(repo_root=repo_a.resolve())
    paths_b = build_paths(repo_root=repo_b.resolve())
    assert paths_a.state_dir != paths_b.state_dir


def test_unmangle_round_trips_common_paths() -> None:
    original = Path("/Users/alice/code/repo")
    assert unmangle_bucket_name(mangle_project_path(original)) == original

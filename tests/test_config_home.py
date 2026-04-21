"""Tests for the per-project home-dir routing.

The plugin used to dump `data/` into every repo it reviewed (contaminating the
user's source tree). Now each repo gets an isolated bucket under
`~/.codex-self-evolution/projects/<mangled-path>/`, mirroring the Claude Code
convention at `~/.claude/projects/`. These tests lock in the mangling scheme
and the default routing so a future refactor can't silently regress to
per-repo `data/`.
"""
from pathlib import Path

from codex_self_evolution.config import (
    HOME_DIR_ENV,
    build_paths,
    get_home_dir,
    mangle_project_path,
)


def test_mangle_project_path_matches_claude_scheme():
    # `/` → `-`; leading `/` becomes a leading `-`. Same visible encoding as
    # `~/.claude/projects/-Users-bytedance-code-github-xxx`.
    assert mangle_project_path(Path("/Users/alice/code/repo")) == "-Users-alice-code-repo"
    assert mangle_project_path(Path("/a")) == "-a"


def test_get_home_dir_defaults_to_dotdir(monkeypatch, tmp_path):
    monkeypatch.delenv(HOME_DIR_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert get_home_dir() == tmp_path / ".codex-self-evolution"


def test_get_home_dir_respects_env_override(monkeypatch, tmp_path):
    override = tmp_path / "custom-home"
    monkeypatch.setenv(HOME_DIR_ENV, str(override))
    assert get_home_dir() == override.resolve()


def test_build_paths_defaults_to_per_project_home_bucket(monkeypatch, tmp_path):
    # Without an explicit state_dir, paths must route into
    # <home>/projects/<mangled-cwd>/ rather than <cwd>/data/. This is the
    # whole point of the change: stop polluting user repos.
    home = tmp_path / "home"
    monkeypatch.setenv(HOME_DIR_ENV, str(home))
    repo = tmp_path / "repo"
    repo.mkdir()

    paths = build_paths(repo_root=repo)

    expected_bucket = home.resolve() / "projects" / mangle_project_path(repo.resolve())
    assert paths.state_dir == expected_bucket
    assert paths.suggestions_pending_dir == expected_bucket / "suggestions" / "pending"
    # And crucially: nothing lands inside the repo itself.
    assert not (repo / "data").exists()


def test_build_paths_explicit_state_dir_still_wins(monkeypatch, tmp_path):
    # Tests and power users should still be able to point state elsewhere via
    # --state-dir. The home-dir default must only apply when unspecified.
    home = tmp_path / "home"
    monkeypatch.setenv(HOME_DIR_ENV, str(home))
    repo = tmp_path / "repo"
    repo.mkdir()
    override = tmp_path / "explicit-state"

    paths = build_paths(repo_root=repo, state_dir=override)

    assert paths.state_dir == override.resolve()
    assert "projects" not in paths.state_dir.parts

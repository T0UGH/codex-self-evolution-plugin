from __future__ import annotations

from pathlib import Path

from codex_self_evolution.config_file import config_to_dict, load_config
from codex_self_evolution.config_file_template import CONFIG_TEMPLATE
from codex_self_evolution.session_reflection.paths import build_session_reflection_paths


def _write_config(home: Path, text: str) -> None:
    """Write a config.toml fixture under the supplied CSEP home."""
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")


def test_session_reflection_defaults_are_enabled(tmp_path: Path) -> None:
    loaded = load_config(home=tmp_path, env={})
    cfg = loaded.config.session_reflection

    assert cfg.enabled is True
    assert cfg.backend == "codex-app-server"
    assert cfg.model == "gpt-5.3-codex-spark"
    assert cfg.ephemeral is True
    assert cfg.sandbox == "danger-full-access"
    assert cfg.approval_policy == "never"
    assert cfg.skill_prefix == "csep-reflect-"
    assert cfg.timeout_seconds == 900.0
    assert cfg.max_concurrent_jobs == 1
    assert cfg.replace_stop_reviewer is True
    assert loaded.sources["session_reflection.model"] == "default"


def test_session_reflection_toml_values_apply(tmp_path: Path) -> None:
    _write_config(tmp_path, """
[session_reflection]
enabled = false
backend = "codex-app-server"
model = "gpt-5.3-codex-spark"
ephemeral = false
sandbox = "workspace-write"
approval_policy = "never"
skill_prefix = "csep-reflect-"
timeout_seconds = 1200
max_concurrent_jobs = 1
replace_stop_reviewer = true
""")

    loaded = load_config(home=tmp_path, env={})
    cfg = loaded.config.session_reflection

    assert cfg.enabled is False
    assert cfg.ephemeral is False
    assert cfg.sandbox == "workspace-write"
    assert cfg.timeout_seconds == 1200.0
    assert loaded.sources["session_reflection.ephemeral"] == "config.toml"


def test_session_reflection_invalid_backend_warns(tmp_path: Path) -> None:
    _write_config(tmp_path, """
[session_reflection]
enabled = true
backend = "codex-exec"
model = "gpt-5.3-codex-spark"
""")

    loaded = load_config(home=tmp_path, env={})

    assert loaded.config.session_reflection.backend == "codex-exec"
    assert any("session_reflection.backend" in warning for warning in loaded.warnings)


def test_config_to_dict_includes_session_reflection(tmp_path: Path) -> None:
    loaded = load_config(home=tmp_path, env={})
    data = config_to_dict(loaded.config)

    assert data["session_reflection"]["backend"] == "codex-app-server"
    assert data["session_reflection"]["skill_prefix"] == "csep-reflect-"


def test_session_reflection_template_contains_defaults() -> None:
    assert "[session_reflection]" in CONFIG_TEMPLATE
    assert 'backend = "codex-app-server"' in CONFIG_TEMPLATE
    assert 'model = "gpt-5.3-codex-spark"' in CONFIG_TEMPLATE
    assert 'threadSource = "memory_consolidation"' not in CONFIG_TEMPLATE


def test_build_session_reflection_paths(tmp_path: Path) -> None:
    paths = build_session_reflection_paths(home=tmp_path, job_id="job-1")

    assert paths.root == tmp_path / "session_reflection"
    assert paths.jobs_dir == paths.root / "jobs"
    assert paths.job_path == paths.jobs_dir / "job-1.json"
    assert paths.run_dir == paths.root / "runs" / "job-1"
    assert paths.receipt_path == paths.run_dir / "receipt.json"
    assert paths.child_threads_dir == paths.root / "child_threads"
    assert paths.locks_dir == paths.root / "locks"

"""Status diagnostic: read-only, fault-tolerant, and legacy-free."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from codex_self_evolution import cli, diagnostics
from codex_self_evolution.diagnostics import (
    _check_env_provider,
    _check_tools,
    collect_status,
)


def test_env_provider_reports_key_names_never_values(tmp_path: Path) -> None:
    """Provider diagnostics report key names without leaking values."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env.provider").write_text(
        "# Comment line\n"
        "MINIMAX_API_KEY=sk-real-secret-value-MUST-NOT-APPEAR\n"
        "OPENAI_API_KEY=\n"
        "ANTHROPIC_API_KEY=some-val\n"
        "KIMI_API_KEY=kimi-secret\n"
        "MINIMAX_REGION=global\n"
        "\n",
        encoding="utf-8",
    )
    result = _check_env_provider(home)

    serialized = json.dumps(result)
    assert "sk-real-secret-value-MUST-NOT-APPEAR" not in serialized
    assert "some-val" not in serialized
    assert "kimi-secret" not in serialized
    assert "MINIMAX_API_KEY" in result["keys_set"]
    assert "ANTHROPIC_API_KEY" in result["keys_set"]
    assert "KIMI_API_KEY" in result["keys_set"]
    assert "OPENAI_API_KEY" in result["keys_unset"]
    assert "MINIMAX_REGION" in result["other_keys_set"]


def test_env_provider_strips_quotes_before_emptiness_check(tmp_path: Path) -> None:
    """Quoted empty values are still reported as unset."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env.provider").write_text(
        'MINIMAX_API_KEY="actually-set"\n'
        "OPENAI_API_KEY=''\n"
        'ANTHROPIC_API_KEY="  "\n',
        encoding="utf-8",
    )
    result = _check_env_provider(home)
    assert "MINIMAX_API_KEY" in result["keys_set"]
    assert "OPENAI_API_KEY" in result["keys_unset"]
    assert "ANTHROPIC_API_KEY" in result["keys_unset"]


def test_env_provider_handles_export_prefix(tmp_path: Path) -> None:
    """Shell-style export lines are accepted by the restrictive parser."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env.provider").write_text(
        "export MINIMAX_API_KEY=shellstyle\n",
        encoding="utf-8",
    )
    result = _check_env_provider(home)
    assert "MINIMAX_API_KEY" in result["keys_set"]


def test_env_provider_missing_file_is_clean_report(tmp_path: Path) -> None:
    """Missing provider config reports all well-known keys as unset."""
    result = _check_env_provider(tmp_path / "does-not-exist")
    assert result["exists"] is False
    assert result["keys_set"] == []
    assert set(result["keys_unset"]) == {
        "MINIMAX_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "KIMI_API_KEY",
    }


def test_status_reports_plugin_hook_bundle_readiness() -> None:
    """Status keeps plugin metadata hook readiness as the hook health signal."""
    result = diagnostics._check_plugin_hook_bundle(
        Path("plugins/codex-self-evolution")
    )

    assert result["manifest_exists"] is True
    assert result["hooks_file_exists"] is True
    assert result["session_start_declared"] is True
    assert result["stop_declared"] is True
    assert result["uses_local_cli"] is True
    assert result["uses_uvx"] is False


def test_plugin_hook_bundle_default_root_is_not_cwd_relative(tmp_path: Path, monkeypatch) -> None:
    """The default plugin root is resolved from the package, not cwd."""
    monkeypatch.chdir(tmp_path)

    result = diagnostics._check_plugin_hook_bundle()

    assert result["manifest_exists"] is True
    assert result["hooks_file_exists"] is True
    assert result["session_start_declared"] is True
    assert result["stop_declared"] is True


def test_plugin_hook_bundle_scans_all_commands_for_uvx(tmp_path: Path) -> None:
    """uvx usage is detected across all commands in the plugin hooks file."""
    plugin_root = tmp_path / "plugin"
    metadata_dir = plugin_root / ".codex-plugin"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "plugin.json").write_text(json.dumps({
        "hooks": "./hooks.json",
    }), encoding="utf-8")
    (plugin_root / "hooks.json").write_text(json.dumps({
        "hooks": {
            "SessionStart": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "csep session-start --from-stdin",
                        },
                        {
                            "type": "command",
                            "command": "uvx codex-self-evolution-plugin session-start",
                        },
                    ],
                },
            ],
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "csep session-stop --from-stdin",
                        },
                    ],
                },
            ],
        },
    }), encoding="utf-8")

    result = diagnostics._check_plugin_hook_bundle(plugin_root)

    assert result["session_start_command"] == (
        "csep session-start --from-stdin"
    )
    assert result["uses_uvx"] is True


def test_plugin_hook_bundle_tolerates_non_object_hooks_section(tmp_path: Path) -> None:
    """A malformed bundled hooks file is isolated to the plugin_hooks section."""
    plugin_root = tmp_path / "plugin"
    metadata_dir = plugin_root / ".codex-plugin"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "plugin.json").write_text(json.dumps({
        "hooks": "./hooks.json",
    }), encoding="utf-8")
    (plugin_root / "hooks.json").write_text(json.dumps({
        "hooks": "bad",
    }), encoding="utf-8")

    result = diagnostics._check_plugin_hook_bundle(plugin_root)

    assert result["error"] is not None
    assert result["session_start_declared"] is False
    assert result["stop_declared"] is False
    assert result["uses_local_cli"] is False
    assert result["uses_uvx"] is False


def test_tools_probe_handles_missing_binary(monkeypatch) -> None:
    """Missing local tools are reported independently."""
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _: None)
    result = _check_tools()
    assert result["codex"]["available"] is False
    assert result["opencode"]["available"] is False
    assert result["pi"]["available"] is False
    assert result["csep"]["available"] is False


def test_tools_probe_grabs_first_line_of_version_output(monkeypatch) -> None:
    """Version probes use the first output line from each tool."""
    calls: list[list[str]] = []

    def fake_which(binary: str) -> str:
        return f"/fake/{binary}"

    def fake_run(argv, **_):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="opencode\n1.4.0\n", stderr="")

    monkeypatch.setattr(diagnostics.shutil, "which", fake_which)
    monkeypatch.setattr(diagnostics.subprocess, "run", fake_run)
    result = _check_tools()
    assert result["codex"]["version"] == "opencode"
    assert result["opencode"]["available"] is True
    assert result["pi"]["available"] is True
    assert result["csep"]["available"] is True
    assert ["csep", "--version"] in calls


def test_tools_probe_reports_csep_version_matrix(monkeypatch, tmp_path: Path) -> None:
    """CSEP status exposes installed, source, and PyPI versions separately."""
    calls: list[list[str]] = []

    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "csep"
version = "1.2.3"
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(diagnostics.shutil, "which", lambda binary: f"/fake/{binary}")

    def fake_run(argv, **_):
        calls.append(list(argv))
        stdout = "csep 1.2.4\n" if argv[0] == "csep" else "tool 0.1.0\n"
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(diagnostics.subprocess, "run", fake_run)
    monkeypatch.setattr(
        diagnostics,
        "_fetch_pypi_latest_version",
        lambda: {"version": "1.2.5", "error": None},
    )

    result = _check_tools(include_remote=True)

    csep_status = result["csep"]
    assert ["csep", "--version"] in calls
    assert csep_status["version"] == "csep 1.2.4"
    assert csep_status["installed_version"] == "1.2.4"
    assert csep_status["source_version"] == "1.2.3"
    assert csep_status["pypi_latest_version"] == "1.2.5"
    assert csep_status["runtime_matches_installed"] is False
    assert csep_status["source_matches_pypi"] is False


def test_tools_probe_fetches_pypi_even_when_csep_binary_is_missing(monkeypatch) -> None:
    """Fresh machines still need the latest published version in status output."""
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        diagnostics,
        "_fetch_pypi_latest_version",
        lambda: {"version": "9.9.9", "error": None},
    )

    result = _check_tools(include_remote=True)

    assert result["csep"]["available"] is False
    assert result["csep"]["installed_version"] is None
    assert result["csep"]["pypi_latest_version"] == "9.9.9"


def test_collect_status_runs_cleanly_with_no_home(monkeypatch, tmp_path: Path) -> None:
    """Fresh installs get a JSON-serializable status with only retained sections."""
    monkeypatch.setenv("HOME", str(tmp_path / "freshly-minted"))
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _: None)

    result = collect_status(home=tmp_path / "does-not-exist")

    json.dumps(result)
    assert set(result) == {
        "timestamp",
        "home",
        "plugin_hooks",
        "stable_memory",
        "session_reflection",
        "session_recall",
        "env_provider",
        "tools",
    }
    assert result["env_provider"]["exists"] is False


def test_collect_status_excludes_unknown_runtime_residue(monkeypatch, tmp_path: Path) -> None:
    """Unknown residue under home must not expand the retained status surface."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _: None)
    residue = tmp_path / "projects" / "-tmp-repo" / "unknown-state"
    residue.mkdir(parents=True)
    (residue / "artifact.json").write_text(json.dumps({"status": "ignored"}), encoding="utf-8")

    result = collect_status(home=tmp_path)

    for absent_key in (
        "hooks",
        "legacy_user_hooks",
        "buckets",
        "recent_activity",
    ):
        assert absent_key not in result


def test_collect_status_includes_session_recall_counts(monkeypatch, tmp_path: Path) -> None:
    """Session recall database stats remain visible in status."""
    from codex_self_evolution.session_recall.models import ParsedMessage, ParsedSession
    from codex_self_evolution.session_recall.store import SessionRecallStore

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _: None)
    db_path = tmp_path / "session_recall" / "state.db"
    store = SessionRecallStore(db_path)
    try:
        store.archive(
            ParsedSession(
                session_id="s1",
                session_path=tmp_path / "s1.jsonl",
                cwd=str(tmp_path),
                metadata={
                    "repo_fingerprint": "repo-a",
                    "repo_root": str(tmp_path),
                    "worktree_root": str(tmp_path),
                },
                messages=[
                    ParsedMessage("s1", "m1", 0, "user", "hermes recall", "{}", raw_event_type="message"),
                ],
            )
        )
    finally:
        store.close()

    result = collect_status(home=tmp_path)

    assert result["session_recall"]["db_exists"] is True
    assert result["session_recall"]["session_count"] == 1
    assert result["session_recall"]["message_count"] == 1
    assert result["session_recall"]["ingest_error_count"] == 0
    assert result["session_recall"]["ingest_error_count_total"] == 0
    assert "history" in result["session_recall"]


def test_collect_status_includes_stable_memory_buckets(monkeypatch, tmp_path: Path) -> None:
    """Stable Memory status reports hot memory, legacy USER.md, and refs."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _: None)
    memory = tmp_path / "home" / "projects" / "-tmp-repo" / "memory"
    refs = memory / "refs" / "design"
    refs.mkdir(parents=True)
    (memory / "MEMORY.md").write_text("Remember this.\n", encoding="utf-8")
    (memory / "USER.md").write_text("Legacy ignored.\n", encoding="utf-8")
    (refs / "memory-line.md").write_text("Reference.\n", encoding="utf-8")

    result = collect_status(home=tmp_path / "home")

    status = result["stable_memory"]
    assert status["bucket_count"] == 1
    assert status["total_refs_count"] == 1
    bucket = status["buckets"][0]
    assert bucket["memory_file_exists"] is True
    assert bucket["memory_size_bytes"] == len("Remember this.\n")
    assert bucket["legacy_user_md_ignored"] is True
    assert bucket["refs_count"] == 1


def test_collect_status_reports_latest_memory_validation_warning(monkeypatch, tmp_path: Path) -> None:
    """Latest memory validation status is surfaced without reading secrets."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _: None)
    latest = tmp_path / "home" / "session_reflection" / "latest.json"
    latest.parent.mkdir(parents=True)
    latest.write_text(json.dumps({
        "validation": {
            "status": "failed",
            "boundary_violations": [
                {"reason": "memory_outside_root", "path": "/tmp/memory/USER.md"},
            ],
            "hash_mismatches": [],
        }
    }), encoding="utf-8")

    result = collect_status(home=tmp_path / "home")

    assert result["stable_memory"]["latest_memory_validation_status"] == "failed"
    assert result["stable_memory"]["latest_memory_validation_warning"] == "memory_outside_root"


def test_status_recommends_backfill_when_history_exists_but_db_is_empty(
    monkeypatch, tmp_path: Path,
) -> None:
    """Status should make cold-start recall bootstrap discoverable."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _: None)
    sessions = tmp_path / "codex-sessions"
    sessions.mkdir()
    (sessions / "one.jsonl").write_text(json.dumps({"role": "user", "content": "history"}) + "\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_SESSIONS_ROOT", str(sessions))

    result = collect_status(home=tmp_path / "home")

    history = result["session_recall"]["history"]
    assert history["jsonl_count"] == 1
    assert history["backfill_recommended"] is True
    assert "recall bootstrap" in history["suggested_command"]


def test_cli_status_outputs_valid_json(tmp_path: Path, capsys, monkeypatch) -> None:
    """The status CLI renders the retained diagnostics as JSON."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _: None)

    exit_code = cli.main(["status", "--home", str(tmp_path)])
    assert exit_code == 0

    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert set(parsed) == {
        "timestamp",
        "home",
        "plugin_hooks",
        "stable_memory",
        "session_reflection",
        "session_recall",
        "env_provider",
        "tools",
    }

"""Status diagnostic: read-only, fault-tolerant, and legacy-free."""
from __future__ import annotations

import json
from pathlib import Path

from codex_self_evolution import cli, diagnostics
from codex_self_evolution.diagnostics import (
    HOOK_MARKER,
    _check_env_provider,
    _check_hooks,
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


def test_hooks_probe_detects_both_managed_entries(tmp_path: Path, monkeypatch) -> None:
    """The standalone user-hooks probe still identifies managed entries."""
    fake_home = tmp_path / "home"
    (fake_home / ".codex").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    (fake_home / ".codex" / "hooks.json").write_text(json.dumps({
        "hooks": {
            "Stop": [
                {"hooks": [{"type": "command", "command": "/other/tool/bridge"}]},
                {"hooks": [{"type": "command",
                            "command": f"bash -c ': {HOOK_MARKER}; exec stop'"}]},
            ],
            "SessionStart": [
                {"hooks": [{"type": "command",
                            "command": f"bash -c ': {HOOK_MARKER}; exec sstart'"}]},
            ],
        }
    }), encoding="utf-8")
    result = _check_hooks()
    assert result["stop_installed"] is True
    assert result["session_start_installed"] is True


def test_hooks_probe_reports_missing_file_cleanly(tmp_path: Path, monkeypatch) -> None:
    """Missing user hooks are reported without raising."""
    monkeypatch.setenv("HOME", str(tmp_path))
    result = _check_hooks()
    assert result["exists"] is False
    assert result["stop_installed"] is False
    assert result["session_start_installed"] is False
    assert result["error"] is None


def test_hooks_probe_tolerates_malformed_json(tmp_path: Path, monkeypatch) -> None:
    """Malformed user hooks surface a parse error instead of crashing."""
    fake_home = tmp_path / "home"
    (fake_home / ".codex").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    (fake_home / ".codex" / "hooks.json").write_text("{broken", encoding="utf-8")
    result = _check_hooks()
    assert result["error"] is not None
    assert result["stop_installed"] is False


def test_hooks_probe_ignores_unmarked_entries(tmp_path: Path, monkeypatch) -> None:
    """Marker-less user hooks are not counted as this plugin's hooks."""
    fake_home = tmp_path / "home"
    (fake_home / ".codex").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    (fake_home / ".codex" / "hooks.json").write_text(json.dumps({
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": "some/other/stop-handler"}]}],
        }
    }), encoding="utf-8")
    result = _check_hooks()
    assert result["stop_installed"] is False


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
                            "command": "codex-self-evolution session-start --from-stdin",
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
                            "command": "codex-self-evolution session-stop --from-stdin",
                        },
                    ],
                },
            ],
        },
    }), encoding="utf-8")

    result = diagnostics._check_plugin_hook_bundle(plugin_root)

    assert result["session_start_command"] == (
        "codex-self-evolution session-start --from-stdin"
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
    def fake_which(binary: str) -> str:
        return f"/fake/{binary}"

    def fake_run(argv, **_):
        class R:
            stdout = "opencode\n1.4.0\n"
            stderr = ""
            returncode = 0
        return R()

    monkeypatch.setattr(diagnostics.shutil, "which", fake_which)
    monkeypatch.setattr(diagnostics.subprocess, "run", fake_run)
    result = _check_tools()
    assert result["codex"]["version"] == "opencode"
    assert result["opencode"]["available"] is True
    assert result["pi"]["available"] is True
    assert result["csep"]["available"] is True


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
        "session_reflection",
        "session_recall",
        "env_provider",
        "tools",
    }

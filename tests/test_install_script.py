import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _managed_command(name: str) -> str:
    return f"bash -c ': codex-self-evolution-plugin managed; exec {name}'"


def test_install_script_uses_uv_tool_and_cleans_only_managed_hooks(tmp_path):
    fake_home = tmp_path / "home"
    fake_codex = fake_home / ".codex"
    fake_codex.mkdir(parents=True)
    hooks_json = fake_codex / "hooks.json"
    hooks_json.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {"type": "command", "command": "third-party stop"},
                                {
                                    "type": "command",
                                    "command": _managed_command("old-stop"),
                                }
                            ]
                        },
                    ],
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": _managed_command("old-start"),
                                }
                            ]
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uv_log = tmp_path / "uv.log"
    _write_executable(
        fake_bin / "uv",
        "#!/usr/bin/env bash\n"
        'echo "$@" >> "$UV_LOG"\n'
        'if [ "$1" = tool ] && [ "$2" = dir ] && [ "$3" = --bin ]; then echo "$FAKE_TOOL_BIN"; fi\n'
        "exit 0\n",
    )
    _write_executable(fake_bin / "codex-self-evolution", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "csep", "#!/usr/bin/env bash\nexit 0\n")

    env = {
        **os.environ,
        "HOME": str(fake_home),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "UV_LOG": str(uv_log),
        "FAKE_TOOL_BIN": str(fake_bin),
        "CSEP_INSTALL_SOURCE": str(ROOT),
    }

    proc = subprocess.run(
        ["bash", str(ROOT / "scripts" / "install.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert "tool install --force" in uv_log.read_text(encoding="utf-8")
    assert "codex_hooks" not in proc.stdout
    assert "plugins, hooks, and plugin_hooks features" in proc.stdout

    data = json.loads(hooks_json.read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for entries in data["hooks"].values()
        for entry in entries
        for hook in entry["hooks"]
    ]
    assert commands == ["third-party stop"]

    expected_version = json.loads(
        (ROOT / "src" / "codex_self_evolution" / "plugin_bundle" / ".codex-plugin" / "plugin.json")
        .read_text(encoding="utf-8")
    )["version"]
    plugin_manifest = (
        fake_codex
        / "plugins"
        / "cache"
        / "codex-self-evolution"
        / "codex-self-evolution"
        / expected_version
        / ".codex-plugin"
        / "plugin.json"
    )
    assert plugin_manifest.exists()
    assert json.loads(plugin_manifest.read_text(encoding="utf-8"))["hooks"] == (
        "./.codex-plugin/hooks.json"
    )


def test_install_script_uses_package_plugin_bundle_as_cache_source():
    """Local install must consume the same bundle that ships in the wheel."""
    text = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")

    assert 'PLUGIN_SOURCE="$REPO/src/codex_self_evolution/plugin_bundle"' in text
    assert 'PLUGIN_SOURCE="$REPO/plugins/codex-self-evolution"' not in text


def test_uninstall_codex_hook_filters_managed_hooks_inside_mixed_entry(tmp_path):
    fake_home = tmp_path / "home"
    fake_codex = fake_home / ".codex"
    fake_codex.mkdir(parents=True)
    hooks_json = fake_codex / "hooks.json"
    hooks_json.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {"type": "command", "command": "third-party stop"},
                                {"type": "command", "command": _managed_command("old-stop")},
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(ROOT / "scripts" / "uninstall-codex-hook.sh")],
        env={**os.environ, "HOME": str(fake_home)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    data = json.loads(hooks_json.read_text(encoding="utf-8"))
    assert data == {
        "hooks": {
            "Stop": [
                {"hooks": [{"type": "command", "command": "third-party stop"}]}
            ]
        }
    }


def test_install_codex_hook_script_is_compatibility_wrapper():
    text = (ROOT / "scripts" / "install-codex-hook.sh").read_text(encoding="utf-8")
    assert 'exec "$REPO/scripts/install.sh" "$@"' in text
    assert "upserting Stop + SessionStart hooks" not in text


def test_setup_script_delegates_to_uvx_setup():
    text = (ROOT / "scripts" / "setup.sh").read_text(encoding="utf-8")
    assert "uvx csep setup" in text
    assert "git clone" not in text


def test_runtime_smoke_script_covers_release_gate_paths():
    path = ROOT / "scripts" / "smoke-runtime.sh"
    text = path.read_text(encoding="utf-8")

    assert path.stat().st_mode & 0o111
    assert "session-start --from-stdin" in text
    assert "session-archive --from-hook-payload" in text
    assert "recall sync-claude" in text
    assert "runtime smoke archive needle" in text
    assert "runtime smoke claude needle" in text


def test_runtime_smoke_script_executes_in_hermetic_state_dir(tmp_path):
    """Runtime smoke is a real release gate, not just a text fixture."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    csep_shim = fake_bin / "csep"
    _write_executable(
        csep_shim,
        "#!/usr/bin/env bash\n"
        f'exec "{sys.executable}" -m codex_self_evolution.csep "$@"\n',
    )
    state_dir = tmp_path / "state"

    proc = subprocess.run(
        ["bash", str(ROOT / "scripts" / "smoke-runtime.sh")],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CSEP_BIN": "csep",
            "PYTHON": sys.executable,
            "CSEP_SMOKE_STATE_DIR": str(state_dir),
            "CSEP_SMOKE_SKIP_CONFIG": "1",
            "CSEP_SMOKE_SKIP_STATUS_VERSION_ASSERT": "1",
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stderr
    assert "runtime smoke passed" in proc.stdout
    assert (state_dir / "status.json").exists()
    assert (state_dir / "recall-codex.json").exists()
    assert (state_dir / "recall-claude.json").exists()


def test_makefile_default_python_is_portable():
    """make test should not default to a developer-private interpreter path."""
    text = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "PYTHON ?= python3" in text
    assert "/Users/" not in text


def test_manifest_does_not_reference_missing_fixtures():
    """Source distribution metadata should not include untracked fixture paths."""
    text = (ROOT / "MANIFEST.in").read_text(encoding="utf-8") if (ROOT / "MANIFEST.in").exists() else ""

    assert "tests/fixtures" not in text

import json
import os
import subprocess
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

    data = json.loads(hooks_json.read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for entries in data["hooks"].values()
        for entry in entries
        for hook in entry["hooks"]
    ]
    assert commands == ["third-party stop"]

    plugin_manifest = (
        fake_codex
        / "plugins"
        / "cache"
        / "codex-self-evolution"
        / "codex-self-evolution"
        / "0.7.1"
        / ".codex-plugin"
        / "plugin.json"
    )
    assert plugin_manifest.exists()
    assert json.loads(plugin_manifest.read_text(encoding="utf-8"))["hooks"] == (
        "./.codex-plugin/hooks.json"
    )


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

import json
import tomllib
from pathlib import Path

from codex_self_evolution import diagnostics


ROOT = Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_root_plugin_manifest_points_to_existing_hooks_file():
    manifest_path = ROOT / ".codex-plugin" / "plugin.json"
    manifest = _load_json(manifest_path)

    assert manifest["hooks"] == "./.codex-plugin/hooks.json"
    assert manifest["skills"] == "./skills/"
    hooks_path = ROOT / ".codex-plugin" / "hooks.json"
    assert hooks_path.exists()
    assert (ROOT / "skills" / "csep-session-recall" / "SKILL.md").exists()


def test_plugin_hooks_use_local_cli_not_uvx_or_tmp_placeholders():
    hooks = _load_json(ROOT / ".codex-plugin" / "hooks.json")["hooks"]

    assert set(hooks) == {"SessionStart", "Stop"}
    session_cmd = hooks["SessionStart"][0]["hooks"][0]["command"]
    stop_cmd = hooks["Stop"][0]["hooks"][0]["command"]

    assert session_cmd == "csep session-start --from-stdin"
    assert stop_cmd == "csep session-stop --from-stdin"
    assert "uvx" not in json.dumps(hooks)
    assert "/tmp/csep-" not in json.dumps(hooks)


def test_plugin_manifest_commands_use_local_cli_not_uvx():
    manifest = _load_json(ROOT / ".codex-plugin" / "plugin.json")

    commands = {entry["name"]: entry["command"] for entry in manifest["commands"]}
    assert commands == {
        "session-start": "csep session-start --from-stdin",
        "session-stop": "csep session-stop --from-stdin",
        "status": "csep status",
        "session-reflect-status": "csep session-reflect --status",
        "recall": (
            'csep recall "$CODEX_RECALL_QUERY" --cwd "$CODEX_CWD" '
            '--state-dir "$CODEX_STATE_DIR"'
        ),
    }
    assert "scheduler" not in manifest
    assert "stop-review" not in json.dumps(manifest)
    assert "compile-preflight" not in json.dumps(manifest)
    assert "skill-synthesize" not in json.dumps(manifest)
    assert "uvx" not in json.dumps(manifest)
    assert "uvx --from codex-self-evolution-plugin" not in json.dumps(manifest)


def test_packaged_plugin_copy_matches_root_hook_bundle():
    root_manifest = _load_json(ROOT / ".codex-plugin" / "plugin.json")
    packaged_manifest = _load_json(
        ROOT / "plugins" / "codex-self-evolution" / ".codex-plugin" / "plugin.json"
    )
    root_hooks = _load_json(ROOT / ".codex-plugin" / "hooks.json")
    packaged_hooks = _load_json(
        ROOT / "plugins" / "codex-self-evolution" / ".codex-plugin" / "hooks.json"
    )

    assert packaged_manifest == root_manifest
    assert packaged_hooks == root_hooks
    assert (ROOT / "plugins" / "codex-self-evolution" / "skills" / "csep-session-recall" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == (ROOT / "skills" / "csep-session-recall" / "SKILL.md").read_text(encoding="utf-8")


def test_default_plugin_root_falls_back_to_package_bundle(tmp_path, monkeypatch):
    package_dir = tmp_path / "site-packages" / "codex_self_evolution"
    metadata_dir = package_dir / "plugin_bundle" / ".codex-plugin"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "plugin.json").write_text(json.dumps({
        "hooks": "./.codex-plugin/hooks.json",
    }), encoding="utf-8")
    (metadata_dir / "hooks.json").write_text(json.dumps({
        "hooks": {
            "SessionStart": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "csep session-start --from-stdin",
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
    monkeypatch.setattr(diagnostics, "__file__", str(package_dir / "diagnostics.py"))

    result = diagnostics._check_plugin_hook_bundle()

    assert result["manifest_path"] == str(metadata_dir / "plugin.json")
    assert result["manifest_exists"] is True
    assert result["hooks_file_exists"] is True
    assert result["session_start_declared"] is True
    assert result["stop_declared"] is True


def test_package_plugin_bundle_matches_repo_plugin_bundle():
    repo_metadata = ROOT / "plugins" / "codex-self-evolution" / ".codex-plugin"
    package_metadata = ROOT / "src" / "codex_self_evolution" / "plugin_bundle" / ".codex-plugin"

    for filename in ("plugin.json", "hooks.json"):
        assert _load_json(package_metadata / filename) == _load_json(
            repo_metadata / filename
        )
    assert (
        ROOT / "src" / "codex_self_evolution" / "plugin_bundle" / "skills" / "csep-session-recall" / "SKILL.md"
    ).read_text(encoding="utf-8") == (
        ROOT / "plugins" / "codex-self-evolution" / "skills" / "csep-session-recall" / "SKILL.md"
    ).read_text(encoding="utf-8")


def test_pyproject_includes_package_plugin_bundle_data():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    package_data = pyproject["tool"]["setuptools"]["package-data"][
        "codex_self_evolution"
    ]

    assert "plugin_bundle/.codex-plugin/plugin.json" in package_data
    assert "plugin_bundle/.codex-plugin/hooks.json" in package_data
    assert "plugin_bundle/skills/csep-session-recall/SKILL.md" in package_data

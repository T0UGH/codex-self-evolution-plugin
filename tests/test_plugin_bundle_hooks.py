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
    hooks_path = ROOT / ".codex-plugin" / "hooks.json"
    assert hooks_path.exists()


def test_plugin_hooks_use_local_cli_not_uvx_or_tmp_placeholders():
    hooks = _load_json(ROOT / ".codex-plugin" / "hooks.json")["hooks"]

    assert set(hooks) == {"SessionStart", "Stop"}
    session_cmd = hooks["SessionStart"][0]["hooks"][0]["command"]
    stop_cmd = hooks["Stop"][0]["hooks"][0]["command"]

    assert session_cmd == "codex-self-evolution session-start --from-stdin"
    assert stop_cmd == "codex-self-evolution stop-review --from-stdin"
    assert "uvx" not in json.dumps(hooks)
    assert "/tmp/csep-" not in json.dumps(hooks)


def test_plugin_manifest_commands_use_local_cli_not_uvx():
    manifest = _load_json(ROOT / ".codex-plugin" / "plugin.json")

    commands = {entry["name"]: entry["command"] for entry in manifest["commands"]}
    assert commands == {
        "session-start": "codex-self-evolution session-start --from-stdin",
        "stop-review": "codex-self-evolution stop-review --from-stdin",
        "compile-preflight": (
            'codex-self-evolution compile-preflight --state-dir "$CODEX_STATE_DIR"'
        ),
        "compile": (
            'codex-self-evolution compile --once --state-dir "$CODEX_STATE_DIR" '
            "--backend agent:opencode"
        ),
        "scan": "codex-self-evolution scan --backend agent:opencode",
        "status": "codex-self-evolution status",
        "recall": (
            'csep recall "$CODEX_RECALL_QUERY" --cwd "$CODEX_CWD" '
            '--state-dir "$CODEX_STATE_DIR"'
        ),
        "recall-trigger": (
            'codex-self-evolution recall-trigger --query "$CODEX_RECALL_QUERY" '
            '--cwd "$CODEX_CWD" --state-dir "$CODEX_STATE_DIR"'
        ),
    }

    scheduler = manifest["scheduler"]
    assert scheduler["scan_command"] == (
        "codex-self-evolution scan --backend agent:opencode"
    )
    assert scheduler["preflight_command"] == (
        'codex-self-evolution compile-preflight --state-dir "$CODEX_STATE_DIR"'
    )
    assert scheduler["compile_command"] == (
        'codex-self-evolution compile --once --state-dir "$CODEX_STATE_DIR" '
        "--backend agent:opencode"
    )
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
                            "command": "codex-self-evolution session-start --from-stdin",
                        },
                    ],
                },
            ],
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "codex-self-evolution stop-review --from-stdin",
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


def test_pyproject_includes_package_plugin_bundle_data():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    package_data = pyproject["tool"]["setuptools"]["package-data"][
        "codex_self_evolution"
    ]

    assert "plugin_bundle/.codex-plugin/plugin.json" in package_data
    assert "plugin_bundle/.codex-plugin/hooks.json" in package_data

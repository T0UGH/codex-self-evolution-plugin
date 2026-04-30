import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_root_plugin_manifest_points_to_existing_hooks_file():
    manifest_path = ROOT / ".codex-plugin" / "plugin.json"
    manifest = _load_json(manifest_path)

    assert manifest["hooks"] == "./hooks.json"
    hooks_path = manifest_path.parent / "hooks.json"
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
    packaged_hooks = _load_json(ROOT / "plugins" / "codex-self-evolution" / "hooks.json")

    assert packaged_manifest == root_manifest
    assert packaged_hooks == root_hooks

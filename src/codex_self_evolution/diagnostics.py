"""Read-only status probe for the retained plugin runtime.

The status surface intentionally reports only the current session-oriented
system: bundled plugin hook readiness, session reflection state, session recall
stats, provider key names, and local tool versions. Removed runtime surfaces
are excluded so status cannot imply unsupported systems still exist.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import get_home_dir
from .session_reflection.runner import session_reflection_status
from .session_recall.archive import default_db_path
from .session_recall.store import SessionRecallStore

# Keys we recognize from .env.provider.example. Not exhaustive — other env
# vars a user might add (custom MINIMAX_BASE_URL etc.) are reported as
# "other" so they show up in the report without leaking values.
WELL_KNOWN_API_KEYS = (
    "MINIMAX_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "KIMI_API_KEY",
)


def collect_status(
    home: str | Path | None = None,
) -> dict[str, Any]:
    """Assemble the retained diagnostic snapshot without mutating state."""
    home_dir = Path(home).expanduser().resolve() if home else get_home_dir()
    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "home": str(home_dir),
        "plugin_hooks": _check_plugin_hook_bundle(),
        "session_reflection": session_reflection_status(home=home_dir),
        "session_recall": _check_session_recall(home_dir),
        "env_provider": _check_env_provider(home_dir),
        "tools": _check_tools(),
    }


def _check_plugin_hook_bundle(plugin_root: Path | None = None) -> dict[str, Any]:
    root = plugin_root or _default_plugin_root()
    manifest_path = root / ".codex-plugin" / "plugin.json"
    result: dict[str, Any] = {
        "plugin_root": str(root),
        "manifest_path": str(manifest_path),
        "manifest_exists": manifest_path.exists(),
        "hooks_path": None,
        "hooks_file_exists": False,
        "session_start_declared": False,
        "stop_declared": False,
        "session_start_command": None,
        "stop_command": None,
        "uses_local_cli": False,
        "uses_uvx": False,
        "error": None,
    }
    if not manifest_path.exists():
        return result

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        result["error"] = f"failed to parse plugin manifest: {exc}"
        return result
    if not isinstance(manifest, dict):
        result["error"] = "plugin manifest is not a JSON object"
        return result

    hooks_value = manifest.get("hooks")
    if not isinstance(hooks_value, str) or not hooks_value:
        result["error"] = "plugin manifest does not declare a hooks file"
        return result

    hooks_path = Path(hooks_value)
    if not hooks_path.is_absolute():
        hooks_path = root / hooks_path
    result["hooks_path"] = str(hooks_path)
    result["hooks_file_exists"] = hooks_path.exists()
    if not hooks_path.exists():
        return result

    try:
        hook_data = json.loads(hooks_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        result["error"] = f"failed to parse plugin hooks file: {exc}"
        return result
    hooks = hook_data.get("hooks") if isinstance(hook_data, dict) else None
    if hooks is None:
        hooks = {}
    if not isinstance(hooks, dict):
        result["error"] = "plugin hooks file has non-object hooks section"
        return result

    all_commands: list[str] = []
    for event_name, result_key, command_key in (
        ("SessionStart", "session_start_declared", "session_start_command"),
        ("Stop", "stop_declared", "stop_command"),
    ):
        event_commands = _commands_for_hook_event(hooks.get(event_name))
        all_commands.extend(event_commands)
        if event_commands:
            result[result_key] = True
            result[command_key] = event_commands[0]

    result["uses_local_cli"] = any(
        command.startswith("csep ") or command.startswith("codex-self-evolution ")
        for command in all_commands
    )
    result["uses_uvx"] = any("uvx" in command for command in all_commands)
    return result


def _default_plugin_root() -> Path:
    override = os.environ.get("CSEP_PLUGIN_ROOT")
    if override:
        return Path(override).expanduser()
    repo_root = Path(__file__).resolve().parents[2]
    source_tree_root = repo_root / "plugins" / "codex-self-evolution"
    if (source_tree_root / ".codex-plugin" / "plugin.json").exists():
        return source_tree_root
    return Path(__file__).resolve().parent / "plugin_bundle"


def _commands_for_hook_event(entries: Any) -> list[str]:
    if not isinstance(entries, list):
        return []

    commands: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        hooks = entry.get("hooks")
        if not isinstance(hooks, list):
            continue
        for hook in hooks:
            if not isinstance(hook, dict):
                continue
            command = hook.get("command")
            if isinstance(command, str) and command:
                commands.append(command)
    return commands


# ---------- session recall ----------------------------------------------


def _check_session_recall(home: str | Path | None = None) -> dict[str, Any]:
    db_path = default_db_path(home=home)
    if not db_path.exists():
        return {
            "db_exists": False,
            "db_path": str(db_path),
            "session_count": 0,
            "message_count": 0,
            "ingest_error_count": 0,
            "latest_error": None,
        }
    try:
        store = SessionRecallStore(db_path)
        try:
            return store.stats()
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001 - status must never crash.
        return {
            "db_exists": True,
            "db_path": str(db_path),
            "error": f"{type(exc).__name__}: {exc}",
        }


# ---------- .env.provider key presence ----------------------------------


def _check_env_provider(home_dir: Path) -> dict[str, Any]:
    env_path = home_dir / ".env.provider"
    result: dict[str, Any] = {
        "path": str(env_path),
        "exists": env_path.exists(),
        "keys_set": [],
        "keys_unset": list(WELL_KNOWN_API_KEYS),
        "other_keys_set": [],
        "error": None,
    }
    if not env_path.exists():
        return result
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError as exc:
        result["error"] = f"failed to read env file: {exc}"
        return result

    # Deliberately a restrictive parser: we do NOT source the file (that's
    # arbitrary code execution if the user ever pasted something weird).
    # Just match `KEY=value` / `export KEY=value` lines and record which
    # keys have non-empty values. Values themselves never leave this function.
    key_re = re.compile(r"^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$")
    found: dict[str, bool] = {}
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = key_re.match(raw_line)
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        # Strip surrounding quotes since users may write KEY="abc" or KEY='abc'.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        # Treat empty / whitespace-only as "not set" — matches how a shell
        # would see the variable after sourcing the file (empty env var is
        # effectively unset for our purposes).
        found[key] = bool(value.strip())

    result["keys_set"] = [k for k in WELL_KNOWN_API_KEYS if found.get(k)]
    result["keys_unset"] = [k for k in WELL_KNOWN_API_KEYS if not found.get(k)]
    result["other_keys_set"] = [
        k for k, v in found.items() if v and k not in WELL_KNOWN_API_KEYS
    ]
    return result


# ---------- CLI tool versions -------------------------------------------


def _check_tools() -> dict[str, Any]:
    return {
        "codex": _probe_version(["codex", "--version"]),
        "opencode": _probe_version(["opencode", "--version"]),
        "pi": _probe_version(["pi", "--version"]),
        "csep": _probe_version(["csep", "--help"]),
    }


def _probe_version(argv: list[str]) -> dict[str, Any]:
    binary = argv[0]
    path = shutil.which(binary)
    if path is None:
        return {"available": False, "path": None, "version": None, "error": "not on PATH"}
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": True, "path": path, "version": None, "error": str(exc)}
    combined = (proc.stdout + "\n" + proc.stderr).strip()
    if proc.returncode != 0 and not combined:
        return {
            "available": True, "path": path, "version": None,
            "error": f"{binary} --version exited {proc.returncode}",
        }
    # Grab the first line; CLIs like to print banners. "codex-cli 0.122.0"
    # or "1.4.0" — caller can eyeball either.
    first_line = combined.splitlines()[0].strip() if combined else ""
    return {"available": True, "path": path, "version": first_line, "error": None}

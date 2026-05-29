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
import tomllib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .config import PROJECTS_SUBDIR, SESSION_REFLECTION_SUBDIR, get_home_dir
from .config_file import ConfigError, config_to_dict, load_config
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
    config_status = _check_config(home_dir)
    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "home": str(home_dir),
        "config": config_status,
        "plugin_hooks": _check_plugin_hook_bundle(),
        "stable_memory": _check_stable_memory(home_dir),
        "session_reflection": session_reflection_status(home=home_dir),
        "session_recall": _check_session_recall(home_dir),
        "env_provider": _check_env_provider(home_dir),
        "tools": _check_tools(include_remote=True),
    }


def _check_config(home_dir: Path) -> dict[str, Any]:
    """Return resolved feature-switch status without leaking sensitive values."""
    try:
        loaded = load_config(home=home_dir)
    except ConfigError as exc:
        return {
            "status": "parse_error",
            "error": str(exc),
            "feature_switches": None,
        }
    resolved = config_to_dict(loaded.config)
    return {
        "status": "ok" if not loaded.warnings else "warnings",
        "config_path": str(loaded.config_path),
        "config_exists": loaded.config_exists,
        "feature_switches": {
            "stable_memory": bool(resolved["stable_memory"]["enabled"]),
            "session_recall": bool(resolved["session_recall"]["enabled"]),
            "session_reflection": bool(resolved["session_reflection"]["enabled"]),
        },
        "sub_switches": {
            "session_recall.session_start_policy": bool(
                resolved["session_recall"]["session_start_policy"]
            ),
            "session_recall.manual_query": bool(resolved["session_recall"]["manual_query"]),
            "session_recall.stop_hook_archive": bool(resolved["session_recall"]["stop_hook_archive"]),
            "session_reflection.trigger.enabled": bool(
                resolved["session_reflection"]["trigger"]["enabled"]
            ),
            "session_reflection.trigger.memory_review": bool(
                resolved["session_reflection"]["trigger"]["memory_review"]
            ),
            "session_reflection.trigger.skill_review": bool(
                resolved["session_reflection"]["trigger"]["skill_review"]
            ),
        },
        "warnings": loaded.warnings,
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


# ---------- stable memory -----------------------------------------------


def _check_stable_memory(home_dir: Path) -> dict[str, Any]:
    """Return read-only Stable Memory status for known project buckets."""
    projects_root = home_dir / PROJECTS_SUBDIR
    result: dict[str, Any] = {
        "projects_root": str(projects_root),
        "exists": projects_root.exists(),
        "bucket_count": 0,
        "total_refs_count": 0,
        "buckets": [],
        "latest_memory_validation_status": None,
        "latest_memory_validation_warning": None,
    }
    if projects_root.exists():
        buckets: list[dict[str, Any]] = []
        for bucket in sorted(projects_root.iterdir(), key=lambda path: path.name):
            if not bucket.is_dir():
                continue
            memory_dir = bucket / "memory"
            memory_file = memory_dir / "MEMORY.md"
            legacy_user_file = memory_dir / "USER.md"
            refs_dir = memory_dir / "refs"
            refs_count = _count_memory_refs(refs_dir)
            buckets.append({
                "bucket": bucket.name,
                "memory_dir": str(memory_dir),
                "memory_file_exists": memory_file.is_file(),
                "memory_size_bytes": _file_size(memory_file),
                "legacy_user_md_ignored": legacy_user_file.exists(),
                "refs_dir_exists": refs_dir.is_dir(),
                "refs_count": refs_count,
            })
        result["buckets"] = buckets
        result["bucket_count"] = len(buckets)
        result["total_refs_count"] = sum(int(bucket["refs_count"]) for bucket in buckets)
    result.update(_latest_memory_validation(home_dir))
    return result


def _count_memory_refs(refs_dir: Path) -> int:
    """Count Markdown files under memory/refs without failing status."""
    if not refs_dir.is_dir():
        return 0
    try:
        return sum(1 for path in refs_dir.rglob("*.md") if path.is_file())
    except OSError:
        return 0


def _file_size(path: Path) -> int:
    """Return file size in bytes, or 0 if the file is absent/unreadable."""
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _latest_memory_validation(home_dir: Path) -> dict[str, Any]:
    """Return latest memory-scoped validation status and warning, if known."""
    latest_path = home_dir / SESSION_REFLECTION_SUBDIR / "latest.json"
    result: dict[str, Any] = {
        "latest_memory_validation_status": None,
        "latest_memory_validation_warning": None,
    }
    if not latest_path.is_file():
        return result
    try:
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result["latest_memory_validation_warning"] = "latest_job_unreadable"
        return result
    validation = latest.get("validation") if isinstance(latest, dict) else None
    if not isinstance(validation, dict):
        return result
    result["latest_memory_validation_status"] = validation.get("status")
    result["latest_memory_validation_warning"] = _memory_validation_warning(validation)
    return result


def _memory_validation_warning(validation: dict[str, Any]) -> str | None:
    """Summarize the first memory-scoped validation issue."""
    for item in validation.get("boundary_violations") or []:
        if isinstance(item, dict) and str(item.get("reason") or "").startswith("memory_"):
            return str(item.get("reason") or "memory_boundary_violation")
    for item in validation.get("hash_mismatches") or []:
        path = str(item.get("path") or "") if isinstance(item, dict) else ""
        if "/memory/" in path or path.endswith("/MEMORY.md"):
            return str(item.get("reason") or "memory_hash_mismatch")
    return None


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
            "ingest_error_count_total": 0,
            "latest_error": None,
            "latest_ingest_run": None,
            "history": _check_codex_session_history(session_count=0),
        }
    try:
        store = SessionRecallStore(db_path)
        try:
            stats = store.stats()
            stats["history"] = _check_codex_session_history(
                session_count=int(stats.get("session_count") or 0),
            )
            return stats
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001 - status must never crash.
        return {
            "db_exists": True,
            "db_path": str(db_path),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _check_codex_session_history(*, session_count: int) -> dict[str, Any]:
    root = Path(os.environ.get("CODEX_SESSIONS_ROOT") or Path.home() / ".codex" / "sessions").expanduser()
    result: dict[str, Any] = {
        "root": str(root),
        "exists": root.exists(),
        "jsonl_count": 0,
        "backfill_recommended": False,
        "suggested_command": None,
        "error": None,
    }
    if not root.exists():
        return result
    try:
        jsonl_count = sum(1 for _ in root.rglob("*.jsonl"))
    except OSError as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    result["jsonl_count"] = jsonl_count
    result["backfill_recommended"] = jsonl_count > 0 and session_count < min(jsonl_count, 10)
    if result["backfill_recommended"]:
        result["suggested_command"] = f"csep recall bootstrap --root {root} --since-days 30"
    return result


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


def _check_tools(*, include_remote: bool = False) -> dict[str, Any]:
    tools = {
        "codex": _probe_version(["codex", "--version"]),
        "opencode": _probe_version(["opencode", "--version"]),
        "pi": _probe_version(["pi", "--version"]),
        "csep": _probe_version(["csep", "--version"]),
    }
    tools["csep"].update(_check_csep_version_details(tools["csep"], include_remote=include_remote))
    return tools


def _check_csep_version_details(csep_probe: dict[str, Any], *, include_remote: bool) -> dict[str, Any]:
    source = _read_cwd_source_version(Path.cwd())
    if include_remote and csep_probe.get("available"):
        pypi = _fetch_pypi_latest_version()
    else:
        pypi = {"version": None, "error": None}
    installed_version = _parse_csep_version_line(csep_probe.get("version"))
    source_version = source.get("version")
    pypi_version = pypi.get("version")
    return {
        "installed_version": installed_version,
        "runtime_version": __version__,
        "source_version": source_version,
        "source_path": source.get("path"),
        "source_error": source.get("error"),
        "pypi_latest_version": pypi_version,
        "pypi_error": pypi.get("error"),
        "runtime_matches_installed": _versions_match(__version__, installed_version),
        "runtime_matches_source": _versions_match(__version__, source_version),
        "runtime_matches_pypi": _versions_match(__version__, pypi_version),
        "source_matches_pypi": _versions_match(source_version, pypi_version),
    }


def _parse_csep_version_line(line: Any) -> str | None:
    if not isinstance(line, str):
        return None
    match = re.search(r"\bcsep\s+([0-9][A-Za-z0-9_.!+~-]*)\b", line)
    return match.group(1) if match else None


def _versions_match(left: str | None, right: str | None) -> bool | None:
    if not left or not right:
        return None
    return left == right


def _read_cwd_source_version(cwd: Path) -> dict[str, Any]:
    for directory in (cwd, *cwd.parents):
        pyproject = directory / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            return {"version": None, "path": str(pyproject), "error": str(exc)}
        project = data.get("project") if isinstance(data, dict) else None
        if not isinstance(project, dict) or project.get("name") != "csep":
            return {"version": None, "path": None, "error": None}
        version = project.get("version")
        return {
            "version": version if isinstance(version, str) else None,
            "path": str(pyproject),
            "error": None if isinstance(version, str) else "project.version missing",
        }
    return {"version": None, "path": None, "error": None}


def _fetch_pypi_latest_version() -> dict[str, str | None]:
    request = urllib.request.Request(
        "https://pypi.org/simple/csep/",
        headers={
            "Accept": "application/vnd.pypi.simple.v1+json",
            "Cache-Control": "no-cache",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return {"version": None, "error": str(exc)}
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, list):
        return {"version": None, "error": "unexpected PyPI simple response"}
    versions: set[str] = set()
    for item in files:
        filename = item.get("filename") if isinstance(item, dict) else None
        version = _version_from_pypi_filename(filename)
        if version:
            versions.add(version)
    sorted_versions = sorted(versions, key=_version_sort_key)
    if not sorted_versions:
        return {"version": None, "error": "no csep releases found"}
    return {"version": sorted_versions[-1], "error": None}


def _version_from_pypi_filename(filename: str | None) -> str | None:
    if not filename or not filename.startswith("csep-"):
        return None
    remainder = filename.removeprefix("csep-")
    if remainder.endswith(".tar.gz"):
        return remainder.removesuffix(".tar.gz")
    if remainder.endswith(".zip"):
        return remainder.removesuffix(".zip")
    if remainder.endswith(".whl"):
        parts = remainder.split("-")
        return parts[0] if parts else None
    return None


def _version_sort_key(version: str) -> tuple[tuple[int, int | str], ...]:
    pieces: list[tuple[int, int | str]] = []
    for piece in re.split(r"([0-9]+)", version):
        if not piece:
            continue
        pieces.append((0, int(piece)) if piece.isdigit() else (1, piece))
    return tuple(pieces)


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

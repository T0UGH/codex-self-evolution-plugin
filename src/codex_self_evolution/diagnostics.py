"""Read-only status probe for the whole plugin installation.

Answers the two questions a user asks immediately after installing:
"did it install correctly?" and "is it doing anything right now?". Covers
every piece of state a running install has:

- Which Codex hooks are wired in ``~/.codex/hooks.json``
- Whether the launchd scheduler is loaded and plist on disk
- Whether ``~/.codex-self-evolution/.env.provider`` has API keys set
  (reports which names are non-empty; NEVER prints values)
- What the ``codex`` and ``opencode`` CLIs report as their versions
- For every per-project bucket: pending/done/failed counts + last
  compile receipt summary

Pure read-only. Every external probe (subprocess, file read, JSON parse)
is individually fault-tolerant — a broken launchctl on one machine must
not prevent the rest of the report from rendering.
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

from .config import PROJECTS_SUBDIR, get_home_dir, is_archived_bucket

HOOK_MARKER = "codex-self-evolution-plugin managed"
LAUNCHD_LABEL = "com.codex-self-evolution.preflight"

# Keys we recognize from .env.provider.example. Not exhaustive — other env
# vars a user might add (custom MINIMAX_BASE_URL etc.) are reported as
# "other" so they show up in the report without leaking values.
WELL_KNOWN_API_KEYS = (
    "MINIMAX_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
)


def collect_status(home: str | Path | None = None) -> dict[str, Any]:
    """Assemble the full diagnostic snapshot. Raises nothing."""
    home_dir = Path(home).expanduser().resolve() if home else get_home_dir()
    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "home": str(home_dir),
        "hooks": _check_hooks(),
        "scheduler": _check_scheduler(),
        "env_provider": _check_env_provider(home_dir),
        "tools": _check_tools(),
        "buckets": _list_buckets(home_dir),
    }


# ---------- hooks --------------------------------------------------------


def _check_hooks() -> dict[str, Any]:
    hooks_path = Path.home() / ".codex" / "hooks.json"
    result: dict[str, Any] = {
        "file": str(hooks_path),
        "exists": hooks_path.exists(),
        "stop_installed": False,
        "session_start_installed": False,
        "error": None,
    }
    if not hooks_path.exists():
        return result
    try:
        data = json.loads(hooks_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        result["error"] = f"failed to parse hooks.json: {exc}"
        return result
    hooks = (data.get("hooks") or {}) if isinstance(data, dict) else {}
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                if HOOK_MARKER not in cmd:
                    continue
                if event == "Stop":
                    result["stop_installed"] = True
                elif event == "SessionStart":
                    result["session_start_installed"] = True
    return result


# ---------- launchd scheduler -------------------------------------------


def _check_scheduler() -> dict[str, Any]:
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    result: dict[str, Any] = {
        "label": LAUNCHD_LABEL,
        "plist_path": str(plist_path),
        "plist_exists": plist_path.exists(),
        "loaded": False,
        "error": None,
    }
    if shutil.which("launchctl") is None:
        # Non-macOS hosts (running plugin in Docker / CI): scheduler is
        # irrelevant, report honestly rather than pretending it's broken.
        result["error"] = "launchctl not available on this host"
        return result
    try:
        proc = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["error"] = f"launchctl list failed: {exc}"
        return result
    # launchctl list rows look like: `PID\tSTATUS\tLABEL`. We only care about
    # "does our label appear", not about PID or status — launchd might have
    # our job loaded but not currently running (between ticks).
    for line in proc.stdout.splitlines():
        if line.strip().endswith(LAUNCHD_LABEL):
            result["loaded"] = True
            break
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


def _check_tools() -> dict[str, Any]:
    return {
        "codex": _probe_version(["codex", "--version"]),
        "opencode": _probe_version(["opencode", "--version"]),
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


# ---------- per-project buckets -----------------------------------------


def _list_buckets(home_dir: Path) -> list[dict[str, Any]]:
    projects_dir = home_dir / PROJECTS_SUBDIR
    if not projects_dir.is_dir():
        return []
    buckets: list[dict[str, Any]] = []
    for bucket_path in sorted(projects_dir.iterdir()):
        if not bucket_path.is_dir():
            continue
        buckets.append(_inspect_bucket(bucket_path))
    return buckets


def _inspect_bucket(bucket: Path) -> dict[str, Any]:
    suggestions_dir = bucket / "suggestions"
    counts = {
        "pending": _count_json(suggestions_dir / "pending"),
        "processing": _count_json(suggestions_dir / "processing"),
        "done": _count_json(suggestions_dir / "done"),
        "failed": _count_json(suggestions_dir / "failed"),
        "discarded": _count_json(suggestions_dir / "discarded"),
    }
    return {
        "project": bucket.name,
        "state_dir": str(bucket),
        "archived": is_archived_bucket(bucket.name),
        "counts": counts,
        "last_receipt": _read_last_receipt(bucket / "compiler" / "last_receipt.json"),
    }


def _count_json(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    try:
        return sum(1 for p in directory.iterdir() if p.is_file() and p.suffix == ".json")
    except OSError:
        return 0


def _read_last_receipt(receipt_path: Path) -> dict[str, Any] | None:
    # Returns None when no compile has ever run in this bucket, which is
    # different from "compile ran but failed" (status lives IN the receipt).
    if not receipt_path.is_file():
        return None
    try:
        data = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        mtime = receipt_path.stat().st_mtime
        timestamp = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except OSError:
        timestamp = None
    # Surface only the summary fields — the full receipt can include item
    # receipts with absolute paths we don't want printed by default. Users
    # who need detail can cat the file directly.
    return {
        "path": str(receipt_path),
        "timestamp": timestamp,
        "run_status": data.get("run_status"),
        "backend": data.get("backend"),
        "fallback_backend": data.get("fallback_backend"),
        "processed_count": data.get("processed_count"),
        "skip_reason": data.get("skip_reason"),
    }

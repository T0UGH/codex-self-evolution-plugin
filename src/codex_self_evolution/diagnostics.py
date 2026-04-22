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


def collect_status(
    home: str | Path | None = None,
    recent_window_hours: float = 24.0,
) -> dict[str, Any]:
    """Assemble the full diagnostic snapshot. Raises nothing.

    ``recent_window_hours`` bounds the plugin.log scan — only entries newer
    than ``now - window`` contribute to the recent-activity aggregate. 24h
    is the default because the log rotates daily, so a full active-day view
    fits in exactly one file without the scanner stitching across rotations.
    """
    home_dir = Path(home).expanduser().resolve() if home else get_home_dir()
    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "home": str(home_dir),
        "hooks": _check_hooks(),
        "scheduler": _check_scheduler(),
        "env_provider": _check_env_provider(home_dir),
        "tools": _check_tools(),
        "buckets": _list_buckets(home_dir),
        "recent_activity": _recent_activity(home_dir, window_hours=recent_window_hours),
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
        "memory_action_stats": data.get("memory_action_stats") or {},
    }


# ---------- recent activity from plugin.log -----------------------------

# Fields we actually care about when rolling up the log. Anything outside
# this set is ignored so new log shapes don't silently distort the metrics.
_LOG_KINDS = {"stop-review", "scan", "compile", "migrate-worktrees", "session-start", "status"}


def _recent_activity(home_dir: Path, window_hours: float = 24.0) -> dict[str, Any]:
    """Roll up the tail of ``plugin.log`` into a health snapshot.

    Returns a dict shape every status consumer can depend on, even when
    the log is missing / unreadable / empty. Individual malformed lines
    are skipped so one bad JSON payload never breaks the whole summary.

    The keys surfaced here are the same ones emitted by
    :func:`cli._observability_extras` so the dashboard reads back what
    the invocation path writes — stop-review families, scan aggregate,
    retry counts.
    """
    log_path = home_dir / "logs" / "plugin.log"
    empty = {
        "log_path": str(log_path),
        "window_hours": window_hours,
        "log_available": False,
        "stop_review": {"total": 0, "succeeded": 0, "failed": 0,
                        "suggestions_emitted": 0,
                        "families": {"memory_updates": 0, "recall_candidate": 0, "skill_action": 0},
                        "skipped": 0, "by_error_type": {}},
        "scan": {"total": 0, "with_processed_buckets": 0, "with_fallback": 0,
                 "memory_actions": {"add": 0, "replace": 0, "remove": 0},
                 "scopes": {"user": 0, "global": 0},
                 "suggestions": 0, "discarded": 0},
        "retries": {"total": 0, "by_reason": {}},
    }
    if not log_path.is_file():
        return empty
    try:
        raw = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return empty

    cutoff = datetime.now(timezone.utc).timestamp() - (window_hours * 3600)
    summary = {
        **empty,
        "log_available": True,
    }
    # Reset nested dicts so we don't mutate the shared empty template.
    summary["stop_review"] = {**empty["stop_review"],
                              "families": dict(empty["stop_review"]["families"]),
                              "by_error_type": {}}
    summary["scan"] = {**empty["scan"],
                       "memory_actions": dict(empty["scan"]["memory_actions"]),
                       "scopes": dict(empty["scan"]["scopes"])}
    summary["retries"] = {**empty["retries"], "by_reason": {}}

    for line in raw.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        ts = event.get("ts")
        if isinstance(ts, str):
            try:
                # ``plugin.log`` writes ``YYYY-MM-DDTHH:MM:SS.ffffffZ``; the
                # 'Z' isn't parsed by fromisoformat pre-3.11 so swap it.
                event_time = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            if event_time < cutoff:
                continue

        msg = str(event.get("msg") or "")
        # Retry signals are INFO log lines from the provider, identified by
        # their message prefix — not by a `kind` field.
        if "retrying after" in msg:
            summary["retries"]["total"] += 1
            reason = _classify_retry_reason(msg)
            summary["retries"]["by_reason"][reason] = summary["retries"]["by_reason"].get(reason, 0) + 1
            continue

        kind = event.get("kind")
        if kind == "stop-review":
            _merge_stop_review(summary["stop_review"], event)
        elif kind == "scan":
            _merge_scan(summary["scan"], event)

    return summary


def _classify_retry_reason(msg: str) -> str:
    """Turn the free-form retry message into a short categorical label."""
    lower = msg.lower()
    match = re.search(r"http (\d+)", lower)
    if match:
        return f"HTTP {match.group(1)}"
    if "timeout" in lower:
        return "timeout"
    return "other"


def _merge_stop_review(acc: dict[str, Any], event: dict[str, Any]) -> None:
    """Fold one stop-review log entry into the running aggregate.

    Skips foreground ``mode=from_stdin`` entries — those are just the
    parent-hook acknowledgements (always success, no reviewer call). The
    background entry (the second one per session) is what carries the
    actual reviewer result.
    """
    if event.get("mode") == "from_stdin":
        return
    acc["total"] += 1
    if event.get("exit_code") == 0:
        acc["succeeded"] += 1
    else:
        acc["failed"] += 1
        err_type = str(event.get("error_type") or "unknown")
        acc["by_error_type"][err_type] = acc["by_error_type"].get(err_type, 0) + 1

    count = event.get("suggestion_count")
    if isinstance(count, int):
        acc["suggestions_emitted"] += count
    skipped = event.get("skipped_suggestion_count")
    if isinstance(skipped, int):
        acc["skipped"] += skipped
    families = event.get("suggestion_families")
    if isinstance(families, dict):
        for key, value in families.items():
            if key in acc["families"] and isinstance(value, int):
                acc["families"][key] += value


def _merge_scan(acc: dict[str, Any], event: dict[str, Any]) -> None:
    """Fold one scan log entry into the running aggregate. Only scans with
    an ``aggregate`` extra (i.e. they actually processed buckets) contribute
    — skip_empty scans are still counted as total but don't pollute the
    action/scope breakdowns."""
    acc["total"] += 1
    aggregate = event.get("aggregate")
    if not isinstance(aggregate, dict):
        return
    acc["with_processed_buckets"] += 1
    if int(aggregate.get("buckets_with_fallback", 0) or 0) > 0:
        acc["with_fallback"] += 1
    acc["suggestions"] += int(aggregate.get("total_memory_suggestions", 0) or 0)
    acc["discarded"] += int(aggregate.get("total_discarded", 0) or 0)
    actions = aggregate.get("actions") or {}
    for key, value in actions.items():
        if key in acc["memory_actions"] and isinstance(value, int):
            acc["memory_actions"][key] += value
    scopes = aggregate.get("scopes") or {}
    for key, value in scopes.items():
        if key in acc["scopes"] and isinstance(value, int):
            acc["scopes"][key] += value

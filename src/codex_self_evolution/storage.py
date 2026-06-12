from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import Paths


@dataclass(frozen=True)
class StableMemorySelection:
    """Selected stable memory content plus source and fallback metadata."""

    content: str
    source: str
    source_path: Path
    memory_path: Path
    summary_path: Path
    summary_meta_path: Path
    fallback_used: bool
    fallback_reason: str


def ensure_runtime_dirs(paths: Paths) -> None:
    """Create retained runtime directories for a project bucket."""
    for directory in (
        paths.state_dir,
        paths.memory_dir,
        paths.memory_refs_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def utc_now() -> datetime:
    """Return timezone-aware UTC now."""
    return datetime.now(UTC)


def atomic_write_json(path: Path, payload: object) -> None:
    """Atomically write pretty JSON to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temp_name = handle.name
    os.replace(temp_name, path)


def atomic_write_text(path: Path, content: str) -> None:
    """Atomically write UTF-8 text to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temp_name = handle.name
    os.replace(temp_name, path)


def load_json(path: Path) -> object:
    """Load JSON from a UTF-8 file."""
    return json.loads(path.read_text(encoding="utf-8"))


def repo_fingerprint(repo_root: Path) -> str:
    """Compute the stable repo fingerprint used in SessionStart payloads."""
    return hashlib.sha1(str(repo_root.resolve()).encode("utf-8")).hexdigest()


def compute_stable_id(value: str) -> str:
    """Return a compact deterministic id for local filenames."""
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def sha256_text(text: str) -> str:
    """Return the SHA-256 hex digest for UTF-8 text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_text_if_exists(path: Path) -> str:
    """Read UTF-8 text from ``path`` when it exists, otherwise return empty."""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _read_optional_summary_text(path: Path) -> str:
    """Read optional summary text, treating malformed summary paths as empty."""
    try:
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def load_stable_memory(paths: Paths) -> StableMemorySelection:
    """Load the default-injected stable memory with safe summary fallback."""
    memory_path = paths.memory_dir / "MEMORY.md"
    summary_path = paths.memory_dir / "memory_summary.md"
    summary_meta_path = paths.memory_dir / "memory_summary.meta.json"
    memory_text = read_text_if_exists(memory_path)
    summary_text = _read_optional_summary_text(summary_path)

    fallback_reason = _summary_fallback_reason(
        memory_text=memory_text,
        summary_text=summary_text,
        summary_meta_path=summary_meta_path,
    )
    if not fallback_reason:
        return StableMemorySelection(
            content=summary_text,
            source="memory_summary.md",
            source_path=summary_path,
            memory_path=memory_path,
            summary_path=summary_path,
            summary_meta_path=summary_meta_path,
            fallback_used=False,
            fallback_reason="",
        )
    return StableMemorySelection(
        content=memory_text,
        source="MEMORY.md",
        source_path=memory_path,
        memory_path=memory_path,
        summary_path=summary_path,
        summary_meta_path=summary_meta_path,
        fallback_used=True,
        fallback_reason=fallback_reason,
    )


def _load_summary_meta(path: Path) -> dict[str, object] | None:
    """Load memory summary metadata when it is a JSON object."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _summary_fallback_reason(
    *,
    memory_text: str,
    summary_text: str,
    summary_meta_path: Path,
) -> str:
    """Return an empty string only when memory_summary.md is safe to inject."""
    summary_path = summary_meta_path.with_name("memory_summary.md")
    if not summary_text.strip():
        return "summary_missing" if not summary_path.exists() else "summary_empty"
    if not summary_meta_path.exists():
        return "summary_meta_missing"
    meta = _load_summary_meta(summary_meta_path)
    if meta is None:
        return "summary_meta_invalid"
    if meta.get("schema_version") != 1 or meta.get("source") != "MEMORY.md":
        return "summary_meta_invalid"
    if meta.get("source_memory_sha256") != sha256_text(memory_text):
        return "source_hash_mismatch"
    if meta.get("summary_sha256") != sha256_text(summary_text):
        return "summary_hash_mismatch"
    return ""


def _pid_alive(pid: object) -> bool:
    """Return whether a process id appears alive without requiring ownership."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we lack signal permission — treat as alive.
        return True
    except OSError:
        return False
    return True

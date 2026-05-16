from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .config import Paths


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


def read_text_if_exists(path: Path) -> str:
    """Read UTF-8 text from ``path`` when it exists, otherwise return empty."""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def load_stable_memory(paths: Paths) -> str:
    """Load the single default-injected project MEMORY.md file."""
    return read_text_if_exists(paths.memory_dir / "MEMORY.md")


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

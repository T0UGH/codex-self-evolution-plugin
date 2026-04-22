"""Hydrate ``~/.codex-self-evolution/.env.provider`` into ``os.environ``.

Why this exists: the ``scan`` job runs under launchd, which hands the child a
near-empty environment (just ``PATH`` + ``HOME`` per our plist). Downstream
agents — specifically ``opencode`` — read their API keys from env vars
(``opencode.json`` resolves ``{env:MINIMAX_API_KEY}`` at runtime). Without
hydration, opencode in the scan path hits MiniMax 401 and exits 0 with an
``{"type":"error"}`` event that the old extractor silently discarded, giving
users a misleading "no assistant text" receipt while every scan silently
fell back to the script backend.

Security posture:

- We **do not source** the file through a shell — that would be arbitrary
  code execution for anyone who pasted odd content into their provider file.
  Instead we use a restrictive ``KEY=value`` parser that mirrors what
  :mod:`diagnostics` already uses for presence checks.
- We **never overwrite** existing ``os.environ`` values by default so the
  interactive path (where the user explicitly exported a key in their shell)
  keeps its explicit override.
- We log only the *keys* applied, never values.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path


logger = logging.getLogger(__name__)

# Mirror the regex used by diagnostics._check_env_provider so the two
# components stay aligned on what syntax .env.provider files accept.
_KEY_RE = re.compile(r"^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$")


def parse_env_file(path: Path) -> dict[str, str]:
    """Extract ``KEY=value`` pairs from a dotenv-style file.

    Returns a dict of non-empty entries. Missing file, unreadable file, or
    malformed lines are all silently tolerated — the goal is graceful
    degradation, not strict validation. Callers should treat an empty dict
    as "nothing to hydrate" without reading it as an error signal.
    """
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _KEY_RE.match(raw_line)
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        # Strip matching surrounding quotes: `KEY="abc"` → `abc`.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        value = value.strip()
        if value:
            result[key] = value
    return result


def load_env_provider(home: Path | None = None) -> dict[str, str]:
    """Read and parse ``<home>/.env.provider`` (default home resolved lazily)."""
    # Late import to avoid a module-load cycle: config → env_loader → config.
    from .config import get_home_dir

    home_dir = home or get_home_dir()
    return parse_env_file(home_dir / ".env.provider")


def apply_to_environ(env_vars: dict[str, str], overwrite: bool = False) -> list[str]:
    """Copy ``env_vars`` into ``os.environ``. Returns the keys actually applied.

    When ``overwrite`` is False (the default), keys already set in
    ``os.environ`` are left alone — that way interactive users who explicitly
    exported a different key in their shell keep priority over whatever is
    saved in ``.env.provider``.
    """
    applied: list[str] = []
    for key, value in env_vars.items():
        if not overwrite and key in os.environ:
            continue
        os.environ[key] = value
        applied.append(key)
    return applied


def hydrate_env_for_subprocesses() -> list[str]:
    """One-shot: load ``.env.provider`` and apply to ``os.environ``.

    Intended to be called once at the CLI entry point. Any subprocess we
    spawn (opencode, the MiniMax HTTP reviewer) then inherits these keys
    via the default ``os.environ`` copy that :class:`subprocess.Popen`
    performs. Safe to call multiple times.

    Returns the keys it actually applied so callers can emit a structured
    log line naming which env vars crossed into process scope (values are
    never logged).
    """
    try:
        env_vars = load_env_provider()
    except Exception:  # noqa: BLE001 — loader is defensive; log + skip
        logger.debug("env provider hydration failed", exc_info=True)
        return []
    return apply_to_environ(env_vars, overwrite=False)

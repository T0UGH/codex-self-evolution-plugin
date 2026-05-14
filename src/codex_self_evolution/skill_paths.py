from __future__ import annotations

import os
from pathlib import Path

CODEX_SKILLS_DIR_ENV = "CSEP_CODEX_SKILLS_DIR"


def codex_skills_dir(override: str | Path | None = None) -> Path:
    """Return the Codex global skills directory used by reflection outputs."""
    if override:
        return Path(override).expanduser().resolve()
    env = os.environ.get(CODEX_SKILLS_DIR_ENV)
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".codex" / "skills"

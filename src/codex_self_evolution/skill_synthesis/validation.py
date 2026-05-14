from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import SYNTH_SKILL_PREFIX
from ..storage import atomic_write_json
from .inventory import _frontmatter
from .redaction import contains_secret_like_text

RETIRED_DESCRIPTION = "Retired generated skill. Do not use."
RETIRED_BODY_LINE = "Do not use this skill. It is kept only for audit history."


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_synth_skill(skill_path: Path) -> dict[str, Any]:
    if skill_path.name != "SKILL.md":
        return {"path": str(skill_path), "valid": False, "reason": "not_skill_md"}
    if not skill_path.parent.name.startswith(SYNTH_SKILL_PREFIX):
        return {"path": str(skill_path), "valid": False, "reason": "outside_synth_namespace"}
    if not re.fullmatch(r"csep-synth-[a-z0-9-]+", skill_path.parent.name):
        return {"path": str(skill_path), "valid": False, "reason": "invalid_skill_id"}
    if not skill_path.is_file():
        return {"path": str(skill_path), "valid": False, "reason": "missing_skill_md"}
    text = skill_path.read_text(encoding="utf-8")
    meta = _frontmatter(text)
    if not meta.get("name") or not meta.get("description"):
        return {"path": str(skill_path), "valid": False, "reason": "missing_frontmatter"}
    if contains_secret_like_text(text):
        return {"path": str(skill_path), "valid": False, "reason": "secret_like_content"}
    if meta.get("csep_status") == "retired":
        if meta.get("description") != RETIRED_DESCRIPTION:
            return {"path": str(skill_path), "valid": False, "reason": "invalid_retired_description"}
        if RETIRED_BODY_LINE not in text:
            return {"path": str(skill_path), "valid": False, "reason": "invalid_retired_body"}
        return {"path": str(skill_path), "valid": True, "status": "retired", "reason": None}
    description = meta.get("description", "").lower()
    if "use" not in description or "when" not in description:
        return {"path": str(skill_path), "valid": False, "reason": "weak_description"}
    words = [word for word in re.split(r"\s+", text) if word]
    if len(words) < 24:
        return {"path": str(skill_path), "valid": False, "reason": "low_signal"}
    if not any(marker in text.lower() for marker in ("workflow", "steps", "run ", "verify", "check")):
        return {"path": str(skill_path), "valid": False, "reason": "low_signal"}
    return {"path": str(skill_path), "valid": True, "status": "active", "reason": None}


def mark_invalid(skill_path: Path, *, run_id: str, reasons: list[str], evidence_keys: list[str]) -> Path:
    marker = skill_path.parent / "CSEP_INVALID.json"
    atomic_write_json(marker, {
        "schema_version": 1,
        "checked_at": _now(),
        "run_id": run_id,
        "skill_path": str(skill_path),
        "reasons": reasons,
        "evidence_keys": evidence_keys,
    })
    return marker


def clear_invalid_marker(skill_path: Path) -> bool:
    marker = skill_path.parent / "CSEP_INVALID.json"
    if marker.exists():
        marker.unlink()
        return True
    return False

from __future__ import annotations

import json
from pathlib import Path

from ..schemas import SkillManifestEntry


def load_manifest(path: Path) -> list[SkillManifestEntry]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [SkillManifestEntry.from_dict(item) for item in raw.get("skills", [])]


def dump_manifest(entries: list[SkillManifestEntry]) -> dict:
    return {"skills": [entry.to_dict() for entry in sorted(entries, key=lambda item: item.skill_id)]}


def summarize_managed_skills(path: Path) -> list[dict[str, str | bool | None]]:
    summary: list[dict[str, str | bool | None]] = []
    for entry in load_manifest(path):
        summary.append(
            {
                "skill_id": entry.skill_id,
                "title": entry.title,
                "status": entry.status,
                "path": entry.path,
                "owner": entry.owner,
                "managed": entry.managed,
                "updated_at": entry.updated_at,
                "retired_at": entry.retired_at,
            }
        )
    return summary

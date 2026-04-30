from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime

from ..config import PLUGIN_OWNER
from ..schemas import SkillManifestEntry, Suggestion


def _normalize_skill_id(raw: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", raw.lower()).strip("-")


def _timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def compile_skills(suggestions: list[Suggestion], existing_entries: list[SkillManifestEntry] | None = None) -> tuple[list[dict], list[dict]]:
    compiled: dict[str, dict] = {}
    discarded: list[dict] = []
    existing_map = {entry.skill_id: entry for entry in existing_entries or []}
    for item in suggestions:
        if item.family != "skill_action":
            continue
        action = str(item.details.get("action", "")).strip().lower()
        if action not in {"create", "patch", "edit", "retire"}:
            discarded.append({"summary": item.summary, "reason": "unsupported_action"})
            continue
        content = str(item.details.get("content", "")).strip()
        title = str(item.details.get("title", item.summary)).strip()
        description = str(item.details.get("description", "")).strip()
        skill_id = _normalize_skill_id(str(item.details.get("skill_id", title)))
        existing = existing_map.get(skill_id)
        if action in {"patch", "edit", "retire"}:
            if existing is None:
                discarded.append({"skill_id": skill_id, "reason": "missing_managed_skill"})
                continue
            if not existing.managed or existing.owner != PLUGIN_OWNER:
                discarded.append({"skill_id": skill_id, "reason": "ownership_violation"})
                continue
        if action in {"create", "patch", "edit"} and not description:
            discarded.append({"skill_id": skill_id, "reason": "missing_description"})
            continue
        if action in {"create", "patch", "edit"} and len(content.split()) < 3:
            discarded.append({"skill_id": skill_id, "reason": "low_signal"})
            continue
        compiled[skill_id] = {
            "skill_id": skill_id,
            "title": title,
            "description": description,
            "content": content,
            "action": action,
        }
    return list(compiled.values()), discarded


def build_manifest_entries(compiled_skills: list[dict], skills_dir: str, existing_entries: list[SkillManifestEntry] | None = None) -> list[SkillManifestEntry]:
    existing_map = {entry.skill_id: entry for entry in existing_entries or []}
    updated = dict(existing_map)
    timestamp = _timestamp()
    for item in compiled_skills:
        current = existing_map.get(item["skill_id"])
        retired_at = timestamp if item["action"] == "retire" else None
        entry = SkillManifestEntry(
            skill_id=item["skill_id"],
            action=item["action"],
            title=item["title"],
            path=f"{skills_dir}/managed/{item['skill_id']}.md",
            status="retired" if item["action"] == "retire" else "active",
            owner=PLUGIN_OWNER,
            managed=True,
            created_by=current.created_by if current else PLUGIN_OWNER,
            updated_at=timestamp,
            retired_at=retired_at,
        )
        updated[item["skill_id"]] = entry if current is None else replace(entry, created_by=current.created_by)
    return list(updated.values())

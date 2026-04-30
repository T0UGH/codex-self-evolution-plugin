from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

from ..config import PLUGIN_OWNER
from ..schemas import SkillManifestEntry
from ..storage import atomic_write_text
from .validation import validate_publishable_skill

CODEX_SKILLS_DIR_ENV = "CSEP_CODEX_SKILLS_DIR"
GLOBAL_NAMESPACE = "csep"
LEGACY_NAMESPACE = "csep-managed"
GLOBAL_PREFIX = "csep-"


def global_skill_id(skill_id: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", skill_id.lower()).strip("-")
    if normalized.startswith(GLOBAL_PREFIX):
        return normalized
    return f"{GLOBAL_PREFIX}{normalized}"


def codex_skills_dir(override: str | Path | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    env = os.environ.get(CODEX_SKILLS_DIR_ENV)
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".codex" / "skills"


def _yaml_scalar(value: str) -> str:
    stripped = value.strip()
    escaped = stripped.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_skill(title: str, description: str, source_skill_id: str, content: str) -> str:
    return (
        "---\n"
        f"name: {_yaml_scalar(title)}\n"
        f"description: {_yaml_scalar(description)}\n"
        "---\n\n"
        f"# {title.strip()}\n\n"
        "<!-- managed-by: codex-self-evolution-plugin; "
        f"source-skill-id: {source_skill_id}; do not edit by hand -->\n\n"
        f"{content.strip()}\n"
    )


def _safe_generated_dir(skills_root: Path, source_skill_id: str) -> Path:
    global_id = global_skill_id(source_skill_id)
    if not global_id.startswith(GLOBAL_PREFIX):
        raise ValueError(f"refusing to publish unprefixed managed skill: {source_skill_id}")
    return skills_root / global_id


def _legacy_generated_dir(skills_root: Path, source_skill_id: str) -> Path:
    return skills_root / LEGACY_NAMESPACE / global_skill_id(source_skill_id)


def _remove_projection(target_dir: Path) -> bool:
    if target_dir.is_symlink() or target_dir.is_file():
        target_dir.unlink()
        return True
    if target_dir.exists():
        shutil.rmtree(target_dir)
        return True
    return False


def publish_global_skills(
    compiled_skills: list[dict[str, Any]],
    entries: list[SkillManifestEntry],
    *,
    skills_root: str | Path | None = None,
) -> dict[str, Any]:
    """Publish active plugin-owned managed skills into Codex's global skill tree.

    The source of truth remains the plugin state directory. The global copy is
    a runtime projection under ``~/.codex/skills/csep-*`` so generated skills
    load as normal Codex skills while staying namespaced.
    """
    root = codex_skills_dir(skills_root)
    entry_map = {entry.skill_id: entry for entry in entries}
    published: list[str] = []
    unpublished: list[str] = []
    skipped: list[dict[str, str]] = []

    for item in compiled_skills:
        source_skill_id = str(item.get("skill_id") or "").strip()
        action = str(item.get("action") or "").strip().lower()
        entry = entry_map.get(source_skill_id)
        if not source_skill_id or entry is None:
            skipped.append({"skill_id": source_skill_id, "reason": "missing_manifest_entry"})
            continue
        if not entry.managed or entry.owner != PLUGIN_OWNER:
            skipped.append({"skill_id": source_skill_id, "reason": "ownership_violation"})
            continue

        target_dir = _safe_generated_dir(root, source_skill_id)
        legacy_target_dir = _legacy_generated_dir(root, source_skill_id)
        if action == "retire" or entry.status == "retired":
            for retired_dir in (target_dir, legacy_target_dir):
                if _remove_projection(retired_dir):
                    unpublished.append(str(retired_dir))
            continue

        if entry.status != "active":
            skipped.append({"skill_id": source_skill_id, "reason": f"status:{entry.status}"})
            continue

        valid, reason = validate_publishable_skill(item)
        if not valid:
            for invalid_dir in (target_dir, legacy_target_dir):
                if _remove_projection(invalid_dir):
                    unpublished.append(str(invalid_dir))
            skipped.append({"skill_id": source_skill_id, "reason": reason or "invalid"})
            continue

        if _remove_projection(legacy_target_dir):
            unpublished.append(str(legacy_target_dir))

        rendered = _render_skill(
            str(item.get("title") or "").strip(),
            str(item.get("description") or "").strip(),
            source_skill_id,
            str(item.get("content") or "").strip(),
        )
        target_path = target_dir / "SKILL.md"
        atomic_write_text(target_path, rendered)
        published.append(str(target_path))

    return {
        "namespace": GLOBAL_NAMESPACE,
        "skills_root": str(root),
        "published": published,
        "unpublished": unpublished,
        "skipped": skipped,
    }

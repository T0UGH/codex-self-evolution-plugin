from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..config import SYNTH_SKILL_PREFIX
from ..storage import atomic_write_json


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    out: dict[str, str] = {}
    for raw in text[4:end].splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _hash_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _skill_doc(path: Path) -> Path:
    return path / "SKILL.md"


def read_skill_metadata(skill_dir: Path) -> dict[str, Any] | None:
    skill_path = _skill_doc(skill_dir)
    if not skill_path.is_file():
        return None
    try:
        text = skill_path.read_text(encoding="utf-8")
        stat = skill_path.stat()
    except OSError:
        return None
    meta = _frontmatter(text)
    return {
        "skill_id": skill_dir.name,
        "path": str(skill_path),
        "name": meta.get("name", ""),
        "description": meta.get("description", ""),
        "csep_status": meta.get("csep_status", ""),
        "mtime": stat.st_mtime,
        "content_hash": _hash_text(text),
    }


def read_skills_inventory(skills_root: Path) -> dict[str, list[dict[str, Any]]]:
    synth: list[dict[str, Any]] = []
    non_synth: list[dict[str, Any]] = []
    if not skills_root.is_dir():
        return {"synth": synth, "non_synth": non_synth}
    for child in sorted(skills_root.iterdir()):
        if not child.is_dir():
            continue
        meta = read_skill_metadata(child)
        if meta is None:
            continue
        if child.name.startswith(SYNTH_SKILL_PREFIX):
            marker = child / "CSEP_INVALID.json"
            meta["invalid_marker"] = str(marker) if marker.exists() else ""
            synth.append(meta)
        else:
            non_synth.append({
                "skill_id": meta["skill_id"],
                "path": meta["path"],
                "name": meta["name"],
                "description": meta["description"],
                "mtime": meta["mtime"],
            })
    return {"synth": synth, "non_synth": non_synth}


def snapshot_synth_skills(skills_root: Path) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    if not skills_root.is_dir():
        return snapshot
    for child in sorted(skills_root.iterdir()):
        if not child.is_dir() or not child.name.startswith(SYNTH_SKILL_PREFIX):
            continue
        meta = read_skill_metadata(child)
        if meta is not None:
            snapshot[str(child / "SKILL.md")] = meta
    return snapshot


def changed_paths(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> list[str]:
    paths = set(before) | set(after)
    return sorted(
        path for path in paths
        if before.get(path, {}).get("content_hash") != after.get(path, {}).get("content_hash")
    )


def write_published_index(path: Path, skills_root: Path, inventory: dict[str, list[dict[str, Any]]]) -> None:
    atomic_write_json(path, {
        "schema_version": 1,
        "skills_root": str(skills_root),
        "skills": inventory.get("synth", []),
    })

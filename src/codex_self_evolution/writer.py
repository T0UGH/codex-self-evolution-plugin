from __future__ import annotations

from pathlib import Path

from .config import PLUGIN_OWNER
from .managed_skills.manifest import dump_manifest, load_manifest
from .schemas import CompilerReceipt, RecallRecord, SkillManifestEntry
from .storage import atomic_write_json, atomic_write_text


def _render_memory_markdown(title: str, records: list[dict]) -> str:
    lines = [f"# {title}", ""]
    if not records:
        lines.extend(["_No entries yet._", ""])
        return "\n".join(lines)
    for item in records:
        lines.extend([f"## {item['summary']}", "", item["content"], ""])
    return "\n".join(lines).rstrip() + "\n"


def write_memory(memory_dir: Path, records_by_scope: dict[str, list[dict]]) -> tuple[Path, Path, Path]:
    user_path = memory_dir / "USER.md"
    global_path = memory_dir / "MEMORY.md"
    index_path = memory_dir / "memory.json"
    user_records = records_by_scope.get("user", [])
    global_records = records_by_scope.get("global", [])
    atomic_write_text(user_path, _render_memory_markdown("USER", user_records))
    atomic_write_text(global_path, _render_memory_markdown("MEMORY", global_records))
    atomic_write_json(index_path, {"user": user_records, "global": global_records})
    return user_path, global_path, index_path


def write_recall(recall_dir: Path, records: list[RecallRecord]) -> tuple[Path, Path]:
    index_path = recall_dir / "index.json"
    markdown_path = recall_dir / "compiled.md"
    atomic_write_json(index_path, {"records": [item.to_dict() for item in records]})
    lines = ["# Compiled Recall", ""]
    for item in records:
        lines.extend([f"## {item.summary}", "", item.content, "", f"Provenance: {', '.join(item.source_paths)}", ""])
    atomic_write_text(markdown_path, "\n".join(lines).rstrip() + "\n")
    return index_path, markdown_path


def write_skills(
    skills_dir: Path,
    compiled_skills: list[dict],
    entries: list[SkillManifestEntry],
    existing_entries: list[SkillManifestEntry] | None = None,
) -> tuple[list[Path], Path]:
    managed_dir = skills_dir / "managed"
    existing_map = {entry.skill_id: entry for entry in (existing_entries or load_manifest(skills_dir / "manifest.json"))}
    written: list[Path] = []
    for item in compiled_skills:
        skill_id = item["skill_id"]
        existing = existing_map.get(skill_id)
        if item["action"] in {"patch", "edit", "retire"}:
            if existing is None or not existing.managed or existing.owner != PLUGIN_OWNER:
                raise ValueError(f"cannot modify unmanaged skill: {skill_id}")
        skill_path = managed_dir / f"{skill_id}.md"
        if item["action"] == "retire":
            content = f"# {item['title']}\n\nStatus: retired\n"
        else:
            content = f"# {item['title']}\n\n{item['content'].strip()}\n"
        atomic_write_text(skill_path, content)
        written.append(skill_path)
    manifest_path = skills_dir / "manifest.json"
    atomic_write_json(manifest_path, dump_manifest(entries))
    return written, manifest_path


def write_receipt(compiler_dir: Path, receipt: CompilerReceipt) -> Path:
    destination = compiler_dir / "last_receipt.json"
    atomic_write_json(destination, receipt.to_dict())
    return destination

from __future__ import annotations

from pathlib import Path

from ..config import DEFAULT_BATCH_SIZE, DEFAULT_LOCK_STALE_SECONDS, build_paths
from ..config import PLUGIN_OWNER
from ..managed_skills.manifest import dump_manifest, load_manifest
from ..schemas import CompilerReceipt, SuggestionEnvelope
from ..storage import (
    CompileLockError,
    atomic_write_json,
    atomic_write_text,
    claim_suggestions,
    file_lock,
    finalize_suggestion,
    has_pending_work,
    list_suggestions,
    load_json,
    lock_status,
)
from .backends import build_compile_context, get_backend


def _render_memory_markdown(title: str, records: list[dict]) -> str:
    lines = [f"# {title}", ""]
    if not records:
        lines.extend(["_No entries yet._", ""])
        return "\n".join(lines)
    for item in records:
        lines.extend([f"## {item['summary']}", "", item["content"], ""])
    return "\n".join(lines).rstrip() + "\n"


def _write_memory(memory_dir: Path, records_by_scope: dict[str, list[dict]]) -> tuple[Path, Path, Path]:
    user_path = memory_dir / "USER.md"
    global_path = memory_dir / "MEMORY.md"
    index_path = memory_dir / "memory.json"
    user_records = records_by_scope.get("user", [])
    global_records = records_by_scope.get("global", [])
    atomic_write_text(user_path, _render_memory_markdown("USER", user_records))
    atomic_write_text(global_path, _render_memory_markdown("MEMORY", global_records))
    atomic_write_json(index_path, {"user": user_records, "global": global_records})
    return user_path, global_path, index_path


def _write_recall(recall_dir: Path, records: list) -> tuple[Path, Path]:
    index_path = recall_dir / "index.json"
    markdown_path = recall_dir / "compiled.md"
    atomic_write_json(index_path, {"records": [item.to_dict() for item in records]})
    lines = ["# Compiled Recall", ""]
    for item in records:
        lines.extend([f"## {item.summary}", "", item.content, "", f"Provenance: {', '.join(item.source_paths)}", ""])
    atomic_write_text(markdown_path, "\n".join(lines).rstrip() + "\n")
    return index_path, markdown_path


def _write_skills(
    skills_dir: Path,
    compiled_skills: list[dict],
    entries: list,
    existing_entries: list | None = None,
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


def apply_compiler_outputs(
    memory_dir: Path,
    recall_dir: Path,
    skills_dir: Path,
    memory_records: dict[str, list[dict]],
    recall_records: list,
    compiled_skills: list[dict],
    manifest_entries: list,
    existing_entries: list | None = None,
) -> dict[str, tuple | list]:
    memory_paths = _write_memory(memory_dir, memory_records)
    recall_paths = _write_recall(recall_dir, recall_records)
    skill_paths = _write_skills(skills_dir, compiled_skills, manifest_entries, existing_entries=existing_entries)
    return {
        "memory": memory_paths,
        "recall": recall_paths,
        "skills": skill_paths,
    }


def preflight_compile(
    repo_root: str | Path | None = None,
    state_dir: str | Path | None = None,
    stale_after_seconds: int = DEFAULT_LOCK_STALE_SECONDS,
) -> dict:
    paths = build_paths(repo_root=repo_root, state_dir=state_dir)
    status = lock_status(paths, stale_after_seconds=stale_after_seconds)
    if status["locked"] and not status["stale"]:
        return {"status": "skip_locked", "lock": status}
    if not has_pending_work(paths):
        return {"status": "skip_empty", "pending": 0, "retryable_failed": 0}
    return {"status": "run", "lock": status, "pending": len(list_suggestions(paths, "pending"))}


def run_compile(
    repo_root: str | Path | None = None,
    state_dir: str | Path | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    backend: str = "script",
    allow_fallback: bool = True,
) -> dict:
    paths = build_paths(repo_root=repo_root, state_dir=state_dir)
    preflight = preflight_compile(repo_root=repo_root, state_dir=state_dir)
    if preflight["status"] != "run":
        receipt = CompilerReceipt(
            run_status=preflight["status"],
            backend=backend,
            processed_count=0,
            archived_count=0,
            memory_records=0,
            recall_records=0,
            managed_skills=0,
            skip_reason=preflight["status"],
        )
        receipt_path = write_receipt(paths.compiler_dir, receipt)
        return {"status": preflight["status"], "processed_count": 0, "receipt_path": str(receipt_path)}
    try:
        with file_lock(paths):
            claimed = claim_suggestions(paths, batch_size=batch_size)
            if not claimed:
                receipt = CompilerReceipt(
                    run_status="skip_empty",
                    backend=backend,
                    processed_count=0,
                    archived_count=0,
                    memory_records=0,
                    recall_records=0,
                    managed_skills=0,
                    skip_reason="skip_empty",
                )
                receipt_path = write_receipt(paths.compiler_dir, receipt)
                return {"status": "skip_empty", "processed_count": 0, "receipt_path": str(receipt_path)}
            envelopes = [SuggestionEnvelope.from_dict(load_json(path)) for path, _ in claimed]
            backend_impl = get_backend(backend)
            context = build_compile_context(paths, envelopes)
            artifacts = backend_impl.compile(envelopes, context, {"allow_fallback": allow_fallback})
            apply_compiler_outputs(
                memory_dir=paths.memory_dir,
                recall_dir=paths.recall_dir,
                skills_dir=paths.skills_dir,
                memory_records=artifacts.memory_records,
                recall_records=artifacts.recall_records,
                compiled_skills=artifacts.compiled_skills,
                manifest_entries=artifacts.manifest_entries,
                existing_entries=context["existing_manifest"],
            )
            item_receipts = []
            for path, envelope in claimed:
                destination = finalize_suggestion(paths, path, envelope, "done")
                item_receipts.append({"suggestion_id": envelope.suggestion_id, "state": "done", "path": str(destination)})
            for discarded in artifacts.discarded_items:
                item_receipts.append({"state": "discarded", **discarded})
            receipt = CompilerReceipt(
                run_status="success",
                backend=artifacts.backend_name,
                processed_count=len(claimed),
                archived_count=len(claimed),
                memory_records=sum(len(items) for items in artifacts.memory_records.values()),
                recall_records=len(artifacts.recall_records),
                managed_skills=len(artifacts.compiled_skills),
                item_receipts=item_receipts,
                fallback_backend=artifacts.fallback_backend,
            )
            receipt_path = write_receipt(paths.compiler_dir, receipt)
            return {"status": "success", "processed_count": len(claimed), "receipt_path": str(receipt_path), "backend": artifacts.backend_name}
    except CompileLockError:
        receipt = CompilerReceipt(
            run_status="skip_locked",
            backend=backend,
            processed_count=0,
            archived_count=0,
            memory_records=0,
            recall_records=0,
            managed_skills=0,
            skip_reason="skip_locked",
        )
        receipt_path = write_receipt(paths.compiler_dir, receipt)
        return {"status": "skip_locked", "processed_count": 0, "receipt_path": str(receipt_path)}

from __future__ import annotations

from pathlib import Path

from ..config import DEFAULT_BATCH_SIZE, DEFAULT_LOCK_STALE_SECONDS, build_paths
from ..schemas import CompilerReceipt, SuggestionEnvelope
from ..storage import (
    CompileLockError,
    claim_suggestions,
    file_lock,
    finalize_suggestion,
    has_pending_work,
    list_suggestions,
    load_json,
    lock_status,
)
from ..writer import write_memory, write_recall, write_receipt, write_skills
from .backends import build_compile_context, get_backend


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
            write_memory(paths.memory_dir, artifacts.memory_records)
            write_recall(paths.recall_dir, artifacts.recall_records)
            write_skills(paths.skills_dir, artifacts.compiled_skills, artifacts.manifest_entries, existing_entries=context["existing_manifest"])
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

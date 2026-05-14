from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_LOCK_STALE_SECONDS,
    DEFAULT_SCAN_MAX_RUNS_PER_PROJECT,
    PLUGIN_OWNER,
    PROJECTS_SUBDIR,
    build_paths,
    get_home_dir,
    is_archived_bucket,
)
from ..managed_skills.manifest import dump_manifest, load_manifest
from ..managed_skills.publish import publish_global_skills
from ..schemas import CompilerReceipt, SuggestionEnvelope
from ..storage import (
    CompileLockError,
    atomic_write_json,
    atomic_write_text,
    claim_suggestions,
    file_lock,
    finalize_suggestion,
    has_pending_work,
    list_stale_processing,
    list_suggestions,
    load_json,
    lock_status,
)
from .backends import build_compile_context, get_backend


def _tally_memory_actions(envelopes: list[SuggestionEnvelope]) -> dict[str, Any]:
    """Count reviewer-requested memory actions across a compile batch.

    Returns a dict with action totals and scope distribution. Populated into
    :class:`CompilerReceipt.memory_action_stats` so observability can tell
    whether the reviewer is actually using the ``replace`` / ``remove`` /
    scope-routing capabilities introduced in Phase 1 — without this, the
    receipts only report aggregate ``memory_records`` which conflates "new
    entries added" with "existing entries passed through untouched".

    Empty dict when the batch has no memory_updates; keeps legacy receipts
    that parse old schema happy.
    """
    actions: dict[str, int] = {"add": 0, "replace": 0, "remove": 0}
    by_scope: dict[str, int] = {"user": 0, "global": 0}
    total = 0
    for envelope in envelopes:
        for suggestion in envelope.suggestions:
            if suggestion.family != "memory_updates":
                continue
            total += 1
            action = str(suggestion.details.get("action") or "add").strip().lower()
            if action in actions:
                actions[action] += 1
            scope = str(suggestion.details.get("scope") or "global").strip().lower()
            if scope in by_scope:
                by_scope[scope] += 1
    if total == 0:
        return {}
    return {
        "total": total,
        "by_action": actions,
        "by_scope": by_scope,
    }


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
    publish_global: bool = False,
) -> tuple[list[Path], Path, dict[str, Any]]:
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
    global_publish = publish_global_skills(compiled_skills, entries) if publish_global else {
        "namespace": "csep-managed",
        "skills_root": "",
        "published": [],
        "unpublished": [],
        "skipped": [],
    }
    return written, manifest_path, global_publish


def write_receipt(compiler_dir: Path, receipt: CompilerReceipt) -> Path:
    destination = compiler_dir / "last_receipt.json"
    atomic_write_json(destination, receipt.to_dict())
    return destination


def _finalize_noop_envelopes(paths, claimed: list[tuple[Path, SuggestionEnvelope]]) -> list[dict[str, Any]]:
    item_receipts: list[dict[str, Any]] = []
    for path, envelope in claimed:
        destination = finalize_suggestion(paths, path, envelope, "done", reason="no_suggestions")
        item_receipts.append({
            "suggestion_id": envelope.suggestion_id,
            "state": "done",
            "path": str(destination),
            "reason": "no_suggestions",
        })
    return item_receipts


def apply_compiler_outputs(
    memory_dir: Path,
    recall_dir: Path,
    skills_dir: Path,
    memory_records: dict[str, list[dict]],
    recall_records: list,
    compiled_skills: list[dict],
    manifest_entries: list,
    existing_entries: list | None = None,
    publish_global_skills_enabled: bool = False,
) -> dict[str, tuple | list]:
    memory_paths = _write_memory(memory_dir, memory_records)
    recall_paths = _write_recall(recall_dir, recall_records)
    skill_paths = _write_skills(
        skills_dir,
        compiled_skills,
        manifest_entries,
        existing_entries=existing_entries,
        publish_global=publish_global_skills_enabled,
    )
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
    stale_processing = len(list_stale_processing(paths, stale_after_seconds=stale_after_seconds))
    if not has_pending_work(paths, stale_after_seconds=stale_after_seconds):
        return {"status": "skip_empty", "pending": 0, "retryable_failed": 0}
    return {
        "status": "run",
        "lock": status,
        "pending": len(list_suggestions(paths, "pending")),
        "stale_processing": stale_processing,
    }


def run_compile(
    repo_root: str | Path | None = None,
    state_dir: str | Path | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    backend: str = "script",
    allow_fallback: bool = True,
    compile_options: dict[str, Any] | None = None,
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
            claimed = claim_suggestions(
                paths,
                batch_size=_effective_batch_size(backend, batch_size, compile_options),
            )
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
            claimed_envelopes = [(path, SuggestionEnvelope.from_dict(load_json(path))) for path, _ in claimed]
            noop_claimed = [(path, envelope) for path, envelope in claimed_envelopes if not envelope.suggestions]
            active_claimed = [(path, envelope) for path, envelope in claimed_envelopes if envelope.suggestions]
            noop_item_receipts = _finalize_noop_envelopes(paths, noop_claimed)
            if not active_claimed:
                receipt = CompilerReceipt(
                    run_status="success",
                    backend=backend,
                    processed_count=len(noop_claimed),
                    archived_count=len(noop_claimed),
                    memory_records=0,
                    recall_records=0,
                    managed_skills=0,
                    item_receipts=noop_item_receipts,
                    skip_reason="no_suggestions",
                )
                receipt_path = write_receipt(paths.compiler_dir, receipt)
                return {
                    "status": "success",
                    "processed_count": len(noop_claimed),
                    "receipt_path": str(receipt_path),
                    "backend": backend,
                    "fallback_backend": None,
                    "memory_action_stats": {},
                    "discarded_count": 0,
                }
            envelopes = [envelope for _, envelope in active_claimed]
            memory_action_stats = _tally_memory_actions(envelopes)
            backend_impl = get_backend(backend)
            context = build_compile_context(paths, envelopes)
            options = {"allow_fallback": allow_fallback, **(compile_options or {})}
            try:
                artifacts = backend_impl.compile(envelopes, context, options)
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
                compiler_observability = getattr(exc, "compiler_observability", {}) or {}
                if len(active_claimed) > 1:
                    return _compile_claimed_individually(
                        paths=paths,
                        claimed=active_claimed,
                        backend_impl=backend_impl,
                        backend=backend,
                        allow_fallback=allow_fallback,
                        compile_options=compile_options,
                        memory_action_stats=memory_action_stats,
                        batch_failure_reason=reason,
                        initial_item_receipts=noop_item_receipts,
                        initial_processed_count=len(noop_claimed),
                        initial_compiler_observability=compiler_observability,
                    )
                item_receipts = []
                item_receipts.extend(noop_item_receipts)
                for path, envelope in active_claimed:
                    destination = finalize_suggestion(paths, path, envelope, "failed", reason=reason)
                    item_receipts.append({
                        "suggestion_id": envelope.suggestion_id,
                        "state": "failed",
                        "path": str(destination),
                        "reason": reason,
                    })
                receipt = CompilerReceipt(
                    run_status="error",
                    backend=backend,
                    processed_count=len(noop_claimed),
                    archived_count=len(noop_claimed),
                    memory_records=0,
                    recall_records=0,
                    managed_skills=0,
                    item_receipts=item_receipts,
                    skip_reason=reason,
                    memory_action_stats=memory_action_stats,
                    compiler_observability=compiler_observability,
                )
                receipt_path = write_receipt(paths.compiler_dir, receipt)
                return {
                    "status": "error",
                    "processed_count": len(noop_claimed),
                    "receipt_path": str(receipt_path),
                    "backend": backend,
                    "error": reason,
                    "memory_action_stats": memory_action_stats,
                    "discarded_count": 0,
                    "compiler_observability": compiler_observability,
                }
            output_paths = apply_compiler_outputs(
                memory_dir=paths.memory_dir,
                recall_dir=paths.recall_dir,
                skills_dir=paths.skills_dir,
                memory_records=artifacts.memory_records,
                recall_records=artifacts.recall_records,
                compiled_skills=artifacts.compiled_skills,
                manifest_entries=artifacts.manifest_entries,
                existing_entries=context["existing_manifest"],
                publish_global_skills_enabled=False,
            )
            item_receipts = []
            item_receipts.extend(noop_item_receipts)
            for path, envelope in active_claimed:
                destination = finalize_suggestion(paths, path, envelope, "done")
                item_receipts.append({"suggestion_id": envelope.suggestion_id, "state": "done", "path": str(destination)})
            for discarded in artifacts.discarded_items:
                item_receipts.append({"state": "discarded", **discarded})
            receipt = CompilerReceipt(
                run_status="success",
                backend=artifacts.backend_name,
                processed_count=len(claimed_envelopes),
                archived_count=len(claimed_envelopes),
                memory_records=sum(len(items) for items in artifacts.memory_records.values()),
                recall_records=len(artifacts.recall_records),
                managed_skills=len(artifacts.compiled_skills),
                item_receipts=item_receipts,
                fallback_backend=artifacts.fallback_backend,
                memory_action_stats=memory_action_stats,
                compiler_observability=artifacts.compiler_observability,
            )
            receipt_path = write_receipt(paths.compiler_dir, receipt)
            return {
                "status": "success",
                "processed_count": len(claimed_envelopes),
                "receipt_path": str(receipt_path),
                "backend": artifacts.backend_name,
                "fallback_backend": artifacts.fallback_backend,
                "memory_action_stats": memory_action_stats,
                "discarded_count": len(artifacts.discarded_items),
                "global_skill_publish": output_paths["skills"][2],
                "compiler_observability": artifacts.compiler_observability,
            }
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


def _effective_batch_size(backend: str, batch_size: int, compile_options: dict[str, Any] | None) -> int:
    pi_mode = str((compile_options or {}).get("pi_mode") or "edit").strip().lower()
    if backend == "agent:pi" and pi_mode == "edit":
        return 1
    return batch_size


def _compile_claimed_individually(
    *,
    paths,
    claimed: list[tuple[Path, SuggestionEnvelope]],
    backend_impl,
    backend: str,
    allow_fallback: bool,
    compile_options: dict[str, Any] | None,
    memory_action_stats: dict[str, Any],
    batch_failure_reason: str,
    initial_item_receipts: list[dict[str, Any]] | None = None,
    initial_processed_count: int = 0,
    initial_compiler_observability: dict[str, Any] | None = None,
) -> dict:
    """Retry a failed batch as single-envelope compiles.

    Agent compilers are sensitive to large heterogeneous batches and external
    file-preview truncation. If the whole batch fails, salvage useful items one
    by one instead of marking every claimed envelope failed.
    """
    item_receipts: list[dict[str, Any]] = list(initial_item_receipts or [])
    item_receipts.append(
        {
            "state": "batch_split",
            "reason": batch_failure_reason,
            "suggestion_count": sum(len(envelope.suggestions) for _, envelope in claimed),
        }
    )
    processed_count = initial_processed_count
    failed_count = 0
    memory_records = 0
    recall_records = 0
    managed_skills = 0
    last_skill_publish = None
    compiler_observability: dict[str, Any] = dict(initial_compiler_observability or {})

    for path, envelope in claimed:
        context = build_compile_context(paths, [envelope])
        options = {"allow_fallback": allow_fallback, **(compile_options or {})}
        try:
            artifacts = backend_impl.compile([envelope], context, options)
        except Exception as exc:  # noqa: BLE001 - split retry is best-effort
            reason = f"{type(exc).__name__}: {exc}"
            compiler_observability = getattr(exc, "compiler_observability", {}) or compiler_observability
            destination = finalize_suggestion(paths, path, envelope, "failed", reason=reason)
            item_receipts.append({
                "suggestion_id": envelope.suggestion_id,
                "state": "failed",
                "path": str(destination),
                "reason": reason,
                "split_from_batch": True,
            })
            failed_count += 1
            continue

        output_paths = apply_compiler_outputs(
            memory_dir=paths.memory_dir,
            recall_dir=paths.recall_dir,
            skills_dir=paths.skills_dir,
            memory_records=artifacts.memory_records,
            recall_records=artifacts.recall_records,
            compiled_skills=artifacts.compiled_skills,
            manifest_entries=artifacts.manifest_entries,
            existing_entries=context["existing_manifest"],
            publish_global_skills_enabled=False,
        )
        last_skill_publish = output_paths["skills"][2]
        compiler_observability = artifacts.compiler_observability or compiler_observability
        destination = finalize_suggestion(paths, path, envelope, "done")
        item_receipts.append({
            "suggestion_id": envelope.suggestion_id,
            "state": "done",
            "path": str(destination),
            "split_from_batch": True,
        })
        for discarded in artifacts.discarded_items:
            item_receipts.append({"state": "discarded", "split_from_batch": True, **discarded})
        processed_count += 1
        memory_records += sum(len(items) for items in artifacts.memory_records.values())
        recall_records += len(artifacts.recall_records)
        managed_skills += len(artifacts.compiled_skills)

    run_status = "success" if failed_count == 0 else "error"
    skip_reason = None if failed_count == 0 else f"split_batch_failures={failed_count}; {batch_failure_reason}"
    receipt = CompilerReceipt(
        run_status=run_status,
        backend=backend,
        processed_count=processed_count,
        archived_count=processed_count,
        memory_records=memory_records,
        recall_records=recall_records,
        managed_skills=managed_skills,
        item_receipts=item_receipts,
        skip_reason=skip_reason,
        memory_action_stats=memory_action_stats,
        compiler_observability=compiler_observability,
    )
    receipt_path = write_receipt(paths.compiler_dir, receipt)
    return {
        "status": run_status,
        "processed_count": processed_count,
        "receipt_path": str(receipt_path),
        "backend": backend,
        "error": skip_reason,
        "memory_action_stats": memory_action_stats,
        "discarded_count": sum(1 for item in item_receipts if item.get("state") == "discarded"),
        "global_skill_publish": last_skill_publish,
        "compiler_observability": compiler_observability,
    }


def scan_all_projects(
    home: str | Path | None = None,
    backend: str = "agent:pi",
    batch_size: int = DEFAULT_BATCH_SIZE,
    allow_fallback: bool = True,
    stale_after_seconds: int = DEFAULT_LOCK_STALE_SECONDS,
    compile_options: dict[str, Any] | None = None,
    max_runs_per_project: int = DEFAULT_SCAN_MAX_RUNS_PER_PROJECT,
) -> dict:
    """Run preflight + compile on every per-project bucket under ``<home>/projects/``.

    Intended for the launchd scheduler: a single invocation drains every repo
    that has accumulated pending suggestions, without the user having to
    enumerate them or stand up one plist per repo (which would become
    unmaintainable as they add repos).

    Per-bucket exceptions are **isolated**: a corrupt / mid-migration / locked
    bucket surfaces as an ``error`` entry in the result list but does not stop
    the scan from processing the others. This matters because the scan runs
    unattended — a single bad bucket should not wedge the whole pipeline.

    The default ``backend="agent:pi"`` matches the production scheduler path.
    Callers that want the deterministic script path (tests / CI) pass
    ``backend="script"`` explicitly.

    Returns a summary:

    .. code-block:: json

        {
          "home": "/home/alice/.codex-self-evolution",
          "total_projects": 3,
          "results": [
            {"project": "-home-alice-repo1",
             "state_dir": "...",
             "preflight_status": "run",
             "compile_status": "success",
             "processed_count": 5,
             "backend": "agent:pi",
             "receipt_path": "...",
             "error": null},
            ...
          ],
          "counts": {"run": 1, "skipped": 1, "failed": 1}
        }

    Nothing is written outside the per-bucket state dirs — scan itself has no
    output store, just the aggregated return value (which the CLI prints to
    stdout for the scheduler to log).
    """
    home_dir = Path(home).expanduser().resolve() if home else get_home_dir()
    projects_dir = home_dir / PROJECTS_SUBDIR
    counts = {"run": 0, "skipped": 0, "failed": 0}
    results: list[dict] = []
    max_runs = max(1, int(max_runs_per_project))

    if not projects_dir.is_dir():
        # First ever launchd run on a fresh install: nothing to do, surface
        # that honestly instead of 500-ing.
        return {
            "home": str(home_dir),
            "total_projects": 0,
            "results": results,
            "counts": counts,
        }

    for bucket_path in sorted(projects_dir.iterdir()):
        if not bucket_path.is_dir():
            continue
        # Archived buckets are the tombstones left by `migrate-worktrees` after
        # a worktree consolidation. They carry historical receipts but no live
        # work — processing them would re-run compile against stale snapshots
        # and dirty the receipts, so we explicitly skip them.
        if is_archived_bucket(bucket_path.name):
            continue
        entry: dict = {
            "project": bucket_path.name,
            "state_dir": str(bucket_path),
            "preflight_status": None,
            "terminal_preflight_status": None,
            "compile_status": "not_run",
            "processed_count": 0,
            "backend": None,
            "receipt_path": None,
            "error": None,
            "runs": [],
            "max_runs_per_project": max_runs,
            "drain_limit_reached": False,
        }
        try:
            preflight = preflight_compile(
                state_dir=bucket_path,
                stale_after_seconds=stale_after_seconds,
            )
            entry["preflight_status"] = preflight["status"]
            if preflight["status"] != "run":
                entry["compile_status"] = preflight["status"]
                entry["terminal_preflight_status"] = preflight["status"]
                counts["skipped"] += 1
            else:
                successful_runs = 0
                while successful_runs < max_runs:
                    compile_result = run_compile(
                        state_dir=bucket_path,
                        batch_size=batch_size,
                        backend=backend,
                        allow_fallback=allow_fallback,
                        compile_options=compile_options,
                    )
                    _record_scan_compile_result(entry, compile_result, default_backend=backend)

                    status = compile_result["status"]
                    if status != "success":
                        entry["terminal_preflight_status"] = status
                        break

                    successful_runs += 1
                    if successful_runs >= max_runs:
                        terminal_preflight = preflight_compile(
                            state_dir=bucket_path,
                            stale_after_seconds=stale_after_seconds,
                        )
                        entry["terminal_preflight_status"] = terminal_preflight["status"]
                        entry["drain_limit_reached"] = terminal_preflight["status"] == "run"
                        break

                    next_preflight = preflight_compile(
                        state_dir=bucket_path,
                        stale_after_seconds=stale_after_seconds,
                    )
                    entry["terminal_preflight_status"] = next_preflight["status"]
                    if next_preflight["status"] != "run":
                        break

                if any(run.get("status") == "error" for run in entry["runs"]):
                    counts["failed"] += 1
                elif any(run.get("status") == "success" for run in entry["runs"]):
                    counts["run"] += 1
                else:
                    # Non-success but non-error (e.g. raced another runner and
                    # got skip_locked mid-run) still counts as skipped.
                    counts["skipped"] += 1
        except Exception as exc:  # noqa: BLE001 — per-bucket isolation is the point
            entry["error"] = f"{type(exc).__name__}: {exc}"
            if entry["preflight_status"] is None:
                entry["preflight_status"] = "error"
            entry["compile_status"] = "error"
            counts["failed"] += 1
        results.append(entry)

    return {
        "home": str(home_dir),
        "total_projects": len(results),
        "results": results,
        "counts": counts,
        "aggregate": _aggregate_scan_stats(results),
    }


def _record_scan_compile_result(entry: dict[str, Any], compile_result: dict[str, Any], *, default_backend: str) -> None:
    run_entry = {
        "status": compile_result.get("status"),
        "processed_count": int(compile_result.get("processed_count", 0) or 0),
        "backend": compile_result.get("backend", default_backend),
        "fallback_backend": compile_result.get("fallback_backend"),
        "receipt_path": compile_result.get("receipt_path"),
        "memory_action_stats": compile_result.get("memory_action_stats", {}),
        "discarded_count": int(compile_result.get("discarded_count", 0) or 0),
        "compiler_observability": compile_result.get("compiler_observability", {}),
    }
    if compile_result.get("error"):
        run_entry["error"] = compile_result.get("error")

    entry["runs"].append(run_entry)
    entry["compile_status"] = run_entry["status"]
    entry["processed_count"] += run_entry["processed_count"]
    entry["backend"] = run_entry["backend"]
    entry["receipt_path"] = run_entry["receipt_path"]
    if run_entry["fallback_backend"]:
        entry["fallback_backend"] = run_entry["fallback_backend"]
    if run_entry.get("error"):
        entry["error"] = run_entry["error"]
    entry["memory_action_stats"] = _merge_memory_action_stats(
        entry.get("memory_action_stats", {}),
        run_entry["memory_action_stats"],
    )
    entry["discarded_count"] = int(entry.get("discarded_count", 0) or 0) + run_entry["discarded_count"]
    entry["compiler_observability"] = _summarize_scan_observability(entry["runs"])


def _merge_memory_action_stats(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    if not base and not update:
        return {}
    merged = {
        "total": int((base or {}).get("total", 0) or 0) + int((update or {}).get("total", 0) or 0),
        "by_action": {"add": 0, "replace": 0, "remove": 0},
        "by_scope": {"user": 0, "global": 0},
    }
    for source in (base or {}, update or {}):
        for key, value in (source.get("by_action") or {}).items():
            if key in merged["by_action"]:
                merged["by_action"][key] += int(value or 0)
        for key, value in (source.get("by_scope") or {}).items():
            if key in merged["by_scope"]:
                merged["by_scope"][key] += int(value or 0)
    if merged["total"] == 0:
        return {}
    return merged


def _summarize_scan_observability(runs: list[dict[str, Any]]) -> dict[str, Any]:
    observations = [
        run.get("compiler_observability")
        for run in runs
        if isinstance(run.get("compiler_observability"), dict) and run.get("compiler_observability")
    ]
    if not observations:
        return {}
    summary = dict(observations[-1])
    summary["runs"] = observations
    summary["run_count"] = len(observations)
    summary["duration_ms"] = sum(int(obs.get("duration_ms", 0) or 0) for obs in observations)
    summary["attempts"] = sum(int(obs.get("attempts", 0) or 0) for obs in observations)
    return summary


def _aggregate_scan_stats(results: list[dict]) -> dict[str, Any]:
    """Roll up per-bucket metrics into a single dict for plugin.log readers.

    Without this every observability question ("did reviewer use replace
    anywhere today?") requires jq-iterating the full results array. With it
    one log line per scan tells the whole story.
    """
    actions = {"add": 0, "replace": 0, "remove": 0}
    scopes = {"user": 0, "global": 0}
    total_suggestions = 0
    buckets_with_fallback = 0
    buckets_processed = 0
    total_discarded = 0
    total_compile_duration_ms = 0
    total_agent_attempts = 0
    total_compile_runs = 0
    buckets_drain_limited = 0
    for entry in results:
        stats = entry.get("memory_action_stats") or {}
        if stats:
            total_suggestions += int(stats.get("total", 0) or 0)
            for key, value in (stats.get("by_action") or {}).items():
                if key in actions:
                    actions[key] += int(value or 0)
            for key, value in (stats.get("by_scope") or {}).items():
                if key in scopes:
                    scopes[key] += int(value or 0)
        if entry.get("compile_status") == "success":
            buckets_processed += 1
            if entry.get("fallback_backend"):
                buckets_with_fallback += 1
            total_discarded += int(entry.get("discarded_count", 0) or 0)
            observability = entry.get("compiler_observability") or {}
            if isinstance(observability, dict):
                total_compile_duration_ms += int(observability.get("duration_ms", 0) or 0)
                total_agent_attempts += int(observability.get("attempts", 0) or 0)
        total_compile_runs += len(entry.get("runs") or [])
        if entry.get("drain_limit_reached"):
            buckets_drain_limited += 1
    return {
        "buckets_processed": buckets_processed,
        "buckets_with_fallback": buckets_with_fallback,
        "total_memory_suggestions": total_suggestions,
        "actions": actions,
        "scopes": scopes,
        "total_discarded": total_discarded,
        "total_compile_duration_ms": total_compile_duration_ms,
        "total_agent_attempts": total_agent_attempts,
        "total_compile_runs": total_compile_runs,
        "buckets_drain_limited": buckets_drain_limited,
    }

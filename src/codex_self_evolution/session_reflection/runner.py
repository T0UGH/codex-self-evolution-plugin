from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import PROJECTS_SUBDIR, Paths, mangle_project_path, resolve_bucket_key, resolve_repo_root
from ..config_file import load_config
from ..managed_skills.publish import codex_skills_dir
from ..storage import atomic_write_json, atomic_write_text, load_json
from .app_server import ReflectionAppServerClient
from .guard import evaluate_recursion_guard
from .paths import build_session_reflection_paths
from .prompt import build_reflection_prompt
from .state import (
    create_job_from_payload,
    acquire_global_lock,
    find_active_parent_job,
    global_lock_status,
    latest_job_path,
    load_job,
    release_global_lock,
    register_child_thread,
    update_job_status,
    utc_timestamp,
)
from .trigger import (
    TriggerLockBusy,
    evaluate_trigger_policy,
    load_trigger_state,
    payload_session_id,
    reset_counters_after_job,
    trigger_paths_for_payload,
    write_trigger_state,
)
from .validation import validate_receipt


def enqueue_reflection_from_payload(payload: dict[str, Any], *, home: str | Path | None = None) -> dict[str, Any]:
    """Evaluate trigger policy and create a queued reflection job when needed."""
    config = load_config(home=Path(home).expanduser().resolve() if home else None).config.session_reflection
    if not config.enabled:
        return {"status": "skipped", "reason": "disabled"}

    guard_decision = evaluate_recursion_guard(payload, home=home)
    if guard_decision.skip:
        return {"status": "skipped", "reason": guard_decision.reason, "detail": guard_decision.detail}

    if not config.trigger.enabled:
        decision = {
            "status": "archive_only",
            "skip_reason": "trigger_disabled",
        }
        return {"status": "archive_only", "reason": "trigger_disabled", "decision": decision}

    parent_session_id = payload_session_id(payload)
    active_job = find_active_parent_job(
        parent_session_id,
        home=home,
        stale_after_seconds=config.trigger.active_job_stale_seconds,
    )
    try:
        trigger_result = evaluate_trigger_policy(payload, config.trigger, home=home, active_job=active_job)
    except TriggerLockBusy as exc:
        return {"status": "archive_only", "reason": "trigger_lock_busy", "detail": str(exc)}

    if trigger_result["status"] != "queued":
        return trigger_result

    state = trigger_result["state"]
    decision = trigger_result["decision"]
    job = create_job_from_payload(
        payload,
        home=home,
        trigger_decision=decision,
        covered_byte_offset=int(state.get("last_counted_byte_offset") or 0),
        covered_message_index=int(state.get("last_counted_message_index") or 0),
        covered_event_uid=str(state.get("last_counted_event_uid") or ""),
        skill_generation_mode=config.trigger.skill_generation_mode,
    )
    state["active_job_id"] = job["job_id"]
    state["active_job_reserved_at"] = job["created_at"]
    write_trigger_state(trigger_result["paths"], state)
    trigger_result["state"] = state
    return {"status": "queued", "job_id": job["job_id"], "job": job, "decision": decision}


def run_reflection_job(
    job_id: str,
    *,
    home: str | Path | None = None,
    client: ReflectionAppServerClient | None = None,
) -> dict[str, Any]:
    """Run one queued reflection job through fork, turn start, and receipt validation."""
    resolved_home = Path(home).expanduser().resolve() if home else None
    config = load_config(home=resolved_home).config.session_reflection
    paths = build_session_reflection_paths(home=resolved_home, job_id=job_id)
    lock_owner_token = ""
    job: dict[str, Any] | None = None
    try:
        lock = acquire_global_lock(home=resolved_home)
        lock_owner_token = lock["owner_token"]
        job = update_job_status(job_id, "running", home=resolved_home)

        project_paths = _build_project_paths_for_home(job["cwd"], home=resolved_home)
        skills_root = codex_skills_dir()
        prompt = build_reflection_prompt(
            job_id=job_id,
            parent_session_id=str(job["parent_session_id"]),
            cwd=str(job["cwd"]),
            memory_user_path=project_paths.memory_dir / "USER.md",
            memory_project_path=project_paths.memory_dir / "MEMORY.md",
            skills_root=skills_root,
            receipt_path=paths.receipt_path,
            review_memory=bool(job.get("review_memory", True)),
            review_skills=bool(job.get("review_skills", True)),
            trigger_reasons=list(job.get("trigger_reasons") or []),
            skill_generation_mode=str(job.get("skill_generation_mode") or "one_shot_active"),
        )
        atomic_write_text(paths.run_dir / "prompt.txt", prompt)

        app_client = client if client is not None else ReflectionAppServerClient(timeout_seconds=config.timeout_seconds)
        child_thread_id, fork_response = app_client.fork_thread(
            parent_thread_id=str(job["parent_session_id"]),
            transcript_path=str(job.get("parent_transcript_path") or ""),
            model=config.model,
            cwd=str(job["cwd"]),
            ephemeral=config.ephemeral,
            sandbox=config.sandbox,
            approval_policy=config.approval_policy,
        )
        register_child_thread(
            child_thread_id=child_thread_id,
            parent_session_id=str(job["parent_session_id"]),
            job_id=job_id,
            home=resolved_home,
        )
        turn_id, turn_response = app_client.start_reflection_turn(
            child_thread_id=child_thread_id,
            cwd=str(job["cwd"]),
            model=config.model,
            approval_policy=config.approval_policy,
            sandbox=config.sandbox,
            prompt=prompt,
        )

        validation = validate_receipt(
            paths.receipt_path,
            memory_roots=[project_paths.memory_dir],
            skills_root=skills_root,
            skill_prefix=config.skill_prefix,
            expected_job_id=job_id,
            expected_parent_session_id=str(job["parent_session_id"]),
            expected_child_thread_id=child_thread_id,
        )
        atomic_write_json(paths.run_dir / "validation.json", validation)
        _reset_trigger_state_for_job(job, validation, home=resolved_home)
        return update_job_status(
            job_id,
            str(validation["status"]),
            home=resolved_home,
            child_thread_id=child_thread_id,
            turn_id=turn_id,
            fork_response=fork_response,
            turn_response=turn_response,
            receipt_path=str(paths.receipt_path),
            validation=validation,
        )
    except Exception as exc:
        _clear_trigger_active_job_on_failure(job_id, job, home=resolved_home)
        return update_job_status(job_id, "failed", home=resolved_home, error=str(exc))
    finally:
        if lock_owner_token:
            release_global_lock(owner_token=lock_owner_token, home=resolved_home)


def session_reflection_status(*, home: str | Path | None = None) -> dict[str, Any]:
    """Return a compact read-only summary of session-reflection state."""
    resolved_home = Path(home).expanduser().resolve() if home else None
    paths = build_session_reflection_paths(home=resolved_home)
    latest_path = latest_job_path(home=resolved_home)
    latest: dict[str, Any] | None = None
    if latest_path.is_file():
        try:
            loaded = load_json(latest_path)
            if not isinstance(loaded, dict):
                raise ValueError("latest job pointer is not an object")
            latest = _status_latest_job(loaded, home=resolved_home)
        except Exception:
            latest = {"unreadable": True, "path": str(latest_path)}
    return {
        "root": str(paths.root),
        "exists": paths.root.exists(),
        "latest": latest,
        "global_lock": global_lock_status(home=resolved_home),
    }


def _status_latest_job(job: dict[str, Any], *, home: Path | None) -> dict[str, Any]:
    """Return the latest-job fields useful for status and failure diagnosis."""
    summary: dict[str, Any] = {
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "updated_at": job.get("updated_at"),
    }
    for key in ("created_at", "error", "receipt_path", "child_thread_id", "turn_id"):
        value = job.get(key)
        if value is not None:
            summary[key] = value
    job_id = job.get("job_id")
    if job_id:
        paths = build_session_reflection_paths(home=home, job_id=str(job_id))
        summary["job_path"] = str(paths.job_path)
        summary.setdefault("receipt_path", str(paths.receipt_path))
        summary["validation_path"] = str(paths.run_dir / "validation.json")
    validation = job.get("validation")
    if isinstance(validation, dict):
        summary["validation"] = _compact_validation(validation)
    return summary


def _compact_validation(validation: dict[str, Any]) -> dict[str, Any]:
    """Return the validation fields needed to diagnose status failures."""
    compact: dict[str, Any] = {}
    for key in ("status", "reason", "error"):
        value = validation.get(key)
        if value is not None:
            compact[key] = value
    return compact


def _build_project_paths_for_home(repo_root: str | Path, *, home: Path | None) -> Paths:
    """Build project-state paths under the explicit CSEP home when provided."""
    if home is None:
        from ..config import build_paths

        return build_paths(repo_root=repo_root, state_dir=None)
    resolved_repo = resolve_repo_root(repo_root)
    bucket_key = resolve_bucket_key(resolved_repo)
    state_dir = home / PROJECTS_SUBDIR / mangle_project_path(bucket_key)
    from ..config import build_paths

    return build_paths(repo_root=resolved_repo, state_dir=state_dir)


def _reset_trigger_state_for_job(job: dict[str, Any], validation: dict[str, Any], *, home: Path | None) -> None:
    """Apply trigger counter reset and clear the matching active job after validation."""
    payload = job.get("raw_payload") if isinstance(job.get("raw_payload"), dict) else {}
    if not payload:
        return
    paths = trigger_paths_for_payload(payload, home=home)
    state = load_trigger_state(paths, session_id=str(job.get("parent_session_id") or "unknown-session"))
    status = str(validation.get("status") or "")
    reason = str(validation.get("reason") or "")
    succeeded = status in {"succeeded", "skipped_empty"}
    job_for_reset = dict(job)
    job_for_reset["review_memory"] = bool(job.get("review_memory", True))
    job_for_reset["review_skills"] = bool(job.get("review_skills", True))
    memory_succeeded = bool(job_for_reset["review_memory"]) and (
        succeeded or (status == "partial" and not _validation_scope_has_issue(validation, "memory"))
    )
    skill_succeeded = bool(job_for_reset["review_skills"]) and (
        succeeded or (status == "partial" and not _validation_scope_has_issue(validation, "skill"))
    )
    updated = reset_counters_after_job(
        state,
        job_for_reset,
        memory_succeeded=memory_succeeded,
        skill_succeeded=skill_succeeded,
        now=utc_timestamp(),
    )
    write_trigger_state(paths, updated)


def _validation_scope_has_issue(validation: dict[str, Any], scope: str) -> bool:
    """Return whether validation contains a memory or skill scoped issue."""
    for item in validation.get("boundary_violations") or []:
        if isinstance(item, dict) and str(item.get("reason") or "").startswith(f"{scope}_"):
            return True
    for item in validation.get("hash_mismatches") or []:
        if isinstance(item, dict) and _path_scope(item.get("path")) == scope:
            return True
    return scope == "skill" and bool(validation.get("invalid_skills"))


def _path_scope(path: object) -> str:
    """Classify a validation path as memory, skill, or unknown."""
    name = Path(str(path or "")).name
    if name in {"USER.md", "MEMORY.md"}:
        return "memory"
    if name == "SKILL.md":
        return "skill"
    return ""


def _clear_trigger_active_job_on_failure(job_id: str, job: dict[str, Any] | None, *, home: Path | None) -> None:
    """Clear this job's trigger reservation after a terminal exception without resetting counters."""
    failed_job = job if job is not None else load_job(job_id, home=home)
    _reset_trigger_state_for_job(failed_job, {"status": "failed"}, home=home)

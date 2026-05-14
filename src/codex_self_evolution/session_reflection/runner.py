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
    global_lock_status,
    latest_job_path,
    release_global_lock,
    register_child_thread,
    update_job_status,
)
from .validation import validate_receipt


def enqueue_reflection_from_payload(payload: dict[str, Any], *, home: str | Path | None = None) -> dict[str, Any]:
    """Create a queued reflection job unless config or guard rules skip it."""
    config = load_config(home=Path(home).expanduser().resolve() if home else None).config.session_reflection
    if not config.enabled:
        return {"status": "skipped", "reason": "disabled"}

    decision = evaluate_recursion_guard(payload, home=home)
    if decision.skip:
        return {"status": "skipped", "reason": decision.reason, "detail": decision.detail}

    job = create_job_from_payload(payload, home=home)
    return {"status": "queued", "job_id": job["job_id"], "job": job}


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

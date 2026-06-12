from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any

from ..config import PROJECTS_SUBDIR, Paths, mangle_project_path, resolve_bucket_key, resolve_repo_root
from ..config_file import load_config
from ..skill_paths import codex_skills_dir
from ..storage import atomic_write_json, atomic_write_text, load_json
from .app_server import ReflectionAppServerClient, app_server_proxy_status
from .guard import evaluate_recursion_guard
from .paths import build_session_reflection_paths
from .prompt import build_reflection_prompt
from .receipt_writer import receipt_writer_error_path
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
    append_decision,
    evaluate_trigger_policy,
    load_trigger_state,
    payload_session_id,
    reset_counters_after_job,
    trigger_paths_for_payload,
    write_trigger_state,
)
from .validation import validate_receipt

RECEIPT_POLL_INTERVAL_SECONDS = 0.05
RECEIPT_STABLE_INVALID_GRACE_SECONDS = 0.5
VALIDATION_ISSUE_KEYS = (
    "boundary_violations",
    "hash_mismatches",
    "invalid_skills",
    "low_value_memory_writes",
)


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

    proxy_status = app_server_proxy_status()
    if not proxy_status["available"]:
        decision = _archive_only_for_unavailable_app_server(
            trigger_result["decision"],
            proxy_status,
        )
        state = trigger_result["state"]
        state["active_job_id"] = None
        state["active_job_reserved_at"] = None
        state["last_decision"] = decision
        write_trigger_state(trigger_result["paths"], state)
        append_decision(trigger_result["paths"], decision)
        trigger_result["state"] = state
        return {
            "status": "archive_only",
            "reason": proxy_status["reason"],
            "decision": decision,
        }

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
        receipt_started_at = utc_timestamp()
        output_snapshot = _snapshot_reflection_outputs(
            memory_dir=project_paths.memory_dir,
            skills_root=skills_root,
            skill_prefix=config.skill_prefix,
        )
        prompt = build_reflection_prompt(
            job_id=job_id,
            parent_session_id=str(job["parent_session_id"]),
            child_thread_id=child_thread_id,
            cwd=str(job["cwd"]),
            memory_path=project_paths.memory_dir / "MEMORY.md",
            memory_refs_dir=project_paths.memory_refs_dir,
            skills_root=skills_root,
            receipt_path=paths.receipt_path,
            review_memory=bool(job.get("review_memory", True)),
            review_skills=bool(job.get("review_skills", True)),
            trigger_reasons=list(job.get("trigger_reasons") or []),
            skill_generation_mode=str(job.get("skill_generation_mode") or "one_shot_active"),
            context_labels=[str(label) for label in job.get("context_labels") or []],
        )
        atomic_write_text(paths.run_dir / "prompt.txt", prompt)
        turn_id, turn_response = app_client.start_reflection_turn(
            child_thread_id=child_thread_id,
            cwd=str(job["cwd"]),
            model=config.model,
            approval_policy=config.approval_policy,
            sandbox=config.sandbox,
            prompt=prompt,
        )
        _wait_for_receipt(paths.receipt_path, timeout_seconds=config.timeout_seconds)
        _prepare_receipt_for_validation(
            paths.receipt_path,
            before=output_snapshot,
            memory_dir=project_paths.memory_dir,
            skills_root=skills_root,
            skill_prefix=config.skill_prefix,
            job_id=job_id,
            parent_session_id=str(job["parent_session_id"]),
            child_thread_id=child_thread_id,
            started_at=receipt_started_at,
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
        validation = _replace_missing_receipt_with_writer_error(paths.receipt_path, validation)
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
        "app_server": app_server_proxy_status(),
        "trigger": _trigger_status_summary(paths),
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
    category = _validation_category(validation)
    if category:
        compact["category"] = category
        compact["issue_counts"] = _validation_issue_counts(validation)
    return compact


def _validation_category(validation: dict[str, Any]) -> str:
    """Classify validation failures by whether receipt or artifacts broke."""
    status = str(validation.get("status") or "")
    if status not in {"failed", "partial"}:
        return ""
    reason = str(validation.get("reason") or "")
    if reason == "draft_invalid":
        return "draft_invalid"
    if reason in {
        "writer_failed",
        "missing_receipt",
        "receipt_missing",
        "receipt_empty",
    }:
        return "writer_failed"
    if reason.startswith("receipt_") or reason in {
        "job_id_mismatch",
        "parent_session_id_mismatch",
        "child_thread_id_mismatch",
    }:
        return "validation_failed"
    if validation.get("boundary_violations"):
        return "child_artifact_boundary"
    if validation.get("hash_mismatches") or validation.get("invalid_skills") or validation.get("low_value_memory_writes"):
        return "child_artifact_quality"
    if validation.get("error"):
        return "system_error"
    return "child_validation"


def _validation_issue_counts(validation: dict[str, Any]) -> dict[str, int]:
    """Return compact counts for validation issue lists present in status."""
    if not any(key in validation for key in VALIDATION_ISSUE_KEYS):
        return {}
    return {
        key: len(value) if isinstance(value := validation.get(key), list) else 0
        for key in VALIDATION_ISSUE_KEYS
    }


def _replace_missing_receipt_with_writer_error(receipt_path: Path, validation: dict[str, Any]) -> dict[str, Any]:
    """Use the writer sidecar when final receipt validation only saw a missing file."""
    if validation.get("reason") not in {"receipt_missing", "missing_receipt"}:
        return validation
    sidecar = receipt_writer_error_path(receipt_path)
    if not sidecar.is_file():
        return validation
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {
            **validation,
            "reason": "writer_failed",
            "error": f"writer error sidecar unreadable: {exc}",
        }
    if not isinstance(payload, dict):
        return {
            **validation,
            "reason": "writer_failed",
            "error": "writer error sidecar is not a JSON object",
        }
    reason = str(payload.get("reason") or "writer_failed")
    if reason not in {"draft_invalid", "writer_failed"}:
        reason = "writer_failed"
    return {
        "status": "failed",
        "reason": reason,
        "error": str(payload.get("message") or payload.get("error") or reason),
        "field": str(payload.get("field") or ""),
        "boundary_violations": [],
        "hash_mismatches": [],
        "invalid_skills": [],
        "low_value_memory_writes": [],
    }


def _wait_for_receipt(receipt_path: Path, *, timeout_seconds: float) -> None:
    """Wait for an asynchronous app-server turn to finish writing its receipt."""
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    last_text: str | None = None
    stable_since = 0.0
    while time.monotonic() < deadline:
        if receipt_path.is_file():
            now = time.monotonic()
            try:
                text = receipt_path.read_text(encoding="utf-8")
            except OSError:
                time.sleep(RECEIPT_POLL_INTERVAL_SECONDS)
                continue
            if text != last_text:
                last_text = text
                stable_since = now
            if _receipt_text_is_parseable(text):
                return
            if now - stable_since >= RECEIPT_STABLE_INVALID_GRACE_SECONDS:
                return
        time.sleep(RECEIPT_POLL_INTERVAL_SECONDS)
    return


def _receipt_text_is_parseable(text: str) -> bool:
    """Return whether receipt text is a complete JSON object."""
    try:
        return isinstance(json.loads(text), dict)
    except ValueError:
        return False


def _prepare_receipt_for_validation(
    receipt_path: Path,
    *,
    before: dict[str, dict[str, str]],
    memory_dir: Path,
    skills_root: Path,
    skill_prefix: str,
    job_id: str,
    parent_session_id: str,
    child_thread_id: str,
    started_at: str,
) -> None:
    """Canonicalize receipt envelope and recover valid durable writes when possible."""
    finished_at = utc_timestamp()
    raw_receipt, raw_error = _load_receipt_object(receipt_path)
    if isinstance(raw_receipt, dict):
        receipt = dict(raw_receipt)
        _canonicalize_receipt_envelope(
            receipt,
            job_id=job_id,
            parent_session_id=parent_session_id,
            child_thread_id=child_thread_id,
            started_at=started_at,
            finished_at=finished_at,
        )
        atomic_write_json(receipt_path, receipt)
        return

    memory_changes, skill_changes = _changed_reflection_outputs(
        before,
        after=_snapshot_reflection_outputs(
            memory_dir=memory_dir,
            skills_root=skills_root,
            skill_prefix=skill_prefix,
        ),
        memory_dir=memory_dir,
    )
    if not memory_changes and not skill_changes:
        return

    invalid_path = _preserve_invalid_receipt(receipt_path)
    recovered_receipt: dict[str, Any] = {
        "schema_version": 1,
        "job_id": job_id,
        "parent_session_id": parent_session_id,
        "child_thread_id": child_thread_id,
        "status": "partial",
        "memory_changes": memory_changes,
        "skill_changes": skill_changes,
        "skipped_candidates": [],
        "validation_notes": [
            {
                "reason": "receipt_recovered_from_durable_outputs",
                "raw_receipt_error": raw_error,
                "raw_receipt_path": str(invalid_path) if invalid_path is not None else "",
            }
        ],
        "errors": [],
        "started_at": started_at,
        "finished_at": finished_at,
    }
    atomic_write_json(receipt_path, recovered_receipt)


def _load_receipt_object(receipt_path: Path) -> tuple[dict[str, Any] | None, str]:
    """Return a parseable receipt object or a compact read/parse failure reason."""
    if not receipt_path.is_file():
        return None, "receipt_missing"
    try:
        text = receipt_path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"receipt_unreadable: {exc}"
    if not text.strip():
        return None, "receipt_empty"
    receipt, parse_error = _parse_receipt_object_text(text)
    if receipt is None:
        return None, f"receipt_invalid_json: {parse_error}"
    if not isinstance(receipt, dict):
        return None, "receipt_not_object"
    return receipt, ""


def _parse_receipt_object_text(text: str) -> tuple[dict[str, Any] | None, str]:
    """Parse common model-written receipt variants into a receipt object."""
    last_error = ""
    for candidate in _receipt_text_candidates(text):
        try:
            loaded = json.loads(candidate)
        except ValueError as exc:
            last_error = str(exc)
            continue
        if isinstance(loaded, dict):
            return loaded, ""
        if isinstance(loaded, str):
            try:
                nested = json.loads(loaded)
            except ValueError as exc:
                last_error = str(exc)
                continue
            if isinstance(nested, dict):
                return nested, ""
    return None, last_error or "not a JSON object"


def _receipt_text_candidates(text: str) -> list[str]:
    """Return raw, fenced, and escaped receipt text candidates."""
    stripped = text.strip()
    candidates = [stripped]
    unfenced = _strip_json_code_fence(stripped)
    if unfenced != stripped:
        candidates.append(unfenced)
    for base in list(candidates):
        decoded = _decode_escaped_receipt_text(base)
        if decoded and decoded not in candidates:
            candidates.append(decoded)
    return candidates


def _strip_json_code_fence(text: str) -> str:
    """Remove a surrounding Markdown JSON code fence from receipt text."""
    lines = text.splitlines()
    if len(lines) >= 3 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text


def _decode_escaped_receipt_text(text: str) -> str:
    """Decode receipt text that escaped quotes/newlines without string quotes."""
    if "\\\"" not in text and "\\n" not in text:
        return ""
    try:
        return text.encode("utf-8").decode("unicode_escape")
    except UnicodeError:
        return ""


def _canonicalize_receipt_envelope(
    receipt: dict[str, Any],
    *,
    job_id: str,
    parent_session_id: str,
    child_thread_id: str,
    started_at: str,
    finished_at: str,
) -> None:
    """Replace deterministic receipt envelope fields with parent-owned values."""
    canonical_fields = {
        "schema_version": 1,
        "job_id": job_id,
        "parent_session_id": parent_session_id,
        "child_thread_id": child_thread_id,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    changed_fields = [key for key, value in canonical_fields.items() if receipt.get(key) != value]
    receipt.update(canonical_fields)
    if changed_fields and isinstance(receipt.get("validation_notes"), list):
        receipt["validation_notes"].append(
            {
                "reason": "receipt_envelope_canonicalized",
                "fields": changed_fields,
            }
        )


def _snapshot_reflection_outputs(
    *,
    memory_dir: Path,
    skills_root: Path,
    skill_prefix: str,
) -> dict[str, dict[str, str]]:
    """Return hashes for durable memory refs and active reflection skills."""
    snapshot: dict[str, dict[str, str]] = {}
    for path in _iter_reflection_output_files(memory_dir=memory_dir, skills_root=skills_root, skill_prefix=skill_prefix):
        if not path.is_file():
            continue
        snapshot[_snapshot_key(path)] = {
            "path": str(path),
            "hash": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return snapshot


def _iter_reflection_output_files(*, memory_dir: Path, skills_root: Path, skill_prefix: str) -> list[Path]:
    """Return files the reflection worker is allowed to mutate durably."""
    files: list[Path] = []
    memory_file = memory_dir / "MEMORY.md"
    if memory_file.exists():
        files.append(memory_file)
    refs_dir = memory_dir / "refs"
    if refs_dir.is_dir():
        files.extend(sorted(path for path in refs_dir.glob("**/*.md") if path.is_file()))
    if skills_root.is_dir():
        for skill_dir in sorted(skills_root.iterdir()):
            if skill_dir.is_dir() and skill_dir.name.startswith(skill_prefix):
                skill_file = skill_dir / "SKILL.md"
                if skill_file.exists():
                    files.append(skill_file)
    return files


def _changed_reflection_outputs(
    before: dict[str, dict[str, str]],
    *,
    after: dict[str, dict[str, str]],
    memory_dir: Path,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return receipt change entries inferred from before/after output hashes."""
    memory_changes: list[dict[str, str]] = []
    skill_changes: list[dict[str, str]] = []
    for key in sorted(set(before) | set(after)):
        before_item = before.get(key)
        after_item = after.get(key)
        before_hash = before_item["hash"] if before_item is not None else ""
        after_hash = after_item["hash"] if after_item is not None else ""
        if before_hash == after_hash:
            continue
        path = Path((after_item or before_item or {}).get("path", ""))
        change = {
            "path": str(path),
            "action": _change_action(before_item, after_item),
            "before_hash": before_hash,
            "after_hash": after_hash,
        }
        if _is_memory_output(path, memory_dir):
            memory_changes.append(change)
        else:
            skill_changes.append(change)
    return memory_changes, skill_changes


def _change_action(before_item: dict[str, str] | None, after_item: dict[str, str] | None) -> str:
    """Return the action name for a before/after file snapshot pair."""
    if before_item is None:
        return "create"
    if after_item is None:
        return "delete"
    return "update"


def _is_memory_output(path: Path, memory_dir: Path) -> bool:
    """Return whether a changed durable output belongs to the memory root."""
    try:
        return path.expanduser().resolve(strict=False).is_relative_to(memory_dir.expanduser().resolve(strict=False))
    except OSError:
        return False


def _snapshot_key(path: Path) -> str:
    """Return a stable key for a local output file path."""
    return str(path.expanduser().resolve(strict=False))


def _preserve_invalid_receipt(receipt_path: Path) -> Path | None:
    """Move an invalid child receipt aside so the recovered receipt keeps evidence."""
    if not receipt_path.exists():
        return None
    invalid_path = receipt_path.with_name("receipt.invalid.txt")
    try:
        shutil.move(str(receipt_path), str(invalid_path))
    except OSError:
        return None
    return invalid_path


def _archive_only_for_unavailable_app_server(
    decision: dict[str, Any],
    proxy_status: dict[str, Any],
) -> dict[str, Any]:
    """Convert a queued trigger decision into an archive-only environment skip."""
    reason = str(proxy_status.get("reason") or "app_server_unavailable")
    warnings = list(decision.get("warnings") or [])
    if reason not in warnings:
        warnings.append(reason)
    return {
        **decision,
        "status": "archive_only",
        "skip_reason": reason,
        "warnings": warnings,
        "app_server": {
            "available": bool(proxy_status.get("available")),
            "reason": proxy_status.get("reason"),
            "socket_path": proxy_status.get("socket_path"),
        },
    }


def _trigger_status_summary(paths: Any) -> dict[str, Any]:
    """Return compact status for session trigger sidecars."""
    triggers_dir = paths.triggers_dir
    if not triggers_dir.is_dir():
        return {"exists": False, "session_count": 0}
    state_files = sorted(triggers_dir.glob("*/state.json"))
    latest_state: dict[str, Any] | None = None
    latest_decision: dict[str, Any] | None = None
    if state_files:
        latest_path = max(state_files, key=lambda item: item.stat().st_mtime)
        try:
            loaded = load_json(latest_path)
            if isinstance(loaded, dict):
                latest_state = {
                    "session_id": loaded.get("session_id"),
                    "stops_since_memory_review": loaded.get("stops_since_memory_review"),
                    "readable_chars_since_memory_review": loaded.get("readable_chars_since_memory_review"),
                    "tool_calls_since_skill_review": loaded.get("tool_calls_since_skill_review"),
                    "active_job_id": loaded.get("active_job_id"),
                    "active_job_reserved_at": loaded.get("active_job_reserved_at"),
                    "last_decision": loaded.get("last_decision"),
                }
                if isinstance(loaded.get("last_decision"), dict):
                    latest_decision = loaded["last_decision"]
        except Exception:
            latest_state = {"unreadable": True, "path": str(latest_path)}
    return {
        "exists": True,
        "session_count": len(state_files),
        "latest_state": latest_state,
        "latest_decision": latest_decision,
    }


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
    for item in validation.get("low_value_memory_writes") or []:
        if scope == "memory" and isinstance(item, dict):
            return True
    return scope == "skill" and bool(validation.get("invalid_skills"))


def _path_scope(path: object) -> str:
    """Classify a validation path as memory, skill, or unknown."""
    path_obj = Path(str(path or ""))
    if path_obj.name == "MEMORY.md" or ("memory" in path_obj.parts and "refs" in path_obj.parts):
        return "memory"
    if path_obj.name == "SKILL.md":
        return "skill"
    return ""


def _clear_trigger_active_job_on_failure(job_id: str, job: dict[str, Any] | None, *, home: Path | None) -> None:
    """Clear this job's trigger reservation after a terminal exception without resetting counters."""
    failed_job = job if job is not None else load_job(job_id, home=home)
    _reset_trigger_state_for_job(failed_job, {"status": "failed"}, home=home)

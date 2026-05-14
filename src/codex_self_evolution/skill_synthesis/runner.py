from __future__ import annotations

import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from ..config_file import load_config
from ..storage import atomic_write_json
from .agent import parse_agent_result, run_pi_skill_synthesis_agent
from .evidence import (
    collect_evidence,
    load_evidence_index,
    materialize_evidence_workspace,
    update_evidence_index,
)
from .inventory import changed_paths, read_skills_inventory, snapshot_synth_skills, write_published_index
from .paths import build_skill_synthesis_paths, resolve_real_skills_root
from .validation import clear_invalid_marker, mark_invalid, validate_synth_skill


AgentCallable = Callable[..., None]


class SkillSynthesisLockError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_id() -> str:
    return uuid.uuid4().hex[:12]


def run_skill_synthesis(
    *,
    home: str | Path | None,
    mode: str | None,
    lookback_hours: int | None,
    lookback_days: int | None,
    dry_run: bool,
    agent_invoker: AgentCallable | None = None,
) -> dict[str, Any]:
    loaded = load_config(home=Path(home).expanduser().resolve() if home else None)
    cfg = loaded.config.skill_synthesis
    if not cfg.enabled:
        return {"status": "skip_unconfigured", "reason": "disabled"}
    if cfg.agent.backend != "agent:pi":
        return {"status": "error", "error": "unsupported_backend", "backend": cfg.agent.backend}

    run_mode = mode or cfg.default_mode
    hours = int(lookback_hours if lookback_hours is not None else cfg.lookback_hours)
    days = int(lookback_days if lookback_days is not None else cfg.lookback_days)
    run_id = _run_id()
    paths = build_skill_synthesis_paths(home=home, run_id=run_id)
    real_skills_root = resolve_real_skills_root()
    paths.root.mkdir(parents=True, exist_ok=True)

    try:
        with _skill_synthesis_lock(paths.lock_path):
            return _run_locked(
                home=paths.home,
                paths=paths,
                run_id=run_id,
                mode=run_mode,
                lookback_hours=hours,
                lookback_days=days,
                dry_run=dry_run,
                real_skills_root=real_skills_root,
                provider=cfg.agent.provider,
                model=cfg.agent.model,
                timeout_seconds=cfg.agent.timeout_seconds,
                agent_invoker=agent_invoker,
            )
    except SkillSynthesisLockError:
        return {"status": "skip_locked", "run_id": run_id}


def _run_locked(
    *,
    home: Path,
    paths,
    run_id: str,
    mode: str,
    lookback_hours: int,
    lookback_days: int,
    dry_run: bool,
    real_skills_root: Path,
    provider: str,
    model: str,
    timeout_seconds: float,
    agent_invoker: AgentCallable | None,
) -> dict[str, Any]:
    started_at = _now()
    evidence_index = load_evidence_index(paths.evidence_index_path)
    evidence = collect_evidence(
        home,
        mode=mode,
        lookback_hours=lookback_hours,
        lookback_days=lookback_days,
        evidence_index=evidence_index,
    )
    real_before = snapshot_synth_skills(real_skills_root)
    skills_root = Path(tempfile.mkdtemp(prefix=f"csep-skill-synthesis-{run_id}-")) / "skills" if dry_run else real_skills_root
    before = snapshot_synth_skills(skills_root)
    inventory = read_skills_inventory(real_skills_root)
    materialize_evidence_workspace(
        paths,
        evidence,
        skills_inventory=inventory,
        synth_inventory=inventory.get("synth", []),
    )

    agent_result = {"valid": False, "actions": [], "result_missing": True, "result_invalid": False}
    error = None
    try:
        if agent_invoker is not None:
            agent_invoker(
                run_id=run_id,
                run_input_dir=paths.run_input_dir,
                run_output_dir=paths.run_output_dir,
                skills_root=skills_root,
            )
            agent_result = parse_agent_result(paths.run_output_dir / "result.json")
        else:
            agent_result = run_pi_skill_synthesis_agent(
                run_input_dir=paths.run_input_dir,
                run_output_dir=paths.run_output_dir,
                skills_root=skills_root,
                provider=provider,
                model=model,
                timeout_seconds=timeout_seconds,
            )
    except Exception as exc:  # noqa: BLE001 - receipt should capture agent failures
        error = f"{type(exc).__name__}: {exc}"

    after = snapshot_synth_skills(skills_root)
    real_after = snapshot_synth_skills(real_skills_root)
    detected = changed_paths(before, after)
    changed_real = changed_paths(real_before, real_after) if dry_run else []
    valid, invalid, retired = _validate_changed(run_id, detected)
    result_missing = bool(agent_result.get("result_missing"))
    result_invalid = bool(agent_result.get("result_invalid"))
    mismatch = _reported_written(agent_result) != detected
    dry_run_leak = dry_run and bool(changed_real)
    status = "success"
    if error or dry_run_leak:
        status = "error"
    elif invalid or mismatch or result_missing or result_invalid:
        status = "partial"
    skipped = [item for item in agent_result.get("actions", []) if item.get("action") == "skip"]
    receipt = {
        "run_id": run_id,
        "status": status,
        "mode": mode,
        "lookback_hours": lookback_hours if mode == "incremental" else None,
        "lookback_days": lookback_days if mode == "full" else None,
        "dry_run": dry_run,
        "evidence_count": len(evidence),
        "new_evidence_count": sum(1 for item in evidence if not item.get("seen_before")),
        "seen_before_count": sum(1 for item in evidence if item.get("seen_before")),
        "reported_written": _reported_written(agent_result),
        "detected_changed": detected,
        "changed_real_paths": changed_real,
        "valid": valid,
        "invalid": invalid,
        "retired": retired,
        "skipped": skipped,
        "mismatch": mismatch,
        "dry_run_leak": dry_run_leak,
        "result_missing": result_missing,
        "result_invalid": result_invalid,
        "error": error,
        "started_at": started_at,
        "finished_at": _now(),
    }
    _write_receipts(paths, receipt)
    update_evidence_index(paths.evidence_index_path, evidence_index, evidence, run_id=run_id, outcome=status)
    write_published_index(paths.published_index_path, real_skills_root, read_skills_inventory(real_skills_root))
    return {**receipt, "receipt_path": str(paths.last_receipt_path)}


def _reported_written(agent_result: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for action in agent_result.get("actions", []):
        if action.get("action") in {"create", "edit", "retire"} and action.get("path"):
            out.append(str(action["path"]))
    return sorted(out)


def _validate_changed(run_id: str, changed: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    retired: list[dict[str, Any]] = []
    for raw in changed:
        skill_path = Path(raw)
        result = validate_synth_skill(skill_path)
        if result.get("valid"):
            clear_invalid_marker(skill_path)
            if result.get("status") == "retired":
                retired.append(result)
            else:
                valid.append(result)
        else:
            mark_invalid(skill_path, run_id=run_id, reasons=[str(result.get("reason") or "invalid")], evidence_keys=[])
            invalid.append(result)
    return valid, invalid, retired


def _write_receipts(paths, receipt: dict[str, Any]) -> None:
    safe_ts = receipt["finished_at"].replace(":", "-")
    receipt_path = paths.receipts_dir / f"{safe_ts}-{receipt['run_id']}.json"
    atomic_write_json(receipt_path, receipt)
    atomic_write_json(paths.last_receipt_path, receipt)


@contextmanager
def _skill_synthesis_lock(lock_path: Path):
    if lock_path.exists():
        raise SkillSynthesisLockError(f"skill synthesis already locked: {lock_path}")
    atomic_write_json(lock_path, {"created_at": _now(), "pid": os.getpid()})
    try:
        yield lock_path
    finally:
        if lock_path.exists():
            lock_path.unlink()

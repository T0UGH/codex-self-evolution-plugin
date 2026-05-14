from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..storage import atomic_write_json

REQUIRED_SKILL_SECTIONS = (
    "## Skill Decision",
    "## When to Use",
    "## Inputs",
    "## Workflow",
    "## Verification",
    "## Failure Handling",
)
VALID_RECEIPT_STATUSES = {"succeeded", "partial", "failed", "skipped"}
REQUIRED_LIST_FIELDS = ("memory_changes", "skill_changes", "skipped_candidates", "validation_notes", "errors")
REQUIRED_TEXT_FIELDS = ("job_id", "parent_session_id", "child_thread_id", "started_at", "finished_at")


def validate_receipt(
    receipt_path: Path,
    *,
    memory_roots: list[Path],
    skills_root: Path,
    skill_prefix: str,
    expected_job_id: str = "",
    expected_parent_session_id: str = "",
    expected_child_thread_id: str = "",
) -> dict[str, Any]:
    """Validate child receipt and normalize its status from durable outputs."""
    if not receipt_path.is_file():
        return _failure("receipt_missing")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        result = _failure("receipt_invalid_json")
        result["error"] = str(exc)
        return result
    if not isinstance(receipt, dict) or receipt.get("schema_version") != 1:
        return _failure("receipt_schema")
    schema_reason = _receipt_schema_reason(receipt)
    if schema_reason:
        return _failure(schema_reason)
    identity_reason = _receipt_identity_reason(
        receipt,
        expected_job_id=expected_job_id,
        expected_parent_session_id=expected_parent_session_id,
        expected_child_thread_id=expected_child_thread_id,
    )
    if identity_reason:
        return _failure(identity_reason)

    memory_changes = receipt.get("memory_changes")
    skill_changes = receipt.get("skill_changes")
    boundary_violations = _memory_boundary_violations(memory_changes, memory_roots)
    boundary_violations.extend(_skill_boundary_violations(skill_changes, skills_root, skill_prefix))
    hash_mismatches = _hash_mismatches(memory_changes) + _hash_mismatches(skill_changes)
    invalid_skills = _invalid_skills(skill_changes, skills_root, skill_prefix)

    status = str(receipt.get("status") or "failed")
    if boundary_violations:
        status = "failed"
    elif invalid_skills or hash_mismatches:
        status = "partial"
    elif not memory_changes and not skill_changes:
        status = "skipped_empty"

    _write_invalid_markers(invalid_skills, receipt.get("job_id"))
    return {
        "status": status,
        "receipt_status": receipt.get("status"),
        "boundary_violations": boundary_violations,
        "hash_mismatches": hash_mismatches,
        "invalid_skills": invalid_skills,
    }


def _failure(reason: str) -> dict[str, Any]:
    """Return the standard failed validation result shape."""
    return {
        "status": "failed",
        "receipt_status": None,
        "reason": reason,
        "boundary_violations": [],
        "hash_mismatches": [],
        "invalid_skills": [],
    }


def _receipt_schema_reason(receipt: dict[str, Any]) -> str:
    """Return a schema or status failure reason for required receipt fields."""
    for field in REQUIRED_TEXT_FIELDS:
        if not isinstance(receipt.get(field), str) or not receipt[field].strip():
            return "receipt_schema"
    if receipt.get("status") not in VALID_RECEIPT_STATUSES:
        return "receipt_status"
    for field in REQUIRED_LIST_FIELDS:
        if not isinstance(receipt.get(field), list):
            return "receipt_schema"
    return ""


def _receipt_identity_reason(
    receipt: dict[str, Any],
    *,
    expected_job_id: str,
    expected_parent_session_id: str,
    expected_child_thread_id: str,
) -> str:
    """Return a failure reason when receipt identity fields do not match."""
    expected = {
        "job_id": expected_job_id,
        "parent_session_id": expected_parent_session_id,
        "child_thread_id": expected_child_thread_id,
    }
    for field, value in expected.items():
        if value and receipt.get(field) != value:
            return f"{field}_mismatch"
    return ""


def _memory_boundary_violations(changes: Any, memory_roots: list[Path]) -> list[dict[str, str]]:
    """Return memory changes outside allowed roots or filenames."""
    if not isinstance(changes, list):
        return [{"reason": "memory_changes_not_list", "path": ""}]
    roots = [root.expanduser().resolve(strict=False) for root in memory_roots]
    violations: list[dict[str, str]] = []
    for item in changes:
        path = _change_path(item)
        if path.name not in {"USER.md", "MEMORY.md"}:
            violations.append({"reason": "memory_filename", "path": str(path)})
        elif not any(path.expanduser().resolve(strict=False).parent == root for root in roots):
            violations.append({"reason": "memory_outside_root", "path": str(path)})
    return violations


def _skill_boundary_violations(changes: Any, skills_root: Path, skill_prefix: str) -> list[dict[str, str]]:
    """Return skill changes outside the allowed csep-reflect namespace."""
    if not isinstance(changes, list):
        return [{"reason": "skill_changes_not_list", "path": ""}]
    root = skills_root.expanduser().resolve(strict=False)
    violations: list[dict[str, str]] = []
    for item in changes:
        path = _change_path(item)
        if path.name != "SKILL.md" or not _under(path, root):
            violations.append({"reason": "skill_outside_root", "path": str(path)})
        elif not path.parent.name.startswith(skill_prefix):
            violations.append({"reason": "skill_outside_namespace", "path": str(path)})
    return violations


def _hash_mismatches(changes: Any) -> list[dict[str, str]]:
    """Return receipt items whose non-blank after_hash does not match bytes."""
    if not isinstance(changes, list):
        return []
    mismatches: list[dict[str, str]] = []
    for item in changes:
        if not isinstance(item, dict):
            continue
        expected = str(item.get("after_hash") or "")
        if not expected:
            continue
        path = _change_path(item)
        if not path.is_file():
            mismatches.append({"path": str(path), "expected": expected, "actual": "", "reason": "missing_file"})
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            mismatches.append({"path": str(path), "expected": expected, "actual": actual, "reason": "hash_mismatch"})
    return mismatches


def _invalid_skills(changes: Any, skills_root: Path, skill_prefix: str) -> list[dict[str, str]]:
    """Validate csep-reflect SKILL.md files referenced by the receipt."""
    if not isinstance(changes, list):
        return []
    root = skills_root.expanduser().resolve(strict=False)
    invalid: list[dict[str, str]] = []
    for item in changes:
        if not isinstance(item, dict):
            continue
        path = _change_path(item)
        if not path.is_file() or not _under(path, root):
            continue
        reason = _validate_skill_doc(path, skill_prefix)
        if reason:
            invalid.append({"path": str(path), "reason": reason})
    return invalid


def _validate_skill_doc(path: Path, skill_prefix: str) -> str:
    """Return an invalid reason for a generated skill, or an empty string."""
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(?P<frontmatter>.*?)\n---\n", text, re.DOTALL)
    if match is None:
        return "missing_frontmatter"
    if _frontmatter_name(match.group("frontmatter")) != path.parent.name:
        return "name_mismatch"
    if not path.parent.name.startswith(skill_prefix):
        return "outside_namespace"
    for section in REQUIRED_SKILL_SECTIONS:
        if section not in text:
            return "missing_required_section"
    return ""


def _frontmatter_name(frontmatter: str) -> str:
    """Extract the literal frontmatter name field from a simple YAML header."""
    for line in frontmatter.splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return ""


def _write_invalid_markers(invalid_skills: list[dict[str, str]], job_id: object) -> None:
    """Write reflection invalid markers beside invalid generated skills."""
    for item in invalid_skills:
        skill_path = Path(item["path"])
        atomic_write_json(
            skill_path.parent / ".csep-invalid.json",
            {
                "schema_version": 1,
                "job_id": str(job_id or ""),
                "skill_path": str(skill_path),
                "reason": item["reason"],
            },
        )


def _change_path(item: object) -> Path:
    """Read a receipt change path without trusting unrelated item shape."""
    if not isinstance(item, dict):
        return Path("")
    return Path(str(item.get("path") or "")).expanduser()


def _under(path: Path, root: Path) -> bool:
    """Return whether path resolves under root without requiring existence."""
    try:
        return path.expanduser().resolve(strict=False).is_relative_to(root)
    except OSError:
        return False

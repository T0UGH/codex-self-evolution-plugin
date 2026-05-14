from __future__ import annotations

import hashlib
import json
from pathlib import Path

from codex_self_evolution.session_reflection.validation import validate_receipt


def _active_skill_text(name: str = "csep-reflect-alpha") -> str:
    """Return a valid active csep-reflect skill document."""
    return (
        "---\n"
        f"name: {name}\n"
        "description: Use when repeated alpha reflection needs concrete command evidence.\n"
        "---\n\n"
        "# Alpha Reflection\n\n"
        "## Skill Decision\n\n"
        "- Why this is a skill: repeated sessions need the same command workflow.\n"
        "- Why not memory: the value is procedural, not a static fact or preference.\n"
        "- Existing skill boundary: no existing skill covers this sequence.\n\n"
        "## When to Use\n\nUse this when alpha reflection needs repeated command evidence.\n\n"
        "## Inputs\n\n- Repository path.\n- Receipt path.\n\n"
        "## Workflow\n\n1. Run the command.\n2. Check the receipt.\n3. Verify the output.\n\n"
        "## Verification\n\nConfirm the receipt has the expected job id.\n\n"
        "## Failure Handling\n\nIf the receipt is missing, stop and report the path.\n"
    )


def _sha(path: Path) -> str:
    """Return the sha256 digest for a test fixture file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_receipt(path: Path, *, memory_changes: list[dict[str, object]], skill_changes: list[dict[str, object]]) -> None:
    """Write a schema-version 1 reflection receipt fixture."""
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "job_id": "job-1",
                "parent_session_id": "parent-1",
                "child_thread_id": "child-1",
                "status": "succeeded",
                "memory_changes": memory_changes,
                "skill_changes": skill_changes,
                "skipped_candidates": [],
                "validation_notes": [],
                "errors": [],
                "started_at": "2026-05-14T12:00:00Z",
                "finished_at": "2026-05-14T12:01:00Z",
            }
        ),
        encoding="utf-8",
    )


def test_validate_receipt_accepts_memory_and_reflect_skill(tmp_path: Path) -> None:
    """Valid memory and csep-reflect skill changes keep the child status."""
    memory = tmp_path / "project" / "memory" / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("Use stable config.\n", encoding="utf-8")
    skill = tmp_path / "skills" / "csep-reflect-alpha" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(_active_skill_text(), encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    _write_receipt(
        receipt,
        memory_changes=[
            {
                "path": str(memory),
                "scope": "global",
                "action": "add",
                "before_hash": "",
                "after_hash": _sha(memory),
                "summary": "record stable config",
            }
        ],
        skill_changes=[
            {
                "skill_id": "csep-reflect-alpha",
                "path": str(skill),
                "action": "create",
                "evidence_kind": "workflow",
                "why_skill_not_memory": "workflow",
                "existing_skill_overlap": "none",
                "before_hash": "",
                "after_hash": _sha(skill),
            }
        ],
    )

    result = validate_receipt(
        receipt,
        memory_roots=[memory.parent],
        skills_root=tmp_path / "skills",
        skill_prefix="csep-reflect-",
    )

    assert result["status"] == "succeeded"
    assert result["receipt_status"] == "succeeded"
    assert result["invalid_skills"] == []
    assert result["boundary_violations"] == []
    assert result["hash_mismatches"] == []


def test_validate_receipt_rejects_skill_outside_namespace(tmp_path: Path) -> None:
    """A SKILL.md outside csep-reflect-* is a hard boundary failure."""
    skill = tmp_path / "skills" / "manual-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(_active_skill_text("manual-skill"), encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    _write_receipt(
        receipt,
        memory_changes=[],
        skill_changes=[{"skill_id": "manual-skill", "path": str(skill), "action": "create", "after_hash": _sha(skill)}],
    )

    result = validate_receipt(
        receipt,
        memory_roots=[tmp_path / "project" / "memory"],
        skills_root=tmp_path / "skills",
        skill_prefix="csep-reflect-",
    )

    assert result["status"] == "failed"
    assert result["boundary_violations"][0]["reason"] == "skill_outside_namespace"


def test_validate_receipt_marks_invalid_skill_partial_and_writes_marker(tmp_path: Path) -> None:
    """A generated skill missing required sections is retained with a reflection marker."""
    skill = tmp_path / "skills" / "csep-reflect-alpha" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        _active_skill_text().replace("## Failure Handling\n\nIf the receipt is missing, stop and report the path.\n", ""),
        encoding="utf-8",
    )
    receipt = tmp_path / "receipt.json"
    _write_receipt(
        receipt,
        memory_changes=[],
        skill_changes=[{"skill_id": "csep-reflect-alpha", "path": str(skill), "action": "create", "after_hash": _sha(skill)}],
    )

    result = validate_receipt(
        receipt,
        memory_roots=[tmp_path / "project" / "memory"],
        skills_root=tmp_path / "skills",
        skill_prefix="csep-reflect-",
    )
    marker = skill.parent / ".csep-invalid.json"

    assert result["status"] == "partial"
    assert result["invalid_skills"][0]["reason"] == "missing_required_section"
    assert marker.is_file()
    assert not (skill.parent / "CSEP_INVALID.json").exists()
    marker_data = json.loads(marker.read_text(encoding="utf-8"))
    assert marker_data["schema_version"] == 1
    assert marker_data["job_id"] == "job-1"
    assert marker_data["skill_path"] == str(skill)
    assert marker_data["reason"] == "missing_required_section"


def test_validate_receipt_rejects_memory_outside_allowed_root(tmp_path: Path) -> None:
    """Memory writes must remain inside the supplied memory roots."""
    memory = tmp_path / "outside" / "MEMORY.md"
    memory.parent.mkdir()
    memory.write_text("Do not accept this path.\n", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    _write_receipt(
        receipt,
        memory_changes=[{"path": str(memory), "action": "add", "after_hash": _sha(memory)}],
        skill_changes=[],
    )

    result = validate_receipt(
        receipt,
        memory_roots=[tmp_path / "project" / "memory"],
        skills_root=tmp_path / "skills",
        skill_prefix="csep-reflect-",
    )

    assert result["status"] == "failed"
    assert result["boundary_violations"][0]["reason"] == "memory_outside_root"


def test_validate_receipt_hash_mismatch_becomes_partial(tmp_path: Path) -> None:
    """A non-blank after_hash must match the bytes on disk."""
    memory = tmp_path / "project" / "memory" / "USER.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("Changed bytes.\n", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    _write_receipt(
        receipt,
        memory_changes=[{"path": str(memory), "action": "add", "after_hash": "0" * 64}],
        skill_changes=[],
    )

    result = validate_receipt(
        receipt,
        memory_roots=[memory.parent],
        skills_root=tmp_path / "skills",
        skill_prefix="csep-reflect-",
    )

    assert result["status"] == "partial"
    assert result["hash_mismatches"][0]["path"] == str(memory)


def test_validate_receipt_no_changes_becomes_skipped_empty(tmp_path: Path) -> None:
    """A valid receipt with no durable writes is normalized to skipped_empty."""
    receipt = tmp_path / "receipt.json"
    _write_receipt(receipt, memory_changes=[], skill_changes=[])

    result = validate_receipt(
        receipt,
        memory_roots=[tmp_path / "project" / "memory"],
        skills_root=tmp_path / "skills",
        skill_prefix="csep-reflect-",
    )

    assert result["status"] == "skipped_empty"
    assert result["receipt_status"] == "succeeded"

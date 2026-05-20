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
    _write_receipt_payload(path, memory_changes=memory_changes, skill_changes=skill_changes)


def _write_receipt_payload(path: Path, **overrides: object) -> None:
    """Write a receipt fixture with optional top-level overrides."""
    payload = {
        "schema_version": 1,
        "job_id": "job-1",
        "parent_session_id": "parent-1",
        "child_thread_id": "child-1",
        "status": "succeeded",
        "memory_changes": [],
        "skill_changes": [],
        "skipped_candidates": [],
        "validation_notes": [],
        "errors": [],
        "started_at": "2026-05-14T12:00:00Z",
        "finished_at": "2026-05-14T12:01:00Z",
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")


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


def test_validate_receipt_accepts_string_change_paths(tmp_path: Path) -> None:
    """Receipt change arrays may contain literal paths from model-written receipts."""
    memory = tmp_path / "project" / "memory" / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("Use stable config.\n", encoding="utf-8")
    skill = tmp_path / "skills" / "csep-reflect-alpha" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(_active_skill_text(), encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    _write_receipt_payload(
        receipt,
        memory_changes=[str(memory)],
        skill_changes=[str(skill)],
    )

    result = validate_receipt(
        receipt,
        memory_roots=[memory.parent],
        skills_root=tmp_path / "skills",
        skill_prefix="csep-reflect-",
    )

    assert result["status"] == "succeeded"
    assert result["boundary_violations"] == []


def test_validate_receipt_accepts_memory_refs_markdown(tmp_path: Path) -> None:
    """Memory refs are allowed under memory/refs as Markdown files."""
    memory_root = tmp_path / "project" / "memory"
    ref = memory_root / "refs" / "design" / "memory-line.md"
    ref.parent.mkdir(parents=True)
    ref.write_text("Long-form memory reference.\n", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    _write_receipt(
        receipt,
        memory_changes=[{"path": str(ref), "action": "add", "after_hash": _sha(ref)}],
        skill_changes=[],
    )

    result = validate_receipt(
        receipt,
        memory_roots=[memory_root],
        skills_root=tmp_path / "skills",
        skill_prefix="csep-reflect-",
    )

    assert result["status"] == "succeeded"
    assert result["boundary_violations"] == []


def test_validate_receipt_flags_duplicate_only_reflection_memory_writes(tmp_path: Path) -> None:
    """Duplicate-only reflection ledgers are not durable memory content."""
    memory_root = tmp_path / "project" / "memory"
    memory = memory_root / "MEMORY.md"
    ref = memory_root / "refs" / "20260519T120637Z-csep-reflection-memory-skills.md"
    ref.parent.mkdir(parents=True)
    memory.write_text(
        "# MEMORY 索引\n\n"
        "- 本次复盘仍未识别新增可复用 `fact`/`rule`/`preference`/`workflow`；"
        "`review memory` 与 `review skills` 均为 `duplicate`。\n",
        encoding="utf-8",
    )
    ref.write_text(
        "# 反思记录\n\n"
        "- 输入：`review memory=true`，`review skills=true`\n"
        "- 结论：无新增可复用 `fact` / `rule` / `preference` / `workflow`。\n"
        "- `memory_changes`: `[]`\n",
        encoding="utf-8",
    )
    receipt = tmp_path / "receipt.json"
    _write_receipt(
        receipt,
        memory_changes=[{"path": str(memory), "action": "update"}, {"path": str(ref), "action": "add"}],
        skill_changes=[],
    )

    result = validate_receipt(
        receipt,
        memory_roots=[memory_root],
        skills_root=tmp_path / "skills",
        skill_prefix="csep-reflect-",
    )

    assert result["status"] == "partial"
    assert result["low_value_memory_writes"] == [
        {"reason": "memory_reflection_noop_summary", "path": str(memory)},
        {"reason": "memory_reflection_noop_ref", "path": str(ref)},
    ]


def test_validate_receipt_flags_duplicate_reflection_refs_without_fixed_noop_phrase(tmp_path: Path) -> None:
    """Reflection refs that only say candidates are duplicate should not be durable memory."""
    memory_root = tmp_path / "project" / "memory"
    ref = memory_root / "refs" / "20260519T113111Z-csep-memory-skills-duplicate.md"
    ref.parent.mkdir(parents=True)
    ref.write_text(
        "## CSEP 反思作业\n\n"
        "本轮 `memory and skills` 复盘复用既有 `csep-reflect-session-receipt` 流程。\n\n"
        "- memory 候选：没有新的长期 fact、rule、preference。\n"
        "- skill 候选：workflow 路径与既有 skill 等价，判定为 duplicate。\n"
        "- durable 变更：仅记录本轮复盘摘要和 receipt.json。\n",
        encoding="utf-8",
    )
    receipt = tmp_path / "receipt.json"
    _write_receipt(
        receipt,
        memory_changes=[{"path": str(ref), "action": "add"}],
        skill_changes=[],
    )

    result = validate_receipt(
        receipt,
        memory_roots=[memory_root],
        skills_root=tmp_path / "skills",
        skill_prefix="csep-reflect-",
    )

    assert result["status"] == "partial"
    assert result["low_value_memory_writes"] == [{"reason": "memory_reflection_noop_ref", "path": str(ref)}]


def test_validate_receipt_flags_no_writable_memory_reflection_refs(tmp_path: Path) -> None:
    """No-writable-memory reflection refs are duplicate ledgers even without filename markers."""
    memory_root = tmp_path / "project" / "memory"
    ref = memory_root / "refs" / "20260519T090154Z-39cbc123.md"
    ref.parent.mkdir(parents=True)
    ref.write_text(
        "# CSEP 复盘记录\n\n"
        "- 任务范围：memory and skills。\n"
        "- 结果：未发现可写入长期 MEMORY 的新 fact/rule/preference。\n"
        "- 判定：继续复用现有 workflow，命中 `csep-reflect-session-receipt`。\n"
        "- 处理：本次无生产代码/配置改动、无新技能落地。\n",
        encoding="utf-8",
    )
    receipt = tmp_path / "receipt.json"
    _write_receipt(
        receipt,
        memory_changes=[{"path": str(ref), "action": "add"}],
        skill_changes=[],
    )

    result = validate_receipt(
        receipt,
        memory_roots=[memory_root],
        skills_root=tmp_path / "skills",
        skill_prefix="csep-reflect-",
    )

    assert result["status"] == "partial"
    assert result["low_value_memory_writes"] == [{"reason": "memory_reflection_noop_ref", "path": str(ref)}]


def test_validate_receipt_allows_non_reflection_duplicate_memory(tmp_path: Path) -> None:
    """Domain notes may mention duplicate behavior without being reflection noise."""
    memory_root = tmp_path / "project" / "memory"
    ref = memory_root / "refs" / "order-idempotency.md"
    ref.parent.mkdir(parents=True)
    ref.write_text(
        "订单接口会把 duplicate request id 作为幂等重试处理，调用方可以复用同一个 request id 查询结果。\n",
        encoding="utf-8",
    )
    receipt = tmp_path / "receipt.json"
    _write_receipt(
        receipt,
        memory_changes=[{"path": str(ref), "action": "add"}],
        skill_changes=[],
    )

    result = validate_receipt(
        receipt,
        memory_roots=[memory_root],
        skills_root=tmp_path / "skills",
        skill_prefix="csep-reflect-",
    )

    assert result["status"] == "succeeded"
    assert result["low_value_memory_writes"] == []


def test_validate_receipt_rejects_legacy_user_memory(tmp_path: Path) -> None:
    """Legacy USER.md is retained on disk but is no longer writable memory."""
    memory = tmp_path / "project" / "memory" / "USER.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("Legacy user memory.\n", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    _write_receipt(
        receipt,
        memory_changes=[{"path": str(memory), "action": "add", "after_hash": _sha(memory)}],
        skill_changes=[],
    )

    result = validate_receipt(
        receipt,
        memory_roots=[memory.parent],
        skills_root=tmp_path / "skills",
        skill_prefix="csep-reflect-",
    )

    assert result["status"] == "failed"
    assert result["boundary_violations"][0]["reason"] == "memory_outside_root"


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
    memory = tmp_path / "project" / "memory" / "MEMORY.md"
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


def test_validate_receipt_missing_memory_with_hash_becomes_partial(tmp_path: Path) -> None:
    """A claimed memory write with a non-blank hash must exist on disk."""
    memory_root = tmp_path / "project" / "memory"
    memory_root.mkdir(parents=True)
    memory = memory_root / "MEMORY.md"
    receipt = tmp_path / "receipt.json"
    _write_receipt(
        receipt,
        memory_changes=[{"path": str(memory), "action": "add", "after_hash": "1" * 64}],
        skill_changes=[],
    )

    result = validate_receipt(
        receipt,
        memory_roots=[memory_root],
        skills_root=tmp_path / "skills",
        skill_prefix="csep-reflect-",
    )

    assert result["status"] == "partial"
    assert result["hash_mismatches"][0]["reason"] == "missing_file"
    assert result["hash_mismatches"][0]["path"] == str(memory)


def test_validate_receipt_missing_skill_with_hash_becomes_partial(tmp_path: Path) -> None:
    """A claimed in-namespace skill write with a non-blank hash must exist."""
    skill = tmp_path / "skills" / "csep-reflect-alpha" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    receipt = tmp_path / "receipt.json"
    _write_receipt(
        receipt,
        memory_changes=[],
        skill_changes=[{"skill_id": "csep-reflect-alpha", "path": str(skill), "action": "create", "after_hash": "2" * 64}],
    )

    result = validate_receipt(
        receipt,
        memory_roots=[tmp_path / "project" / "memory"],
        skills_root=tmp_path / "skills",
        skill_prefix="csep-reflect-",
    )

    assert result["status"] == "partial"
    assert result["hash_mismatches"][0]["reason"] == "missing_file"
    assert result["hash_mismatches"][0]["path"] == str(skill)


def test_validate_receipt_rejects_invalid_status_value(tmp_path: Path) -> None:
    """Receipt status must be one of the known child outcomes."""
    receipt = tmp_path / "receipt.json"
    _write_receipt_payload(receipt, status="banana")

    result = validate_receipt(
        receipt,
        memory_roots=[tmp_path / "project" / "memory"],
        skills_root=tmp_path / "skills",
        skill_prefix="csep-reflect-",
    )

    assert result["status"] == "failed"
    assert result["reason"] == "receipt_status"


def test_validate_receipt_rejects_missing_required_field(tmp_path: Path) -> None:
    """Required receipt fields must exist before validation trusts the payload."""
    receipt = tmp_path / "receipt.json"
    _write_receipt_payload(receipt, job_id="")

    result = validate_receipt(
        receipt,
        memory_roots=[tmp_path / "project" / "memory"],
        skills_root=tmp_path / "skills",
        skill_prefix="csep-reflect-",
    )

    assert result["status"] == "failed"
    assert result["reason"] == "receipt_schema"


def test_validate_receipt_rejects_wrong_expected_job_id(tmp_path: Path) -> None:
    """Expected job id binds a receipt to the current runner job."""
    receipt = tmp_path / "receipt.json"
    _write_receipt(receipt, memory_changes=[], skill_changes=[])

    result = validate_receipt(
        receipt,
        memory_roots=[tmp_path / "project" / "memory"],
        skills_root=tmp_path / "skills",
        skill_prefix="csep-reflect-",
        expected_job_id="job-2",
    )

    assert result["status"] == "failed"
    assert result["reason"] == "job_id_mismatch"


def test_validate_receipt_rejects_wrong_expected_child_thread_id(tmp_path: Path) -> None:
    """Expected child thread id prevents stale child receipts from passing."""
    receipt = tmp_path / "receipt.json"
    _write_receipt(receipt, memory_changes=[], skill_changes=[])

    result = validate_receipt(
        receipt,
        memory_roots=[tmp_path / "project" / "memory"],
        skills_root=tmp_path / "skills",
        skill_prefix="csep-reflect-",
        expected_child_thread_id="child-2",
    )

    assert result["status"] == "failed"
    assert result["reason"] == "child_thread_id_mismatch"


def test_validate_receipt_rejects_nested_memory_under_allowed_root(tmp_path: Path) -> None:
    """Memory path parent must exactly equal an allowed memory root."""
    memory_root = tmp_path / "project" / "memory"
    memory = memory_root / "nested" / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("Nested memory must not be accepted.\n", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    _write_receipt(
        receipt,
        memory_changes=[{"path": str(memory), "action": "add", "after_hash": _sha(memory)}],
        skill_changes=[],
    )

    result = validate_receipt(
        receipt,
        memory_roots=[memory_root],
        skills_root=tmp_path / "skills",
        skill_prefix="csep-reflect-",
    )

    assert result["status"] == "failed"
    assert result["boundary_violations"][0]["reason"] == "memory_outside_root"


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

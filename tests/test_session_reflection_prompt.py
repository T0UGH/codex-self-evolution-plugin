from __future__ import annotations

from pathlib import Path

from codex_self_evolution.session_reflection.prompt import build_reflection_prompt


def test_reflection_prompt_contains_markers_paths_classification_and_receipt_shape(tmp_path: Path) -> None:
    """Prompt exposes the fixed child contract and bounded output locations."""
    prompt = build_reflection_prompt(
        job_id="job-1",
        parent_session_id="parent-1",
        cwd=tmp_path,
        memory_user_path=tmp_path / "memory" / "USER.md",
        memory_project_path=tmp_path / "memory" / "MEMORY.md",
        skills_root=tmp_path / "skills",
        receipt_path=tmp_path / "runs" / "job-1" / "receipt.json",
    )

    assert prompt.count("CSEP_REFLECTION_JOB_ID=job-1") == 1
    assert prompt.count("CSEP_REFLECTION_CHILD=1") == 1
    assert str(tmp_path / "memory" / "USER.md") in prompt
    assert str(tmp_path / "memory" / "MEMORY.md") in prompt
    assert str(tmp_path / "skills" / "csep-reflect-*") in prompt
    assert "fact | rule | preference | workflow | duplicate | transient | sensitive" in prompt
    assert '"memory_changes": []' in prompt
    assert '"skill_changes": []' in prompt
    assert str(tmp_path / "runs" / "job-1" / "receipt.json") in prompt

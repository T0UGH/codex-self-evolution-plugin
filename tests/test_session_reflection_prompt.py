from __future__ import annotations

from pathlib import Path

from codex_self_evolution.session_reflection.prompt import build_reflection_prompt


def test_reflection_prompt_contains_markers_paths_classification_and_receipt_shape(tmp_path: Path) -> None:
    """Prompt exposes the fixed child contract and bounded output locations."""
    prompt = build_reflection_prompt(
        job_id="job-1",
        parent_session_id="parent-1",
        child_thread_id="child-1",
        cwd=tmp_path,
        memory_path=tmp_path / "memory" / "MEMORY.md",
        memory_refs_dir=tmp_path / "memory" / "refs",
        skills_root=tmp_path / "skills",
        receipt_path=tmp_path / "runs" / "job-1" / "receipt.json",
    )

    assert prompt.count("CSEP_REFLECTION_JOB_ID=job-1") == 1
    assert prompt.count("CSEP_REFLECTION_CHILD=1") == 1
    assert prompt.count("Child thread id: child-1") == 1
    assert '"child_thread_id": "child-1"' in prompt
    assert str(tmp_path / "memory" / "MEMORY.md") in prompt
    assert str(tmp_path / "memory" / "refs") in prompt
    assert str(tmp_path / "skills" / "csep-reflect-*") in prompt
    assert "fact | rule | preference | workflow | duplicate | transient | sensitive" in prompt
    assert '"memory_changes": []' in prompt
    assert '"skill_changes": []' in prompt
    assert str(tmp_path / "runs" / "job-1" / "receipt.json") in prompt


def test_reflection_prompt_includes_trigger_scope_and_skill_mode(tmp_path: Path) -> None:
    """Prompt respects explicit artifact review scope."""
    prompt = build_reflection_prompt(
        job_id="job-1",
        parent_session_id="parent-1",
        cwd=tmp_path,
        memory_path=tmp_path / "MEMORY.md",
        memory_refs_dir=tmp_path / "refs",
        skills_root=tmp_path / "skills",
        receipt_path=tmp_path / "receipt.json",
        review_memory=True,
        review_skills=False,
        trigger_reasons=["memory_stop_interval"],
        skill_generation_mode="one_shot_active",
    )

    assert "Review scope: memory" in prompt
    assert "Review memory: true" in prompt
    assert "Review skills: false" in prompt
    assert "Trigger reasons: memory_stop_interval" in prompt
    assert "Skill generation mode: one_shot_active" in prompt
    assert "Do not create, edit, or delete skills. Keep skill_changes empty." in prompt
    assert "Write receipt.json atomically" in prompt
    assert '"child_thread_id": "<current child thread id>"' in prompt


def test_reflection_prompt_keeps_duplicate_noop_reviews_out_of_memory(tmp_path: Path) -> None:
    """Prompt keeps duplicate-only reflection reviews in the receipt, not durable memory."""
    prompt = build_reflection_prompt(
        job_id="job-1",
        parent_session_id="parent-1",
        cwd=tmp_path,
        memory_path=tmp_path / "memory" / "MEMORY.md",
        memory_refs_dir=tmp_path / "memory" / "refs",
        skills_root=tmp_path / "skills",
        receipt_path=tmp_path / "receipt.json",
        review_memory=True,
        review_skills=True,
    )

    assert "If all candidates are duplicate, transient, or sensitive, leave MEMORY.md and refs/ unchanged." in prompt
    assert "Duplicate-only or no-new-candidate review summaries belong only in receipt.json" in prompt
    assert "Do not create a ref just to record that nothing reusable was found." in prompt

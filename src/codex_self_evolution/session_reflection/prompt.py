from __future__ import annotations

from pathlib import Path


def build_reflection_prompt(
    *,
    job_id: str,
    parent_session_id: str,
    cwd: str | Path,
    memory_path: str | Path,
    memory_refs_dir: str | Path,
    skills_root: str | Path,
    receipt_path: str | Path,
    child_thread_id: str = "",
    review_memory: bool = True,
    review_skills: bool = True,
    trigger_reasons: list[str] | None = None,
    skill_generation_mode: str = "one_shot_active",
) -> str:
    """Build the bounded instruction contract for the reflection child."""
    receipt_child_thread_id = child_thread_id or "<current child thread id>"
    scope = _review_scope(review_memory=review_memory, review_skills=review_skills)
    memory_instruction = (
        "Review memory: true\n"
        f"Project memory file: {Path(memory_path)}\n"
        f"Project memory refs directory: {Path(memory_refs_dir)}\n"
        "Write facts, rules, and preferences only to MEMORY.md. Move useful long-form context to refs/ "
        "and leave a short summary plus absolute path in MEMORY.md.\n"
    ) if review_memory else (
        "Review memory: false\n"
        "Do not create, edit, or delete MEMORY.md or refs/ entries. Keep memory_changes empty.\n"
    )
    skill_instruction = (
        f"Review skills: true\n"
        f"Writable skill namespace: {Path(skills_root) / 'csep-reflect-*'}\n"
        "Create or edit active skills only for reusable workflows under csep-reflect-*.\n"
    ) if review_skills else (
        "Review skills: false\n"
        "Do not create, edit, or delete skills. Keep skill_changes empty.\n"
    )
    return (
        "You are the codex-self-evolution session reflection worker.\n\n"
        f"CSEP_REFLECTION_JOB_ID={job_id}\n"
        "CSEP_REFLECTION_CHILD=1\n\n"
        f"Parent session id: {parent_session_id}\n"
        f"Child thread id: {receipt_child_thread_id}\n"
        f"Repository cwd: {Path(cwd)}\n"
        f"Required receipt path: {Path(receipt_path)}\n\n"
        f"Review scope: {scope}\n"
        f"Trigger reasons: {', '.join(trigger_reasons or [])}\n"
        f"Skill generation mode: {skill_generation_mode}\n\n"
        f"{memory_instruction}\n"
        f"{skill_instruction}\n"
        "When Skill generation mode is one_shot_active, a complete workflow candidate may become an active "
        "csep-reflect-* skill in this run.\n"
        "When Skill generation mode is evidence_first, record workflow evidence only in skipped_candidates or "
        "validation_notes; do not publish an active skill or write another durable artifact.\n\n"
        "Your durable outputs are limited by the review scope above.\n"
        "Classify every candidate as: fact | rule | preference | workflow | duplicate | transient | sensitive.\n"
        "Skip transient and sensitive candidates. Do not write secrets, tokens, cookies, private ids, or internal links.\n\n"
        "If all candidates are duplicate, transient, or sensitive, leave MEMORY.md and refs/ unchanged. "
        "Keep memory_changes and skill_changes empty, record the decision in skipped_candidates or validation_notes, "
        "and use receipt status skipped.\n"
        "Duplicate-only or no-new-candidate review summaries belong only in receipt.json, not MEMORY.md or refs/. "
        "Do not create a ref just to record that nothing reusable was found. MEMORY.md is not a per-job changelog.\n\n"
        "Do not write CSEP reflection job metadata, trigger counters, receipt mechanics, or run-state diagnostics "
        "into memory.\n\n"
        "Every active SKILL.md must include Skill Decision, When to Use, Inputs, Workflow, Verification, and Failure Handling.\n"
        "The frontmatter name must match the csep-reflect-* directory name.\n\n"
        "Write receipt.json atomically: write the JSON to a temporary file in the same directory, then rename it "
        "to the required receipt path.\n"
        "Write receipt.json with this exact top-level shape:\n"
        "{\n"
        '  "schema_version": 1,\n'
        f'  "job_id": "{job_id}",\n'
        f'  "parent_session_id": "{parent_session_id}",\n'
        f'  "child_thread_id": "{receipt_child_thread_id}",\n'
        '  "status": "succeeded|partial|failed|skipped",\n'
        '  "memory_changes": [],\n'
        '  "skill_changes": [],\n'
        '  "skipped_candidates": [],\n'
        '  "validation_notes": [],\n'
        '  "errors": [],\n'
        '  "started_at": "<UTC ISO timestamp>",\n'
        '  "finished_at": "<UTC ISO timestamp>"\n'
        "}\n\n"
        "After writing files and receipt, reply with a one-sentence summary only."
    )


def _review_scope(*, review_memory: bool, review_skills: bool) -> str:
    """Return the human-readable artifact scope for the reflection prompt."""
    parts: list[str] = []
    if review_memory:
        parts.append("memory")
    if review_skills:
        parts.append("skills")
    return " and ".join(parts) if parts else "none"

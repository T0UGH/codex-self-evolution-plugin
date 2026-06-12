from __future__ import annotations

from pathlib import Path

from .state import _normalize_context_labels


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
    context_labels: list[str] | None = None,
) -> str:
    """Build the bounded instruction contract for the reflection child."""
    receipt_child_thread_id = child_thread_id or "<current child thread id>"
    receipt_draft_path = Path(receipt_path).with_name("receipt.draft.json")
    scope = _review_scope(review_memory=review_memory, review_skills=review_skills)
    labels = _normalize_context_labels(context_labels or [])
    context_label_text = ", ".join(labels) if labels else "none"
    contamination_instruction = (
        "Do not promote external_web or third_party_document content as durable user preference by default.\n"
        if any(label in {"external_web", "third_party_document"} for label in labels)
        else ""
    )
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
        f"Draft receipt path: {receipt_draft_path}\n"
        f"Required receipt path: {Path(receipt_path)}\n\n"
        f"Review scope: {scope}\n"
        f"Trigger reasons: {', '.join(trigger_reasons or [])}\n"
        f"Skill generation mode: {skill_generation_mode}\n\n"
        f"Context labels: {context_label_text}\n"
        f"{contamination_instruction}\n"
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
        "Do not hand-write the final receipt.json. First write a semantic draft JSON to the Draft receipt path, "
        "then call the schema-safe writer command below to create the final Required receipt path.\n"
        "The writer owns schema_version, job_id, parent_session_id, child_thread_id, started_at, finished_at, "
        "canonical JSON formatting, and atomic final receipt writes. Your responsibility is an accurate draft status "
        "plus memory_changes, skill_changes, skipped_candidates, validation_notes, and errors.\n"
        "Write the draft JSON with this exact top-level shape:\n"
        "{\n"
        '  "status": "succeeded|partial|failed|skipped",\n'
        '  "memory_changes": [],\n'
        '  "skill_changes": [],\n'
        '  "skipped_candidates": [],\n'
        '  "validation_notes": [],\n'
        '  "errors": []\n'
        "}\n"
        "Then run exactly this command, replacing no values:\n"
        "```bash\n"
        "csep session-reflection write-receipt \\\n"
        f"  --draft {receipt_draft_path} \\\n"
        f"  --output {Path(receipt_path)} \\\n"
        f"  --job-id {job_id} \\\n"
        f"  --parent-session-id {parent_session_id} \\\n"
        f"  --child-thread-id {receipt_child_thread_id}\n"
        "```\n"
        "If the writer fails, fix the draft and rerun the command; do not create a fallback receipt manually.\n\n"
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

from __future__ import annotations

from pathlib import Path


def build_reflection_prompt(
    *,
    job_id: str,
    parent_session_id: str,
    cwd: str | Path,
    memory_user_path: str | Path,
    memory_project_path: str | Path,
    skills_root: str | Path,
    receipt_path: str | Path,
) -> str:
    """Build the bounded instruction contract for the reflection child."""
    return (
        "You are the codex-self-evolution session reflection worker.\n\n"
        f"CSEP_REFLECTION_JOB_ID={job_id}\n"
        "CSEP_REFLECTION_CHILD=1\n\n"
        f"Parent session id: {parent_session_id}\n"
        f"Repository cwd: {Path(cwd)}\n"
        f"User memory file: {Path(memory_user_path)}\n"
        f"Project memory file: {Path(memory_project_path)}\n"
        f"Writable skill namespace: {Path(skills_root) / 'csep-reflect-*'}\n"
        f"Required receipt path: {Path(receipt_path)}\n\n"
        "Your only durable outputs are memory and skills.\n"
        "Classify every candidate as: fact | rule | preference | workflow | duplicate | transient | sensitive.\n"
        "Write facts, rules, and preferences only to the memory files above.\n"
        "Create or edit active skills only for reusable workflows under csep-reflect-*.\n"
        "Skip transient and sensitive candidates. Do not write secrets, tokens, cookies, private ids, or internal links.\n\n"
        "Every active SKILL.md must include Skill Decision, When to Use, Inputs, Workflow, Verification, and Failure Handling.\n"
        "The frontmatter name must match the csep-reflect-* directory name.\n\n"
        "Write receipt.json with this exact top-level shape:\n"
        "{\n"
        '  "schema_version": 1,\n'
        f'  "job_id": "{job_id}",\n'
        f'  "parent_session_id": "{parent_session_id}",\n'
        '  "child_thread_id": "<current child thread id>",\n'
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

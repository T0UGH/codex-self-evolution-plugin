from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable


class SkillSynthesisAgentError(RuntimeError):
    pass


AgentInvoker = Callable[[list[str], float], subprocess.CompletedProcess[str]]
_EVIDENCE_KINDS = {"workflow", "fact", "rule", "preference", "duplicate", "transient"}


def build_skill_synthesis_prompt(*, run_input_dir: Path, run_output_dir: Path, skills_root: Path) -> str:
    """Build the bounded SOP and result contract for the synthesis agent."""
    result_path = run_output_dir / "result.json"
    return (
        "You are the codex-self-evolution skill synthesis agent.\n\n"
        f"Input directory: {run_input_dir}\n"
        f"Output result file: {result_path}\n"
        f"Writable skills namespace: {skills_root}/csep-synth-*\n\n"
        "Rules:\n"
        "1. Do not read files outside the input directory.\n"
        "2. Write result.json only to the output result file.\n"
        "3. Write SKILL.md only under the writable skills namespace.\n"
        "4. Create, edit, or retire skills only for repeated reusable workflows.\n"
        "5. Do not create skills for one-off facts, temporary state, secrets, or account identifiers.\n"
        "6. Retired skills must use description \"Retired generated skill. Do not use.\" and csep_status: retired.\n\n"
        "Classify every candidate before writing anything: fact|rule|preference|workflow|duplicate|transient.\n"
        "Only workflow candidates may become active skills. Facts belong in memory, behavior rules belong in AGENTS/memory,\n"
        "preferences belong in USER.md, transient state should be skipped, and duplicate workflows should edit or retire an existing csep-synth skill.\n\n"
        "Active SKILL.md frontmatter must be simple single-line YAML:\n"
        "---\n"
        "name: csep-synth-example\n"
        "description: Use when repeated example workflow needs concrete command guidance.\n"
        "---\n"
        "Do not use block scalar descriptions such as `description: |`.\n"
        "The frontmatter name must match the csep-synth-* directory name exactly.\n"
        "Only retired skills may contain the retired body line.\n\n"
        "Active SKILL.md bodies must use this contract:\n"
        "# <Skill title>\n\n"
        "## Skill Decision\n\n"
        "- Why this is a skill: <why the evidence is a reusable workflow>\n"
        "- Why not memory: <why this is not a static fact, preference, or rule>\n"
        "- Existing skill boundary: <nearest existing skill and why this is not duplicate>\n\n"
        "## When to Use\n\n"
        "<concrete trigger conditions>\n\n"
        "## Inputs\n\n"
        "<required ids, paths, links, env vars, or prerequisites>\n\n"
        "## Workflow\n\n"
        "<ordered tool/command steps>\n\n"
        "## Verification\n\n"
        "<evidence that proves success>\n\n"
        "## Failure Handling\n\n"
        "<when to stop, ask, skip, retire, or report uncertainty>\n\n"
        "Write result.json with exactly this schema:\n"
        "{\n"
        "  \"schema_version\": 1,\n"
        "  \"run_id\": \"<current run id if known>\",\n"
        "  \"actions\": [\n"
        "    {\n"
        "      \"action\": \"create|edit|retire|skip\",\n"
        "      \"skill_id\": \"csep-synth-example\",\n"
        "      \"path\": \"<absolute path to SKILL.md, or empty for skip>\",\n"
        "      \"evidence_keys\": [\"<evidence key>\"],\n"
        "      \"reason\": \"why this action is justified\",\n"
        "      \"evidence_kind\": \"workflow|fact|rule|preference|duplicate|transient\",\n"
        "      \"why_skill_not_memory\": \"required for create/edit\",\n"
        "      \"existing_skill_overlap\": \"nearest existing skill or no direct overlap\"\n"
        "    }\n"
        "  ],\n"
        "  \"notes\": \"optional\"\n"
        "}\n"
        "Do not write legacy result shapes such as synthesized, valid_skills, invalid, retired, or skipped as top-level substitutes for actions.\n\n"
        "After writing files, return exactly DONE."
    )


def build_pi_skill_synthesis_command(*, prompt: str, provider: str, model: str) -> list[str]:
    cmd = [
        "pi",
        "-p",
        "--mode",
        "json",
        "--no-session",
        "--no-context-files",
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        "--no-themes",
        "--tools",
        "read,write,edit,ls",
    ]
    if provider:
        cmd.extend(["--provider", provider])
    if model:
        cmd.extend(["--model", model])
    cmd.append(prompt)
    return cmd


def run_pi_skill_synthesis_agent(
    *,
    run_input_dir: Path,
    run_output_dir: Path,
    skills_root: Path,
    provider: str,
    model: str,
    timeout_seconds: float,
    invoker: AgentInvoker | None = None,
) -> dict[str, Any]:
    if invoker is None and shutil.which("pi") is None:
        raise SkillSynthesisAgentError("pi_unavailable")
    prompt = build_skill_synthesis_prompt(
        run_input_dir=run_input_dir,
        run_output_dir=run_output_dir,
        skills_root=skills_root,
    )
    cmd = build_pi_skill_synthesis_command(prompt=prompt, provider=provider, model=model)
    runner = invoker or _subprocess_run
    proc = runner(cmd, timeout_seconds)
    if proc.returncode != 0:
        raise SkillSynthesisAgentError(f"pi exit={proc.returncode}; stderr={proc.stderr.strip()[:400]}")
    return parse_agent_result(run_output_dir / "result.json")


def _subprocess_run(cmd: list[str], timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout_seconds, check=False)


def parse_agent_result(path: Path) -> dict[str, Any]:
    """Parse and validate the synthesis agent's result.json contract."""
    if not path.is_file():
        return {"valid": False, "actions": [], "result_missing": True, "result_invalid": False}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"valid": False, "actions": [], "result_missing": False, "result_invalid": True, "error": str(exc)}
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        return {"valid": False, "actions": [], "result_missing": False, "result_invalid": True, "error": "schema_version"}
    actions = raw.get("actions")
    if not isinstance(actions, list):
        return {"valid": False, "actions": [], "result_missing": False, "result_invalid": True, "error": "actions"}
    parsed: list[dict[str, Any]] = []
    for item in actions:
        if not isinstance(item, dict):
            return {"valid": False, "actions": [], "result_missing": False, "result_invalid": True, "error": "action_not_object"}
        action = str(item.get("action") or "")
        skill_id = str(item.get("skill_id") or "")
        if action not in {"create", "edit", "retire", "skip"}:
            return {"valid": False, "actions": [], "result_missing": False, "result_invalid": True, "error": "bad_action"}
        if skill_id and not re.fullmatch(r"csep-synth-[a-z0-9-]+", skill_id):
            return {"valid": False, "actions": [], "result_missing": False, "result_invalid": True, "error": "bad_skill_id"}
        evidence_kind = str(item.get("evidence_kind") or "")
        why_skill_not_memory = str(item.get("why_skill_not_memory") or "").strip()
        existing_skill_overlap = str(item.get("existing_skill_overlap") or "").strip()
        if evidence_kind and evidence_kind not in _EVIDENCE_KINDS:
            return {"valid": False, "actions": [], "result_missing": False, "result_invalid": True, "error": "bad_evidence_kind"}
        if action in {"create", "edit"}:
            if not evidence_kind or not why_skill_not_memory or not existing_skill_overlap:
                return {"valid": False, "actions": [], "result_missing": False, "result_invalid": True, "error": "missing_skill_decision"}
            if evidence_kind != "workflow":
                return {"valid": False, "actions": [], "result_missing": False, "result_invalid": True, "error": "non_workflow_skill"}
        parsed.append({
            "action": action,
            "skill_id": skill_id,
            "path": str(item.get("path") or ""),
            "evidence_keys": [str(v) for v in item.get("evidence_keys", []) if isinstance(v, str)],
            "reason": str(item.get("reason") or ""),
            "evidence_kind": evidence_kind,
            "why_skill_not_memory": why_skill_not_memory,
            "existing_skill_overlap": existing_skill_overlap,
        })
    return {
        "valid": True,
        "actions": parsed,
        "result_missing": False,
        "result_invalid": False,
        "notes": str(raw.get("notes") or ""),
    }

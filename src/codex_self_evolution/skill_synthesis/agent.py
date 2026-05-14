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


def build_skill_synthesis_prompt(*, run_input_dir: Path, run_output_dir: Path, skills_root: Path) -> str:
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
        parsed.append({
            "action": action,
            "skill_id": skill_id,
            "path": str(item.get("path") or ""),
            "evidence_keys": [str(v) for v in item.get("evidence_keys", []) if isinstance(v, str)],
            "reason": str(item.get("reason") or ""),
        })
    return {
        "valid": True,
        "actions": parsed,
        "result_missing": False,
        "result_invalid": False,
        "notes": str(raw.get("notes") or ""),
    }

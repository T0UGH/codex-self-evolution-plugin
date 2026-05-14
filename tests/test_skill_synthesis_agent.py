from __future__ import annotations

import json
from pathlib import Path

from codex_self_evolution.skill_synthesis.agent import (
    build_pi_skill_synthesis_command,
    build_skill_synthesis_prompt,
    parse_agent_result,
)


def _create_action(**overrides):
    action = {
        "action": "create",
        "skill_id": "csep-synth-alpha",
        "path": "/tmp/skills/csep-synth-alpha/SKILL.md",
        "evidence_keys": ["ev1"],
        "reason": "repeated workflow",
        "evidence_kind": "workflow",
        "why_skill_not_memory": "This is a reusable command workflow, not a static fact or preference.",
        "existing_skill_overlap": "No existing skill covers this exact trigger and command sequence.",
    }
    action.update(overrides)
    return action


def test_prompt_names_workspace_and_allowed_skills_root(tmp_path: Path) -> None:
    prompt = build_skill_synthesis_prompt(
        run_input_dir=tmp_path / "run" / "input",
        run_output_dir=tmp_path / "run" / "output",
        skills_root=tmp_path / "skills",
    )

    assert str(tmp_path / "run" / "input") in prompt
    assert str(tmp_path / "run" / "output" / "result.json") in prompt
    assert str(tmp_path / "skills" / "csep-synth-*") in prompt
    assert "Do not read files outside the input directory" in prompt
    assert '"schema_version": 1' in prompt
    assert '"actions": [' in prompt
    assert "Do not write legacy result shapes" in prompt
    assert "description: Use when" in prompt
    assert "Classify every candidate" in prompt
    assert "fact|rule|preference|workflow|duplicate|transient" in prompt
    assert "why_skill_not_memory" in prompt
    assert "existing_skill_overlap" in prompt


def test_build_pi_command_uses_independent_provider_model() -> None:
    cmd = build_pi_skill_synthesis_command(
        prompt="hello",
        provider="minimax",
        model="MiniMax-M2.7",
    )

    assert cmd[:9] == [
        "pi", "-p", "--mode", "json", "--no-session", "--no-context-files",
        "--no-extensions", "--no-skills", "--no-prompt-templates",
    ]
    assert "--provider" in cmd
    assert cmd[cmd.index("--provider") + 1] == "minimax"
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "MiniMax-M2.7"
    assert cmd[-1] == "hello"


def test_parse_agent_result_accepts_actions(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({
        "schema_version": 1,
        "run_id": "run-1",
        "actions": [
            {
                **_create_action(),
            }
        ],
    }), encoding="utf-8")

    parsed = parse_agent_result(result_path)

    assert parsed["valid"] is True
    assert parsed["actions"][0]["skill_id"] == "csep-synth-alpha"
    assert parsed["actions"][0]["evidence_kind"] == "workflow"


def test_parse_agent_result_marks_missing_or_invalid_partial(tmp_path: Path) -> None:
    missing = parse_agent_result(tmp_path / "missing.json")
    assert missing["valid"] is False
    assert missing["result_missing"] is True

    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("{bad", encoding="utf-8")
    invalid = parse_agent_result(invalid_path)
    assert invalid["valid"] is False
    assert invalid["result_invalid"] is True


def test_parse_agent_result_rejects_publishable_action_without_skill_decision(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({
        "schema_version": 1,
        "run_id": "run-1",
        "actions": [
            _create_action(evidence_kind="", why_skill_not_memory="", existing_skill_overlap=""),
        ],
    }), encoding="utf-8")

    parsed = parse_agent_result(result_path)

    assert parsed["valid"] is False
    assert parsed["result_invalid"] is True
    assert parsed["error"] == "missing_skill_decision"


def test_parse_agent_result_rejects_publishable_non_workflow_action(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({
        "schema_version": 1,
        "run_id": "run-1",
        "actions": [
            _create_action(evidence_kind="rule"),
        ],
    }), encoding="utf-8")

    parsed = parse_agent_result(result_path)

    assert parsed["valid"] is False
    assert parsed["result_invalid"] is True
    assert parsed["error"] == "non_workflow_skill"


def test_parse_agent_result_rejects_non_synth_skill_id(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({
        "schema_version": 1,
        "run_id": "run-1",
        "actions": [{"action": "edit", "skill_id": "csep-legacy", "path": "", "evidence_keys": [], "reason": ""}],
    }), encoding="utf-8")

    parsed = parse_agent_result(result_path)

    assert parsed["valid"] is False
    assert parsed["result_invalid"] is True

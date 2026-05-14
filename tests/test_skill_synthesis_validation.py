from __future__ import annotations

import json
from pathlib import Path

from codex_self_evolution.skill_synthesis.redaction import redact_secrets
from codex_self_evolution.skill_synthesis.validation import (
    RETIRED_BODY_LINE,
    RETIRED_DESCRIPTION,
    mark_invalid,
    validate_synth_skill,
)


def _active_skill_text(*, body: str | None = None, description: str = "Use when repeated alpha debugging needs command evidence.") -> str:
    workflow_body = body or (
        "# Alpha\n\n"
        "## Skill Decision\n\n"
        "- Why this is a skill: repeated requests need the same command workflow.\n"
        "- Why not memory: the value is procedural, not a static fact or preference.\n"
        "- Existing skill boundary: no existing skill covers this command sequence.\n\n"
        "## When to Use\n\n"
        "Use this when alpha debugging needs command evidence across repeated sessions.\n\n"
        "## Inputs\n\n"
        "- Repository path.\n"
        "- Target run id or receipt path.\n\n"
        "## Workflow\n\n"
        "1. Run `codex-self-evolution status`.\n"
        "2. Check the receipt.\n"
        "3. Verify the output before changing files.\n\n"
        "## Verification\n\n"
        "Confirm the receipt contains the expected run id and status.\n\n"
        "## Failure Handling\n\n"
        "If the receipt is missing, stop and report the missing evidence path.\n"
    )
    return (
        "---\n"
        "name: csep-synth-alpha\n"
        f"description: {description}\n"
        "---\n\n"
        f"{workflow_body}"
    )


def test_redact_secrets_masks_obvious_tokens() -> None:
    text, count = redact_secrets("Authorization: Bearer abcdefghijklmnop\napi_key=sk-1234567890")

    assert count == 2
    assert "abcdefghijklmnop" not in text
    assert "sk-1234567890" not in text
    assert "[REDACTED]" in text


def test_validate_active_skill_accepts_trigger_and_workflow(tmp_path: Path) -> None:
    skill = tmp_path / "csep-synth-alpha" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(_active_skill_text(), encoding="utf-8")

    result = validate_synth_skill(skill)

    assert result["valid"] is True
    assert result["status"] == "active"


def test_validate_rejects_active_skill_without_skill_decision(tmp_path: Path) -> None:
    skill = tmp_path / "csep-synth-alpha" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(_active_skill_text(body=(
        "# Alpha\n\n"
        "## When to Use\n\n"
        "Use this when alpha debugging needs command evidence.\n\n"
        "## Inputs\n\n"
        "- Repository path.\n\n"
        "## Workflow\n\n"
        "1. Run `codex-self-evolution status`.\n"
        "2. Verify the receipt.\n\n"
        "## Verification\n\n"
        "Confirm receipt status.\n\n"
        "## Failure Handling\n\n"
        "Report missing receipt path.\n"
    )), encoding="utf-8")

    result = validate_synth_skill(skill)

    assert result["valid"] is False
    assert result["reason"] == "missing_skill_decision"


def test_validate_rejects_active_skill_without_workflow_contract(tmp_path: Path) -> None:
    skill = tmp_path / "csep-synth-alpha" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(_active_skill_text(body=(
        "# Alpha\n\n"
        "## Skill Decision\n\n"
        "- Why this is a skill: repeated requests need the same command workflow.\n"
        "- Why not memory: the value is procedural, not a static fact or preference.\n"
        "- Existing skill boundary: no existing skill covers this command sequence.\n\n"
        "## Workflow\n\n"
        "1. Run `codex-self-evolution status`.\n"
        "2. Verify the receipt.\n"
    )), encoding="utf-8")

    result = validate_synth_skill(skill)

    assert result["valid"] is False
    assert result["reason"] == "missing_workflow_contract"


def test_validate_rejects_frontmatter_name_mismatch(tmp_path: Path) -> None:
    skill = tmp_path / "csep-synth-alpha" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(
        "---\n"
        "name: alpha\n"
        "description: Use when repeated alpha debugging needs command evidence.\n"
        "---\n\n"
        "# Alpha\n\n## Workflow\n\n"
        "1. Run `codex-self-evolution status`.\n"
        "2. Verify the receipt.\n"
        "3. Check output.\n",
        encoding="utf-8",
    )

    result = validate_synth_skill(skill)

    assert result["valid"] is False
    assert result["reason"] == "name_mismatch"


def test_validate_rejects_multiline_description_frontmatter(tmp_path: Path) -> None:
    skill = tmp_path / "csep-synth-alpha" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(
        "---\n"
        "name: csep-synth-alpha\n"
        "description: |\n"
        "  Use when repeated alpha debugging needs command evidence.\n"
        "---\n\n"
        "# Alpha\n\n## Workflow\n\n"
        "1. Run `codex-self-evolution status`.\n"
        "2. Verify the receipt.\n"
        "3. Check output.\n",
        encoding="utf-8",
    )

    result = validate_synth_skill(skill)

    assert result["valid"] is False
    assert result["reason"] == "unsupported_description_format"


def test_validate_rejects_weak_active_description(tmp_path: Path) -> None:
    skill = tmp_path / "csep-synth-alpha" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(
        "---\n"
        "name: csep-synth-alpha\n"
        "description: Repeated alpha debugging with command evidence.\n"
        "---\n\n"
        "# Alpha\n\n## Workflow\n\n"
        "1. Run `codex-self-evolution status`.\n"
        "2. Verify the receipt.\n"
        "3. Check output.\n",
        encoding="utf-8",
    )

    result = validate_synth_skill(skill)

    assert result["valid"] is False
    assert result["reason"] == "weak_description"


def test_validate_rejects_retired_body_without_retired_status(tmp_path: Path) -> None:
    skill = tmp_path / "csep-synth-alpha" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(
        "---\n"
        "name: csep-synth-alpha\n"
        "description: Use when repeated alpha debugging needs command evidence.\n"
        "---\n\n"
        "# Alpha\n\n## Workflow\n\n"
        "1. Run `codex-self-evolution status`.\n"
        "2. Verify the receipt.\n"
        "3. Check output.\n\n"
        f"{RETIRED_BODY_LINE}\n",
        encoding="utf-8",
    )

    result = validate_synth_skill(skill)

    assert result["valid"] is False
    assert result["reason"] == "retired_body_without_status"


def test_validate_retired_skill_requires_low_trigger_format(tmp_path: Path) -> None:
    skill = tmp_path / "csep-synth-old" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(
        "---\n"
        "name: csep-synth-old\n"
        f"description: \"{RETIRED_DESCRIPTION}\"\n"
        "csep_status: retired\n"
        "---\n\n"
        f"{RETIRED_BODY_LINE}\n",
        encoding="utf-8",
    )

    result = validate_synth_skill(skill)

    assert result["valid"] is True
    assert result["status"] == "retired"


def test_validate_rejects_secret_like_content(tmp_path: Path) -> None:
    skill = tmp_path / "csep-synth-leaky" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(
        "---\nname: csep-synth-leaky\ndescription: Use when testing leaks.\n---\n\n"
        "Run with token=abcdefghi and verify output.\n",
        encoding="utf-8",
    )

    result = validate_synth_skill(skill)

    assert result["valid"] is False
    assert result["reason"] == "secret_like_content"


def test_mark_invalid_writes_marker(tmp_path: Path) -> None:
    skill = tmp_path / "csep-synth-bad" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("bad", encoding="utf-8")

    marker = mark_invalid(skill, run_id="run-1", reasons=["low_signal"], evidence_keys=["ev1"])

    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["run_id"] == "run-1"
    assert data["reasons"] == ["low_signal"]
    assert data["evidence_keys"] == ["ev1"]

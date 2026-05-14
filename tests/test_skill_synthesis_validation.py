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


def test_redact_secrets_masks_obvious_tokens() -> None:
    text, count = redact_secrets("Authorization: Bearer abcdefghijklmnop\napi_key=sk-1234567890")

    assert count == 2
    assert "abcdefghijklmnop" not in text
    assert "sk-1234567890" not in text
    assert "[REDACTED]" in text


def test_validate_active_skill_accepts_trigger_and_workflow(tmp_path: Path) -> None:
    skill = tmp_path / "csep-synth-alpha" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(
        "---\n"
        "name: csep-synth-alpha\n"
        "description: Use when repeated alpha debugging needs command evidence.\n"
        "---\n\n"
        "# Alpha\n\n"
        "## Workflow\n\n"
        "1. Run `codex-self-evolution status`.\n"
        "2. Check the receipt.\n"
        "3. Verify the output before changing files.\n",
        encoding="utf-8",
    )

    result = validate_synth_skill(skill)

    assert result["valid"] is True
    assert result["status"] == "active"


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

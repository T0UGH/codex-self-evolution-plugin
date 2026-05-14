from __future__ import annotations

import json
from pathlib import Path

from codex_self_evolution.diagnostics import collect_status


def test_status_reports_skill_synthesis_receipt_and_counts(tmp_path: Path, monkeypatch) -> None:
    skills_root = tmp_path / "skills"
    active = skills_root / "csep-synth-active"
    active.mkdir(parents=True)
    (active / "SKILL.md").write_text(
        "---\nname: csep-synth-active\ndescription: Use when active repeats.\n---\n\nWorkflow steps check verify run output.\n",
        encoding="utf-8",
    )
    invalid = skills_root / "csep-synth-invalid"
    invalid.mkdir()
    (invalid / "SKILL.md").write_text("bad", encoding="utf-8")
    (invalid / "CSEP_INVALID.json").write_text("{}", encoding="utf-8")
    legacy = skills_root / "csep-legacy"
    legacy.mkdir()
    (legacy / "SKILL.md").write_text("---\nname: csep-legacy\ndescription: legacy\n---\n", encoding="utf-8")

    synth_dir = tmp_path / "skill_synthesis"
    synth_dir.mkdir()
    (synth_dir / "last_receipt.json").write_text(json.dumps({"status": "partial", "run_id": "run-1"}), encoding="utf-8")
    monkeypatch.setenv("CSEP_CODEX_SKILLS_DIR", str(skills_root))

    status = collect_status(home=tmp_path)

    assert status["skill_synthesis"]["last_receipt"]["status"] == "partial"
    assert status["skills"]["synthesized"]["active"] == 1
    assert status["skills"]["synthesized"]["invalid"] == 1
    assert status["skills"]["compiler_legacy"]["active"] == 1

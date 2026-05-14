from __future__ import annotations

import json
from pathlib import Path

from codex_self_evolution.skill_synthesis.runner import run_skill_synthesis


def _write_config(home: Path, enabled: bool = True) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(f"""
[skill_synthesis]
enabled = {str(enabled).lower()}
default_mode = "incremental"
lookback_hours = 24
lookback_days = 30
skills_prefix = "csep-synth-"

[skill_synthesis.agent]
backend = "agent:pi"
provider = "minimax"
model = "MiniMax-M2.7"
timeout_seconds = 1800
""", encoding="utf-8")


def test_run_skill_synthesis_skips_when_disabled(tmp_path: Path) -> None:
    _write_config(tmp_path, enabled=False)

    result = run_skill_synthesis(home=tmp_path, mode=None, lookback_hours=None, lookback_days=None, dry_run=False, agent_invoker=None)

    assert result["status"] == "skip_unconfigured"


def test_run_skill_synthesis_dry_run_uses_temp_root_and_writes_receipt(tmp_path: Path) -> None:
    _write_config(tmp_path)
    calls = []

    def fake_agent(**kwargs):
        calls.append(kwargs)
        skill = Path(kwargs["skills_root"]) / "csep-synth-alpha" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "---\nname: csep-synth-alpha\ndescription: Use when repeated alpha debugging needs command evidence.\n---\n\n"
            "# Alpha\n\n## Workflow\n\n1. Run `codex-self-evolution status`.\n2. Verify the receipt.\n3. Check output.\n",
            encoding="utf-8",
        )
        out = Path(kwargs["run_output_dir"]) / "result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "schema_version": 1,
            "run_id": kwargs["run_id"],
            "actions": [{"action": "create", "skill_id": "csep-synth-alpha", "path": str(skill), "evidence_keys": [], "reason": "test"}],
        }), encoding="utf-8")

    result = run_skill_synthesis(home=tmp_path, mode="incremental", lookback_hours=24, lookback_days=None, dry_run=True, agent_invoker=fake_agent)

    assert result["status"] == "success"
    assert result["dry_run"] is True
    assert result["detected_changed"]
    assert result["dry_run_leak"] is False
    assert Path(result["receipt_path"]).exists()
    assert calls[0]["skills_root"] != Path.home() / ".codex" / "skills"


def test_run_skill_synthesis_marks_partial_when_result_missing(tmp_path: Path) -> None:
    _write_config(tmp_path)

    def fake_agent(**kwargs):
        skill = Path(kwargs["skills_root"]) / "csep-synth-alpha" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "---\nname: csep-synth-alpha\ndescription: Use when repeated alpha debugging needs command evidence.\n---\n\n"
            "# Alpha\n\n## Workflow\n\n1. Run `codex-self-evolution status`.\n2. Verify receipt.\n3. Check output.\n",
            encoding="utf-8",
        )

    result = run_skill_synthesis(home=tmp_path, mode="incremental", lookback_hours=24, lookback_days=None, dry_run=True, agent_invoker=fake_agent)

    assert result["status"] == "partial"
    assert result["result_missing"] is True


def test_run_skill_synthesis_detects_dry_run_leak(tmp_path: Path, monkeypatch) -> None:
    _write_config(tmp_path)
    real_root = tmp_path / "real-skills"
    monkeypatch.setenv("CSEP_CODEX_SKILLS_DIR", str(real_root))

    def fake_agent(**kwargs):
        leaked = real_root / "csep-synth-leak" / "SKILL.md"
        leaked.parent.mkdir(parents=True)
        leaked.write_text("leak", encoding="utf-8")
        out = Path(kwargs["run_output_dir"]) / "result.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"schema_version": 1, "run_id": kwargs["run_id"], "actions": []}), encoding="utf-8")

    result = run_skill_synthesis(home=tmp_path, mode="incremental", lookback_hours=24, lookback_days=None, dry_run=True, agent_invoker=fake_agent)

    assert result["status"] == "error"
    assert result["dry_run_leak"] is True
    assert result["changed_real_paths"] == [str(real_root / "csep-synth-leak" / "SKILL.md")]

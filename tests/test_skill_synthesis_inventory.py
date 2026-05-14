from __future__ import annotations

from pathlib import Path

from codex_self_evolution.skill_synthesis.inventory import (
    read_skills_inventory,
    snapshot_synth_skills,
)
from codex_self_evolution.skill_synthesis.paths import build_skill_synthesis_paths


def test_paths_use_global_skill_synthesis_home(tmp_path: Path) -> None:
    paths = build_skill_synthesis_paths(home=tmp_path, run_id="run-1")

    assert paths.root == tmp_path / "skill_synthesis"
    assert paths.runs_dir == paths.root / "runs"
    assert paths.run_input_dir == paths.root / "runs" / "run-1" / "input"
    assert paths.run_output_dir == paths.root / "runs" / "run-1" / "output"
    assert paths.receipts_dir == paths.root / "receipts"


def test_inventory_reads_frontmatter_only_for_non_synth(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    user_skill = skills_root / "manual-skill"
    user_skill.mkdir(parents=True)
    (user_skill / "SKILL.md").write_text(
        "---\nname: manual-skill\ndescription: Manual skill summary.\n---\n\nSECRET_BODY\n",
        encoding="utf-8",
    )
    synth_skill = skills_root / "csep-synth-alpha"
    synth_skill.mkdir()
    (synth_skill / "SKILL.md").write_text(
        "---\nname: csep-synth-alpha\ndescription: Use when alpha repeats.\n---\n\nWorkflow body\n",
        encoding="utf-8",
    )

    inventory = read_skills_inventory(skills_root)

    assert inventory["non_synth"][0]["name"] == "manual-skill"
    assert inventory["non_synth"][0]["description"] == "Manual skill summary."
    assert "SECRET_BODY" not in str(inventory)
    assert inventory["synth"][0]["skill_id"] == "csep-synth-alpha"


def test_snapshot_synth_skills_only_tracks_synth_prefix(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    (skills_root / "csep-synth-alpha").mkdir(parents=True)
    (skills_root / "csep-synth-alpha" / "SKILL.md").write_text("alpha", encoding="utf-8")
    (skills_root / "csep-legacy").mkdir()
    (skills_root / "csep-legacy" / "SKILL.md").write_text("legacy", encoding="utf-8")

    snapshot = snapshot_synth_skills(skills_root)

    assert list(snapshot) == [str(skills_root / "csep-synth-alpha" / "SKILL.md")]

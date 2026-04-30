from codex_self_evolution.config import PLUGIN_OWNER
from codex_self_evolution.managed_skills.publish import global_skill_id, publish_global_skills
from codex_self_evolution.schemas import SkillManifestEntry


def _entry(skill_id: str, *, status: str = "active") -> SkillManifestEntry:
    return SkillManifestEntry(
        skill_id=skill_id,
        action="create",
        title="Trace Debug",
        path=f"skills/managed/{skill_id}.md",
        status=status,
        owner=PLUGIN_OWNER,
        managed=True,
        created_by=PLUGIN_OWNER,
        updated_at="2026-04-30T00:00:00Z",
    )


def test_global_skill_id_is_prefixed():
    assert global_skill_id("trace-debug") == "csep-trace-debug"
    assert global_skill_id("csep-trace-debug") == "csep-trace-debug"


def test_publish_global_skills_writes_csep_managed_projection(tmp_path):
    result = publish_global_skills(
        [
            {
                "skill_id": "trace-debug",
                "title": "Trace Debug",
                "content": "Use when debugging a repeated trace workflow with local command evidence.",
                "action": "create",
            }
        ],
        [_entry("trace-debug")],
        skills_root=tmp_path / "skills",
    )

    target = tmp_path / "skills" / "csep-managed" / "csep-trace-debug" / "SKILL.md"
    assert result["published"] == [str(target)]
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert "managed-by: codex-self-evolution-plugin" in content
    assert "source-skill-id: trace-debug" in content


def test_publish_global_skills_skips_low_signal_content(tmp_path):
    result = publish_global_skills(
        [{"skill_id": "thin", "title": "Thin", "content": "too short", "action": "create"}],
        [_entry("thin")],
        skills_root=tmp_path / "skills",
    )

    assert not result["published"]
    assert result["skipped"][0]["reason"] == "low_signal"


def test_retire_unpublishes_generated_projection(tmp_path):
    target = tmp_path / "skills" / "csep-managed" / "csep-old" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Old\n", encoding="utf-8")

    result = publish_global_skills(
        [{"skill_id": "old", "title": "Old", "content": "", "action": "retire"}],
        [_entry("old", status="retired")],
        skills_root=tmp_path / "skills",
    )

    assert result["unpublished"] == [str(target.parent)]
    assert not target.parent.exists()

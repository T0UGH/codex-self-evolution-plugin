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


def test_publish_global_skills_writes_valid_skill_frontmatter(tmp_path):
    legacy_target = tmp_path / "skills" / "csep-managed" / "csep-trace-debug" / "SKILL.md"
    legacy_target.parent.mkdir(parents=True)
    legacy_target.write_text("# Old Nested\n", encoding="utf-8")

    result = publish_global_skills(
        [
            {
                "skill_id": "trace-debug",
                "title": "Trace Debug",
                "description": "This skill should be used when debugging repeated trace workflows with local command evidence.",
                "content": "## Workflow\n\n1. Inspect the trace id.\n2. Run the local log lookup command.\n3. Summarize the exact evidence.",
                "action": "create",
            }
        ],
        [_entry("trace-debug")],
        skills_root=tmp_path / "skills",
    )

    target = tmp_path / "skills" / "csep-trace-debug" / "SKILL.md"
    assert result["published"] == [str(target)]
    assert result["unpublished"] == [str(legacy_target.parent)]
    assert target.exists()
    assert not legacy_target.parent.exists()
    content = target.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert 'name: "Trace Debug"' in content
    assert 'description: "This skill should be used when debugging repeated trace workflows' in content
    assert "---\n\n# Trace Debug" in content
    assert "managed-by: codex-self-evolution-plugin" in content
    assert "source-skill-id: trace-debug" in content


def test_publish_global_skills_escapes_quoted_frontmatter(tmp_path):
    result = publish_global_skills(
        [
            {
                "skill_id": "trace-debug",
                "title": 'Trace "Debug"',
                "description": r'This skill should be used when checking C:\tmp\trace "debug" output.',
                "content": "## Workflow\n\n1. Inspect the trace id.\n2. Run the local log lookup command.\n3. Summarize the exact evidence.",
                "action": "create",
            }
        ],
        [_entry("trace-debug")],
        skills_root=tmp_path / "skills",
    )

    target = tmp_path / "skills" / "csep-trace-debug" / "SKILL.md"
    assert result["published"] == [str(target)]
    content = target.read_text(encoding="utf-8")
    assert 'name: "Trace \\"Debug\\""' in content
    assert r'description: "This skill should be used when checking C:\\tmp\\trace \"debug\" output."' in content


def test_publish_global_skills_skips_missing_description(tmp_path):
    direct_target = tmp_path / "skills" / "csep-thin" / "SKILL.md"
    legacy_target = tmp_path / "skills" / "csep-managed" / "csep-thin" / "SKILL.md"
    direct_target.parent.mkdir(parents=True)
    legacy_target.parent.mkdir(parents=True)
    direct_target.write_text("# Old Direct\n", encoding="utf-8")
    legacy_target.write_text("# Old Nested\n", encoding="utf-8")

    result = publish_global_skills(
        [
            {
                "skill_id": "thin",
                "title": "Thin",
                "content": "## Workflow\n\n1. Do something repeatable with evidence.",
                "action": "create",
            }
        ],
        [_entry("thin")],
        skills_root=tmp_path / "skills",
    )

    assert result["published"] == []
    assert result["unpublished"] == [str(direct_target.parent), str(legacy_target.parent)]
    assert not direct_target.parent.exists()
    assert not legacy_target.parent.exists()
    assert result["skipped"][0]["reason"] == "missing_description"


def test_publish_global_skills_skips_weak_description(tmp_path):
    result = publish_global_skills(
        [
            {
                "skill_id": "thin",
                "title": "Thin",
                "description": "Debugging repeated trace workflows with command evidence.",
                "content": "## Workflow\n\n1. Inspect the trace id.\n2. Run the local log lookup command.\n3. Verify the exact evidence.",
                "action": "create",
            }
        ],
        [_entry("thin")],
        skills_root=tmp_path / "skills",
    )

    assert result["published"] == []
    assert result["skipped"][0]["reason"] == "weak_description"


def test_publish_global_skills_skips_low_signal_content(tmp_path):
    result = publish_global_skills(
        [
            {
                "skill_id": "thin",
                "title": "Thin",
                "description": "This skill should be used when checking whether a generated skill has enough signal.",
                "content": "too short",
                "action": "create",
            }
        ],
        [_entry("thin")],
        skills_root=tmp_path / "skills",
    )

    assert not result["published"]
    assert result["skipped"][0]["reason"] == "low_signal"


def test_retire_unpublishes_generated_projection(tmp_path):
    direct_target = tmp_path / "skills" / "csep-old" / "SKILL.md"
    nested_target = tmp_path / "skills" / "csep-managed" / "csep-old" / "SKILL.md"
    direct_target.parent.mkdir(parents=True)
    nested_target.parent.mkdir(parents=True)
    direct_target.write_text("# Old Direct\n", encoding="utf-8")
    nested_target.write_text("# Old Nested\n", encoding="utf-8")

    result = publish_global_skills(
        [{"skill_id": "old", "title": "Old", "content": "", "action": "retire"}],
        [_entry("old", status="retired")],
        skills_root=tmp_path / "skills",
    )

    assert result["unpublished"] == [str(direct_target.parent), str(nested_target.parent)]
    assert not direct_target.parent.exists()
    assert not nested_target.parent.exists()

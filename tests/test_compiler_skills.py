from codex_self_evolution.compiler.skills import build_manifest_entries, compile_skills
from codex_self_evolution.config import PLUGIN_OWNER
from codex_self_evolution.schemas import SkillManifestEntry, Suggestion



def test_compile_skills_filters_low_signal_and_builds_manifest():
    suggestions = [
        Suggestion(
            family="skill_action",
            summary="create good skill",
            details={
                "action": "create",
                "skill_id": "Useful Skill",
                "title": "Useful Skill",
                "description": "This skill should be used when repeated repo tasks appear.",
                "content": "Do this when repeated repo tasks appear.",
            },
        ),
        Suggestion(
            family="skill_action",
            summary="ignore noise",
            details={
                "action": "create",
                "skill_id": "noise",
                "title": "Noise",
                "description": "This skill should be used when checking noisy skill suggestions.",
                "content": "too short",
            },
        ),
    ]
    compiled, discarded = compile_skills(suggestions)
    assert len(compiled) == 1
    assert compiled[0]["description"] == "This skill should be used when repeated repo tasks appear."
    assert discarded[0]["reason"] == "low_signal"
    entries = build_manifest_entries(compiled, "skills")
    assert entries[0].skill_id == "useful-skill"
    assert entries[0].owner == PLUGIN_OWNER
    assert entries[0].managed is True



def test_compile_skills_enforces_managed_ownership_for_patch_and_edit():
    suggestions = [
        Suggestion(
            family="skill_action",
            summary="patch skill",
            details={
                "action": "patch",
                "skill_id": "Useful Skill",
                "title": "Useful Skill",
                "description": "This skill should be used when patching repeated managed workflow gaps.",
                "content": "Patch the managed workflow when repeated gaps appear.",
            },
        )
    ]
    unmanaged = [
        SkillManifestEntry(
            skill_id="useful-skill",
            action="create",
            title="Useful Skill",
            path="skills/user/useful-skill.md",
            status="active",
            owner="user",
            managed=False,
            created_by="user",
            updated_at="2026-01-01T00:00:00Z",
        )
    ]
    compiled, discarded = compile_skills(suggestions, existing_entries=unmanaged)
    assert compiled == []
    assert discarded[0]["reason"] == "ownership_violation"


def test_compile_skills_discards_missing_description_for_publishable_actions():
    suggestions = [
        Suggestion(
            family="skill_action",
            summary="create missing description",
            details={
                "action": "create",
                "skill_id": "Missing Description",
                "title": "Missing Description",
                "content": "Run focused checks when repeated repo tasks appear.",
            },
        )
    ]

    compiled, discarded = compile_skills(suggestions)

    assert compiled == []
    assert discarded[0]["reason"] == "missing_description"

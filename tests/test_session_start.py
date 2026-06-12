import hashlib
import json

from codex_self_evolution.hooks.session_start import session_start


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_session_start_injects_memory_and_short_recall_pointer(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    (state / "memory").mkdir(parents=True)
    (state / "memory" / "USER.md").write_text("# USER\n\nDo not inject me.\n", encoding="utf-8")
    (state / "memory" / "MEMORY.md").write_text("# MEMORY\n\nRun focused tests first.\n", encoding="utf-8")
    (state / "memory" / "refs").mkdir()
    (state / "memory" / "refs" / "detail.md").write_text("Cold detail.\n", encoding="utf-8")
    result = session_start(cwd=repo, state_dir=state)
    assert result["hook"] == "SessionStart"
    assert "recall" in result["recall"]["policy"].lower()
    assert "csep-session-recall" == result["recall"]["skill"]["skill_id"]
    assert result["recall"]["skill"]["provided_by"] == "plugin"
    assert "content" not in result["recall"]["skill"]
    assert result["runtime"]["session_context"]["thread_start_injected"] is True
    assert "Run focused tests first." in result["stable_background"]["current_memory_md"]
    assert "Do not inject me." not in result["stable_background"]["combined_prefix"]
    assert "Cold detail." not in result["stable_background"]["combined_prefix"]
    assert result["stable_background"]["legacy_user_md_ignored"] is True
    assert "# Stable Background" in result["stable_background"]["combined_prefix"]
    assert "## Recall Contract" not in result["stable_background"]["combined_prefix"]
    assert "# Session Recall Skill" not in result["stable_background"]["combined_prefix"]
    assert result["stable_background"]["memory_source"] == "MEMORY.md"
    assert result["stable_background"]["memory_fallback_used"] is True
    assert result["stable_background"]["memory_fallback_reason"] == "summary_missing"
    assert result["stable_background"]["memory_source_path"] == str(state / "memory" / "MEMORY.md")
    assert result["stable_background"]["memory_summary_path"] == str(state / "memory" / "memory_summary.md")
    assert result["stable_background"]["memory_summary_meta_path"] == str(state / "memory" / "memory_summary.meta.json")
    assert (state / "memory").exists()
    assert (state / "memory" / "refs").exists()
    json.dumps(result)


def test_session_start_prefers_valid_memory_summary(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    memory_dir = state / "memory"
    memory_dir.mkdir(parents=True)
    memory_text = "# MEMORY\n\nFull detail should stay cold.\n"
    summary_text = "# Memory Summary\n\nHot summary only.\n"
    (memory_dir / "MEMORY.md").write_text(memory_text, encoding="utf-8")
    (memory_dir / "memory_summary.md").write_text(summary_text, encoding="utf-8")
    (memory_dir / "memory_summary.meta.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "MEMORY.md",
                "source_memory_sha256": _sha256_text(memory_text),
                "summary_sha256": _sha256_text(summary_text),
                "generated_at": "2026-06-12T00:00:00Z",
                "generator": "session_reflection_consolidation",
            }
        ),
        encoding="utf-8",
    )

    result = session_start(cwd=repo, state_dir=state)

    assert result["stable_background"]["memory_source"] == "memory_summary.md"
    assert result["stable_background"]["memory_fallback_used"] is False
    assert result["stable_background"]["memory_fallback_reason"] == ""
    assert result["stable_background"]["current_memory_md"] == summary_text
    assert "## memory_summary.md" in result["stable_background"]["combined_prefix"]
    assert "Hot summary only." in result["stable_background"]["combined_prefix"]
    assert "Full detail should stay cold." not in result["stable_background"]["combined_prefix"]


def test_session_start_falls_back_when_memory_summary_is_directory(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    memory_dir = state / "memory"
    memory_dir.mkdir(parents=True)
    memory_text = "# MEMORY\n\nFull detail remains available.\n"
    (memory_dir / "MEMORY.md").write_text(memory_text, encoding="utf-8")
    (memory_dir / "memory_summary.md").mkdir()

    result = session_start(cwd=repo, state_dir=state)

    assert result["stable_background"]["memory_source"] == "MEMORY.md"
    assert result["stable_background"]["memory_fallback_used"] is True
    assert result["stable_background"]["memory_fallback_reason"] == "summary_empty"
    assert result["stable_background"]["current_memory_md"] == memory_text
    assert "Full detail remains available." in result["stable_background"]["combined_prefix"]


def test_session_start_falls_back_when_summary_source_hash_is_stale(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    memory_dir = state / "memory"
    memory_dir.mkdir(parents=True)
    memory_text = "# MEMORY\n\nFresh full memory.\n"
    summary_text = "# Memory Summary\n\nStale summary.\n"
    (memory_dir / "MEMORY.md").write_text(memory_text, encoding="utf-8")
    (memory_dir / "memory_summary.md").write_text(summary_text, encoding="utf-8")
    (memory_dir / "memory_summary.meta.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "MEMORY.md",
                "source_memory_sha256": _sha256_text("# MEMORY\n\nOld full memory.\n"),
                "summary_sha256": _sha256_text(summary_text),
                "generated_at": "2026-06-12T00:00:00Z",
                "generator": "session_reflection_consolidation",
            }
        ),
        encoding="utf-8",
    )

    result = session_start(cwd=repo, state_dir=state)

    assert result["stable_background"]["memory_source"] == "MEMORY.md"
    assert result["stable_background"]["memory_fallback_used"] is True
    assert result["stable_background"]["memory_fallback_reason"] == "source_hash_mismatch"
    assert result["stable_background"]["current_memory_md"] == memory_text
    assert "Fresh full memory." in result["stable_background"]["combined_prefix"]
    assert "Stale summary." not in result["stable_background"]["combined_prefix"]


def test_session_start_falls_back_when_summary_hash_mismatches(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    memory_dir = state / "memory"
    memory_dir.mkdir(parents=True)
    memory_text = "# MEMORY\n\nFull memory.\n"
    summary_text = "# Memory Summary\n\nEdited without meta update.\n"
    (memory_dir / "MEMORY.md").write_text(memory_text, encoding="utf-8")
    (memory_dir / "memory_summary.md").write_text(summary_text, encoding="utf-8")
    (memory_dir / "memory_summary.meta.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "MEMORY.md",
                "source_memory_sha256": _sha256_text(memory_text),
                "summary_sha256": _sha256_text("# Memory Summary\n\nPrevious summary.\n"),
                "generated_at": "2026-06-12T00:00:00Z",
                "generator": "session_reflection_consolidation",
            }
        ),
        encoding="utf-8",
    )

    result = session_start(cwd=repo, state_dir=state)

    assert result["stable_background"]["memory_source"] == "MEMORY.md"
    assert result["stable_background"]["memory_fallback_used"] is True
    assert result["stable_background"]["memory_fallback_reason"] == "summary_hash_mismatch"
    assert result["stable_background"]["current_memory_md"] == memory_text
    assert "Full memory." in result["stable_background"]["combined_prefix"]
    assert "Edited without meta update." not in result["stable_background"]["combined_prefix"]


def test_session_start_falls_back_when_summary_meta_is_invalid_json(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    memory_dir = state / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("# MEMORY\n\nSafe full memory.\n", encoding="utf-8")
    (memory_dir / "memory_summary.md").write_text("# Memory Summary\n\nBroken meta summary.\n", encoding="utf-8")
    (memory_dir / "memory_summary.meta.json").write_text("{not-json", encoding="utf-8")

    result = session_start(cwd=repo, state_dir=state)

    assert result["stable_background"]["memory_source"] == "MEMORY.md"
    assert result["stable_background"]["memory_fallback_used"] is True
    assert result["stable_background"]["memory_fallback_reason"] == "summary_meta_invalid"
    assert "Safe full memory." in result["stable_background"]["combined_prefix"]
    assert "Broken meta summary." not in result["stable_background"]["combined_prefix"]
    json.dumps(result)

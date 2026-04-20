import json

from codex_self_evolution.compiler.backends import build_compile_context
from codex_self_evolution.config import build_paths
from codex_self_evolution.schemas import Suggestion, SuggestionEnvelope
from codex_self_evolution.storage import atomic_write_json, atomic_write_text, ensure_runtime_dirs


def _make_envelope(cwd: str) -> SuggestionEnvelope:
    return SuggestionEnvelope(
        schema_version=1,
        suggestion_id="sug-1",
        idempotency_key="idem-1",
        thread_id="thread-1",
        cwd=cwd,
        repo_fingerprint="fp-1",
        reviewer_timestamp="2026-04-20T00:00:00Z",
        suggestions=[Suggestion(family="memory_updates", summary="s", details={"content": "c"})],
        source_authority=[],
    )


def test_build_compile_context_with_empty_state_returns_empty_existing_assets(tmp_path):
    paths = build_paths(repo_root=tmp_path / "repo", state_dir=tmp_path / "state")
    ensure_runtime_dirs(paths)
    envelope = _make_envelope(str(paths.repo_root))

    context = build_compile_context(paths, [envelope])

    assert context["cwd"] == str(paths.repo_root)
    assert context["repo_fingerprint"] == "fp-1"
    assert context["existing_user_memory"] == ""
    assert context["existing_global_memory"] == ""
    assert context["existing_memory_index"] == {"user": [], "global": []}
    assert context["existing_recall_records"] == []
    assert context["existing_recall_markdown"] == ""
    assert context["memory_paths"]["user"].endswith("memory/USER.md")
    assert context["recall_paths"]["index"].endswith("recall/index.json")


def test_build_compile_context_reads_existing_memory_and_recall(tmp_path):
    paths = build_paths(repo_root=tmp_path / "repo", state_dir=tmp_path / "state")
    ensure_runtime_dirs(paths)

    atomic_write_text(paths.memory_dir / "USER.md", "# USER\n\nuser text\n")
    atomic_write_text(paths.memory_dir / "MEMORY.md", "# MEMORY\n\nglobal text\n")
    atomic_write_json(
        paths.memory_dir / "memory.json",
        {
            "user": [{"summary": "u1", "content": "user entry"}],
            "global": [{"summary": "g1", "content": "global entry"}],
        },
    )
    atomic_write_json(
        paths.recall_dir / "index.json",
        {
            "records": [
                {
                    "id": "r1",
                    "summary": "old recall",
                    "content": "old content",
                    "source_paths": ["x"],
                    "repo_fingerprint": "fp",
                    "cwd": "/tmp",
                }
            ]
        },
    )
    atomic_write_text(paths.recall_dir / "compiled.md", "# Compiled Recall\n\n## old recall\n")

    envelope = _make_envelope(str(paths.repo_root))
    context = build_compile_context(paths, [envelope])

    assert "user text" in context["existing_user_memory"]
    assert "global text" in context["existing_global_memory"]
    assert context["existing_memory_index"]["user"][0]["summary"] == "u1"
    assert context["existing_memory_index"]["global"][0]["content"] == "global entry"
    assert context["existing_recall_records"][0]["id"] == "r1"
    assert "old recall" in context["existing_recall_markdown"]


def test_build_compile_context_tolerates_corrupt_index_files(tmp_path):
    paths = build_paths(repo_root=tmp_path / "repo", state_dir=tmp_path / "state")
    ensure_runtime_dirs(paths)

    (paths.memory_dir / "memory.json").write_text("not json", encoding="utf-8")
    (paths.recall_dir / "index.json").write_text("also not json", encoding="utf-8")

    envelope = _make_envelope(str(paths.repo_root))
    context = build_compile_context(paths, [envelope])

    assert context["existing_memory_index"] == {"user": [], "global": []}
    assert context["existing_recall_records"] == []


def test_build_compile_context_falls_back_to_repo_root_when_batch_empty(tmp_path):
    paths = build_paths(repo_root=tmp_path / "repo", state_dir=tmp_path / "state")
    ensure_runtime_dirs(paths)

    context = build_compile_context(paths, [])

    assert context["cwd"] == str(paths.repo_root)
    assert context["repo_fingerprint"] == ""
    assert context["existing_manifest"] == []

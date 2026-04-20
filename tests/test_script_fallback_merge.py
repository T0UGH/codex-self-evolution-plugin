"""Regression: script backend must not wipe existing memory / recall when a
follow-up compile run has an empty or disjoint batch."""

import json

from codex_self_evolution.compiler.engine import run_compile
from codex_self_evolution.hooks.stop_review import stop_review


def _write_stop_payload(path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_second_compile_preserves_existing_memory_and_recall(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"

    first_payload_path = tmp_path / "stop_1.json"
    _write_stop_payload(
        first_payload_path,
        {
            "thread_id": "thread-initial",
            "turn_id": "turn-1",
            "cwd": str(repo),
            "transcript": "seeded memory and recall",
            "thread_read_output": "seed",
            "reviewer_provider": "dummy",
            "provider_stub_response": {
                "memory_updates": [
                    {"summary": "User pref", "details": {"content": "Prefer concise", "scope": "user"}},
                    {"summary": "Repo fact", "details": {"content": "Run focused pytest", "scope": "global"}},
                ],
                "recall_candidate": [
                    {"summary": "Pytest hint", "details": {"content": "Run focused pytest", "source_paths": ["tests/x.py"]}}
                ],
                "skill_action": [],
            },
        },
    )
    stop_review(hook_payload=first_payload_path, state_dir=state)
    first = run_compile(repo_root=repo, state_dir=state, backend="script")
    assert first["status"] == "success"

    user_md_first = (state / "memory" / "USER.md").read_text(encoding="utf-8")
    global_md_first = (state / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    recall_index_first = json.loads((state / "recall" / "index.json").read_text(encoding="utf-8"))

    assert "Prefer concise" in user_md_first
    assert "Run focused pytest" in global_md_first
    assert any(item["content"] == "Run focused pytest" for item in recall_index_first["records"])

    # Second turn: reviewer emits a brand-new, unrelated entry. The old memory
    # and recall must still be on disk afterwards.
    second_payload_path = tmp_path / "stop_2.json"
    _write_stop_payload(
        second_payload_path,
        {
            "thread_id": "thread-second",
            "turn_id": "turn-2",
            "cwd": str(repo),
            "transcript": "added unrelated fact",
            "thread_read_output": "seed2",
            "reviewer_provider": "dummy",
            "provider_stub_response": {
                "memory_updates": [
                    {"summary": "New fact", "details": {"content": "Avoid force push", "scope": "global"}}
                ],
                "recall_candidate": [
                    {"summary": "Force push", "details": {"content": "Avoid force push", "source_paths": ["docs/git.md"]}}
                ],
                "skill_action": [],
            },
        },
    )
    stop_review(hook_payload=second_payload_path, state_dir=state)
    second = run_compile(repo_root=repo, state_dir=state, backend="script")
    assert second["status"] == "success"

    user_md_second = (state / "memory" / "USER.md").read_text(encoding="utf-8")
    global_md_second = (state / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    recall_index_second = json.loads((state / "recall" / "index.json").read_text(encoding="utf-8"))

    # Old entries still there.
    assert "Prefer concise" in user_md_second
    assert "Run focused pytest" in global_md_second
    assert any(item["content"] == "Run focused pytest" for item in recall_index_second["records"])
    # New entries appended.
    assert "Avoid force push" in global_md_second
    assert any(item["content"] == "Avoid force push" for item in recall_index_second["records"])


def test_compile_with_disjoint_batch_does_not_drop_prior_recall(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"

    seed_path = tmp_path / "stop_seed.json"
    _write_stop_payload(
        seed_path,
        {
            "thread_id": "thread-seed",
            "turn_id": "turn-seed",
            "cwd": str(repo),
            "transcript": "seed",
            "thread_read_output": "seed",
            "reviewer_provider": "dummy",
            "provider_stub_response": {
                "memory_updates": [],
                "recall_candidate": [
                    {"summary": "stable recall", "details": {"content": "stable content", "source_paths": ["a"]}}
                ],
                "skill_action": [],
            },
        },
    )
    stop_review(hook_payload=seed_path, state_dir=state)
    run_compile(repo_root=repo, state_dir=state, backend="script")

    # Second turn only adds memory, no recall candidates. Prior recall must stay.
    follow_up = tmp_path / "stop_followup.json"
    _write_stop_payload(
        follow_up,
        {
            "thread_id": "thread-follow",
            "turn_id": "turn-follow",
            "cwd": str(repo),
            "transcript": "followup",
            "thread_read_output": "seed",
            "reviewer_provider": "dummy",
            "provider_stub_response": {
                "memory_updates": [
                    {"summary": "Memory only", "details": {"content": "fresh memory only", "scope": "global"}}
                ],
                "recall_candidate": [],
                "skill_action": [],
            },
        },
    )
    stop_review(hook_payload=follow_up, state_dir=state)
    run_compile(repo_root=repo, state_dir=state, backend="script")

    index = json.loads((state / "recall" / "index.json").read_text(encoding="utf-8"))
    assert any(item["content"] == "stable content" for item in index["records"])

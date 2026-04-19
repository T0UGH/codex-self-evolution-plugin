import json

from codex_self_evolution.config import build_paths
from codex_self_evolution.schemas import SuggestionEnvelope
from codex_self_evolution.storage import append_pending_suggestion, claim_suggestions, compute_stable_id, finalize_suggestion, repo_fingerprint



def _envelope(repo):
    return SuggestionEnvelope(
        schema_version=1,
        suggestion_id="suggestion-1",
        idempotency_key=compute_stable_id("same"),
        thread_id="thread-1",
        cwd=str(repo),
        repo_fingerprint=repo_fingerprint(repo),
        reviewer_timestamp="2026-01-01T00:00:00Z",
        suggestions=[],
        source_authority=["hook_payload"],
    )



def test_suggestion_store_tracks_processing_and_done_states(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    paths = build_paths(repo_root=repo, state_dir=tmp_path / "state")
    pending_path = append_pending_suggestion(paths, _envelope(repo))
    claimed = claim_suggestions(paths, batch_size=10)
    assert claimed[0][0].parent.name == "processing"
    done_path = finalize_suggestion(paths, claimed[0][0], claimed[0][1], "done")
    assert done_path.parent.name == "done"
    stored = json.loads(done_path.read_text(encoding="utf-8"))
    assert stored["state"] == "done"



def test_append_pending_suggestion_is_idempotent(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    paths = build_paths(repo_root=repo, state_dir=tmp_path / "state")
    first = append_pending_suggestion(paths, _envelope(repo))
    second = append_pending_suggestion(paths, _envelope(repo))
    assert first == second

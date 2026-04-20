from __future__ import annotations

import json
from pathlib import Path

from ..config import build_paths
from ..review.runner import run_reviewer
from ..review.snapshot import build_review_snapshot
from ..schemas import SuggestionEnvelope
from ..storage import append_pending_suggestion, compute_stable_id, repo_fingerprint, utc_now


def _load_payload(path: str | Path) -> dict:
    payload_path = Path(path)
    return json.loads(payload_path.read_text(encoding="utf-8"))


def stop_review(hook_payload: str | Path, state_dir: str | Path | None = None) -> dict:
    payload = _load_payload(hook_payload)
    cwd = payload.get("cwd") or "."
    paths = build_paths(repo_root=cwd, state_dir=state_dir)
    snapshot, snapshot_path = build_review_snapshot(payload, paths)
    reviewer_output, provider_result, skipped_suggestions = run_reviewer(snapshot)
    timestamp = utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")
    idempotency_key = compute_stable_id(
        json.dumps(
            {
                "thread_id": payload.get("thread_id", "unknown-thread"),
                "turn_id": payload.get("turn_id", ""),
                "snapshot_path": str(snapshot_path),
            },
            sort_keys=True,
        )
    )
    envelope = SuggestionEnvelope(
        schema_version=1,
        suggestion_id=compute_stable_id(f"{idempotency_key}-{timestamp}"),
        idempotency_key=idempotency_key,
        thread_id=payload.get("thread_id", "unknown-thread"),
        cwd=str(Path(cwd).resolve()),
        repo_fingerprint=repo_fingerprint(Path(cwd).resolve()),
        reviewer_timestamp=timestamp,
        suggestions=reviewer_output.all_suggestions(),
        source_authority=snapshot["source_authority"],
        review_snapshot_path=str(snapshot_path),
        transition_log=[{"at": timestamp, "from": "", "to": "pending", "reason": provider_result.provider}],
    )
    destination = append_pending_suggestion(paths, envelope)
    return {
        "hook": "Stop",
        "pending_suggestion_path": str(destination),
        "review_snapshot_path": str(snapshot_path),
        "reviewer_provider": provider_result.provider,
        "suggestion_count": len(envelope.suggestions),
        "skipped_suggestion_count": len(skipped_suggestions),
        "skipped_suggestions": skipped_suggestions,
    }

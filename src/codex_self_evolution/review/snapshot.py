from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import Paths
from ..managed_skills.manifest import summarize_managed_skills
from ..storage import atomic_write_json, load_memory_files, utc_now


SOURCE_AUTHORITY = [
    "thread_read_output",
    "thread_read_path",
    "transcript",
    "transcript_path",
    "memory_files",
    "managed_skills_manifest",
    "hook_payload",
]


def _load_text_value(payload: dict[str, Any], inline_key: str, path_key: str) -> tuple[str, str | None]:
    inline_value = payload.get(inline_key)
    if isinstance(inline_value, str) and inline_value.strip():
        return inline_value, inline_key
    raw_path = payload.get(path_key)
    if isinstance(raw_path, str) and raw_path.strip():
        path = Path(raw_path)
        if path.exists():
            return path.read_text(encoding="utf-8"), path_key
    return "", None


def build_review_snapshot(payload: dict[str, Any], paths: Paths) -> tuple[dict[str, Any], Path]:
    memory_files = load_memory_files(paths)
    transcript, transcript_source = _load_text_value(payload, "transcript", "transcript_path")
    thread_output, thread_source = _load_text_value(payload, "thread_read_output", "thread_read_path")
    source_authority = [item for item in [thread_source, transcript_source, "memory_files", "managed_skills_manifest", "hook_payload"] if item]
    snapshot = {
        "context": {
            "session_id": payload.get("session_id", ""),
            "thread_id": payload.get("thread_id", "unknown-thread"),
            "turn_id": payload.get("turn_id", ""),
            "cwd": str(paths.repo_root),
            "repo_root": str(paths.repo_root),
            "state_dir": str(paths.state_dir),
            "triggered_at": payload.get("triggered_at") or utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        },
        "turn_snapshot": {
            "transcript": transcript,
            "thread_read_output": thread_output,
            "last_assistant_message": payload.get("last_assistant_message", ""),
            "hook_payload_excerpt": {key: value for key, value in payload.items() if key not in {"reviewer_output", "provider_stub_response"}},
        },
        "comparison_materials": {
            "current_user_md": memory_files["USER.md"],
            "current_memory_md": memory_files["MEMORY.md"],
            "managed_skills_summary": summarize_managed_skills(paths.skills_dir / "manifest.json"),
        },
        "source_authority": source_authority,
        "provider_stub_response": payload.get("provider_stub_response"),
        "reviewer_provider": payload.get("reviewer_provider", "dummy"),
    }
    snapshot_id = f"{snapshot['context']['thread_id']}-{compute_snapshot_digest(snapshot)}"
    destination = paths.review_snapshots_dir / f"{snapshot_id}.json"
    atomic_write_json(destination, snapshot)
    return snapshot, destination


def compute_snapshot_digest(snapshot: dict[str, Any]) -> str:
    serialized = json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
    return __import__("hashlib").sha1(serialized.encode("utf-8")).hexdigest()[:12]

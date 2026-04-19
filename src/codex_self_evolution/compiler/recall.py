from __future__ import annotations

import hashlib

from ..schemas import RecallRecord, Suggestion


def compile_recall(suggestions: list[Suggestion], repo_fingerprint: str, cwd: str, thread_id: str = "", turn_id: str = "") -> list[RecallRecord]:
    records: list[RecallRecord] = []
    seen: set[str] = set()
    for item in suggestions:
        if item.family != "recall_candidate":
            continue
        content = str(item.details.get("content", item.summary)).strip()
        if not content:
            continue
        key = hashlib.sha1(content.encode("utf-8")).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        records.append(
            RecallRecord(
                id=key[:12],
                summary=item.summary,
                content=content,
                source_paths=[str(path) for path in item.details.get("source_paths", [])],
                repo_fingerprint=str(item.details.get("repo_fingerprint", repo_fingerprint)),
                cwd=str(item.details.get("cwd", cwd)),
                thread_id=str(item.details.get("thread_id", thread_id)),
                turn_id=str(item.details.get("turn_id", turn_id)),
                source_updated_at=str(item.details.get("source_updated_at", "")),
            )
        )
    return records

from __future__ import annotations

import hashlib
from typing import Any

from ..schemas import RecallRecord, SchemaError, Suggestion


def _content_key(content: str) -> str:
    return hashlib.sha1(content.encode("utf-8")).hexdigest()


def compile_recall(
    suggestions: list[Suggestion],
    repo_fingerprint: str,
    cwd: str,
    thread_id: str = "",
    turn_id: str = "",
    *,
    existing_records: list[dict[str, Any]] | None = None,
) -> list[RecallRecord]:
    """Compile recall records, preserving existing records by default.

    existing_records is the list stored at ``recall/index.json``. When provided,
    existing entries are kept (parsed as :class:`RecallRecord`) and new
    suggestions are only appended when their content has not been seen before.
    Malformed existing entries are skipped so a corrupt index cannot break a
    compile run.
    """
    records: list[RecallRecord] = []
    seen: set[str] = set()

    for raw in existing_records or []:
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content", "")).strip()
        if not content:
            continue
        key = _content_key(content)
        if key in seen:
            continue
        try:
            record = RecallRecord.from_dict(raw)
        except SchemaError:
            continue
        seen.add(key)
        records.append(record)

    for item in suggestions:
        if item.family != "recall_candidate":
            continue
        content = str(item.details.get("content", item.summary)).strip()
        if not content:
            continue
        key = _content_key(content)
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

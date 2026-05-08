from __future__ import annotations

import hashlib
from typing import Any

from ..schemas import RecallRecord, SchemaError, Suggestion


FUTURE_TRIGGER_MARKERS = (
    "when ",
    "if ",
    "next time",
    "again",
    "future",
    "before ",
    "reuse",
    "run ",
    "check ",
    "verify ",
    "debug",
    "trace",
    "diagnostic",
    "boundary",
    "pitfall",
    "workflow",
    "convention",
    "risk",
    "触发",
    "下次",
    "以后",
    "再次",
    "排查",
    "检查",
    "验证",
    "边界",
    "约定",
    "风险",
)

PROCESS_STATE_MARKERS = (
    "mr ",
    "pr ",
    "merge request",
    "comment",
    "resolved",
    "pushed",
    "commit",
    "branch",
    "yesterday",
    "today",
    "tomorrow",
    "review round",
    "评论",
    "已解决",
    "已修",
    "已经",
    "昨天",
    "今天",
    "分支",
    "提交",
)


def _content_key(content: str) -> str:
    return hashlib.sha1(content.encode("utf-8")).hexdigest()


def _extract_content(details: dict[str, Any], fallback_summary: str) -> str:
    """Mirror of compiler.memory._extract_content: accept common alias keys
    (note / text / body) before falling back to the summary, so a reviewer that
    renamed ``details.content`` doesn't silently dedupe to summary text."""
    for key in ("content", "note", "text", "body"):
        value = details.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(fallback_summary).strip()


def _has_recall_reuse_signal(item: Suggestion, content: str) -> bool:
    if str(item.details.get("reuse_trigger") or "").strip():
        return True
    source_paths = item.details.get("source_paths") or []
    if not isinstance(source_paths, list) or not any(str(path).strip() for path in source_paths):
        return False
    text = f"{item.summary}\n{content}".lower()
    if any(marker in text for marker in PROCESS_STATE_MARKERS) and not any(
        marker in text for marker in FUTURE_TRIGGER_MARKERS
    ):
        return False
    return any(marker in text for marker in FUTURE_TRIGGER_MARKERS)


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
    records, _ = compile_recall_with_discarded(
        suggestions,
        repo_fingerprint,
        cwd,
        thread_id,
        turn_id,
        existing_records=existing_records,
    )
    return records


def compile_recall_with_discarded(
    suggestions: list[Suggestion],
    repo_fingerprint: str,
    cwd: str,
    thread_id: str = "",
    turn_id: str = "",
    *,
    existing_records: list[dict[str, Any]] | None = None,
) -> tuple[list[RecallRecord], list[dict[str, Any]]]:
    records: list[RecallRecord] = []
    discarded: list[dict[str, Any]] = []
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
        content = _extract_content(item.details, item.summary)
        if not content:
            continue
        key = _content_key(content)
        if key in seen:
            continue
        if not _has_recall_reuse_signal(item, content):
            discarded.append(
                {
                    "family": "recall_candidate",
                    "summary": item.summary,
                    "reason": "missing_reuse_trigger",
                }
            )
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
    return records, discarded

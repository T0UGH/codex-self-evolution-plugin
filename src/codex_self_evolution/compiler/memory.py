from __future__ import annotations

import logging
from typing import Any

from ..schemas import Suggestion


logger = logging.getLogger(__name__)


def _normalize_scope(raw_scope: object) -> str:
    scope = str(raw_scope or "").strip().lower()
    if scope in {"user", "global"}:
        return scope
    return "global"


def _extract_content(details: dict[str, Any], fallback_summary: str) -> str:
    """Prefer explicit content; accept a few common alias keys reviewers drift
    into (note/text/body) before falling back to the summary. Empty/whitespace
    values are treated as missing."""
    for key in ("content", "note", "text", "body"):
        value = details.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(fallback_summary).strip()


def _normalize_existing_entry(scope: str, item: dict[str, Any]) -> dict[str, Any] | None:
    content = str(item.get("content", "")).strip()
    if not content:
        return None
    try:
        confidence = float(item.get("confidence", 1.0))
    except (TypeError, ValueError):
        confidence = 1.0
    source_paths = item.get("source_paths", []) or []
    provenance = item.get("provenance", []) or []
    return {
        "scope": scope,
        "summary": str(item.get("summary", "")),
        "content": content,
        "source_paths": list(source_paths),
        "confidence": confidence,
        "provenance": list(provenance),
    }


def _find_by_old_summary(entries: list[dict[str, Any]], old_summary: str) -> int | None:
    """Locate the single entry whose `summary` contains ``old_summary``.

    Returns the index, or ``None`` if unmatched or ambiguous. Ambiguity means
    multiple distinct summaries matched — if every match shares the exact same
    summary text we treat it as collapsible duplicates and operate on the first.
    The reviewer is expected to pick `old_summary` unique enough to avoid this;
    we fail closed rather than guess when the match is ambiguous.
    """
    needle = old_summary.strip()
    if not needle:
        return None
    matches = [(i, entry) for i, entry in enumerate(entries) if needle in entry["summary"]]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0][0]
    unique_summaries = {entry["summary"] for _, entry in matches}
    if len(unique_summaries) == 1:
        return matches[0][0]
    return None


def compile_memory(
    suggestions: list[Suggestion],
    *,
    existing_index: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, list[dict]]:
    """Compile memory records using atomic add/replace/remove actions.

    For each ``memory_updates`` suggestion the reviewer can pick one of three
    actions via ``details.action`` (default ``"add"`` when omitted):

    - ``add``: append when the (scope, content) pair is new. Exact duplicates
      are still rejected at this layer — the reviewer is responsible for
      choosing ``replace`` when updating a near-duplicate.
    - ``replace``: find an existing entry whose ``summary`` contains
      ``details.old_summary`` and overwrite its content/summary/source_paths.
      Ambiguous matches are skipped with a warning rather than guessing.
    - ``remove``: drop the entry identified by ``details.old_summary``.

    Existing entries from ``memory.json`` are preserved as-is unless a
    suggestion explicitly targets them, so a bad suggestion batch cannot wipe
    stable memory.
    """
    user_out: list[dict[str, Any]] = []
    global_out: list[dict[str, Any]] = []

    if existing_index:
        seen_keys: set[tuple[str, str]] = set()
        for scope in ("user", "global"):
            for raw in existing_index.get(scope, []) or []:
                if not isinstance(raw, dict):
                    continue
                normalized = _normalize_existing_entry(scope, raw)
                if normalized is None:
                    continue
                key = (scope, normalized["content"])
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                (user_out if scope == "user" else global_out).append(normalized)

    for item in suggestions:
        if item.family != "memory_updates":
            continue
        scope = _normalize_scope(item.details.get("scope"))
        bucket = user_out if scope == "user" else global_out
        action = str(item.details.get("action") or "add").strip().lower()

        if action == "remove":
            old_summary = str(item.details.get("old_summary", "")).strip()
            idx = _find_by_old_summary(bucket, old_summary)
            if idx is None:
                logger.warning(
                    "memory remove skipped: no unique match for old_summary=%r in scope=%s",
                    old_summary,
                    scope,
                )
                continue
            bucket.pop(idx)
            continue

        content = _extract_content(item.details, item.summary)
        if not content:
            continue

        if action == "replace":
            old_summary = str(item.details.get("old_summary", "")).strip()
            idx = _find_by_old_summary(bucket, old_summary)
            if idx is None:
                logger.warning(
                    "memory replace skipped: no unique match for old_summary=%r in scope=%s",
                    old_summary,
                    scope,
                )
                continue
            bucket[idx] = {
                "scope": scope,
                "summary": item.summary,
                "content": content,
                "source_paths": list(item.details.get("source_paths", [])),
                "confidence": item.confidence,
                "provenance": bucket[idx].get("provenance", []),
            }
            continue

        # action == "add" (default). Reject exact (scope, content) duplicates,
        # but bump the existing entry's confidence to the max of old/new so
        # repeat signals still strengthen an entry's weight.
        existing_match = next(
            (entry for entry in bucket if entry["content"] == content),
            None,
        )
        if existing_match is not None:
            if item.confidence > existing_match["confidence"]:
                existing_match["confidence"] = item.confidence
            continue
        bucket.append({
            "scope": scope,
            "summary": item.summary,
            "content": content,
            "source_paths": list(item.details.get("source_paths", [])),
            "confidence": item.confidence,
            "provenance": [],
        })

    return {"user": user_out, "global": global_out}

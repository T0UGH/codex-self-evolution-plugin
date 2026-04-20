from __future__ import annotations

from typing import Any

from ..schemas import Suggestion


def _normalize_scope(raw_scope: object) -> str:
    scope = str(raw_scope or "").strip().lower()
    if scope in {"user", "global"}:
        return scope
    return "global"


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


def compile_memory(
    suggestions: list[Suggestion],
    *,
    existing_index: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, list[dict]]:
    """Compile memory records, preserving existing entries by default.

    existing_index is the ``{"user": [...], "global": [...]}`` structure loaded
    from ``memory.json``. When provided, existing entries are kept as-is and new
    suggestions are only appended when they introduce a new (scope, content)
    pair. This avoids the previous destructive behaviour where a new batch
    silently overwrote stable memory.
    """
    seen_keys: set[tuple[str, str]] = set()
    user_out: list[dict[str, Any]] = []
    global_out: list[dict[str, Any]] = []

    if existing_index:
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

    new_merged: dict[tuple[str, str], dict[str, Any]] = {}
    for item in suggestions:
        if item.family != "memory_updates":
            continue
        content = str(item.details.get("content", item.summary)).strip()
        if not content:
            continue
        scope = _normalize_scope(item.details.get("scope"))
        key = (scope, content)
        if key not in new_merged:
            new_merged[key] = {
                "scope": scope,
                "summary": item.summary,
                "content": content,
                "source_paths": list(item.details.get("source_paths", [])),
                "confidence": item.confidence,
                "provenance": [],
            }
        else:
            new_merged[key]["confidence"] = max(new_merged[key]["confidence"], item.confidence)

    for key, entry in new_merged.items():
        if key in seen_keys:
            continue
        seen_keys.add(key)
        scope = key[0]
        (user_out if scope == "user" else global_out).append(entry)

    return {"user": user_out, "global": global_out}

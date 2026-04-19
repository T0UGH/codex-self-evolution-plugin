from __future__ import annotations

from ..schemas import Suggestion


def _normalize_scope(raw_scope: object) -> str:
    scope = str(raw_scope or "").strip().lower()
    if scope in {"user", "global"}:
        return scope
    return "global"


def compile_memory(suggestions: list[Suggestion]) -> dict[str, list[dict]]:
    merged: dict[tuple[str, str], dict] = {}
    for item in suggestions:
        if item.family != "memory_updates":
            continue
        content = str(item.details.get("content", item.summary)).strip()
        if not content:
            continue
        scope = _normalize_scope(item.details.get("scope"))
        key = (scope, content)
        if key not in merged:
            merged[key] = {
                "scope": scope,
                "summary": item.summary,
                "content": content,
                "source_paths": list(item.details.get("source_paths", [])),
                "confidence": item.confidence,
                "provenance": [],
            }
        merged[key]["confidence"] = max(merged[key]["confidence"], item.confidence)
    records = list(merged.values())
    return {
        "user": [item for item in records if item["scope"] == "user"],
        "global": [item for item in records if item["scope"] == "global"],
    }

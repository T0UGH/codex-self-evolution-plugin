from __future__ import annotations

from pathlib import Path
from typing import Any

from .search import search_recall


def evaluate_recall_trigger(query: str, policy: str | None = None, *, explicit: bool = False) -> dict[str, Any]:
    terms = [term for term in query.split() if term.strip()]
    reasons: list[str] = []
    if explicit:
        reasons.append("explicit")
    if len(terms) >= 2:
        reasons.append("multi_term_query")
    lowered = query.lower()
    for marker in ("remember", "previous", "again", "recall", "before"):
        if marker in lowered:
            reasons.append(f"marker:{marker}")
            break
    triggered = bool(reasons)
    return {"triggered": triggered, "reasons": reasons, "policy": policy or ""}


def build_focused_recall(query: str, cwd: str | Path, state_dir: str | Path | None = None, top_k: int = 3) -> dict[str, Any]:
    results = search_recall(query=query, cwd=cwd, state_dir=state_dir)[:top_k]
    bullets = [f"- {item['summary']}: {item['content']}" for item in results]
    return {
        "query": query,
        "count": len(results),
        "results": results,
        "focused_recall": "\n".join(bullets),
    }

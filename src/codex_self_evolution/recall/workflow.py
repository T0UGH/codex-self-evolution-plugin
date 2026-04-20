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


def evaluate_session_recall(
    query: str,
    cwd: str | Path,
    state_dir: str | Path | None = None,
    *,
    session_payload: dict[str, Any] | None = None,
    explicit: bool = False,
    top_k: int = 3,
) -> dict[str, Any]:
    session_payload = session_payload or {}
    recall_payload = session_payload.get("recall", {}) if isinstance(session_payload, dict) else {}
    policy = recall_payload.get("policy") if isinstance(recall_payload, dict) else ""
    skill = recall_payload.get("skill") if isinstance(recall_payload, dict) else {}
    trigger = evaluate_recall_trigger(query=query, policy=policy, explicit=explicit)
    if not trigger["triggered"]:
        return {
            **trigger,
            "skill_id": skill.get("skill_id") if isinstance(skill, dict) else None,
            "skill_content": skill.get("content") if isinstance(skill, dict) else "",
            "query": query,
            "count": 0,
            "results": [],
            "focused_recall": "",
        }
    focused = build_focused_recall(query=query, cwd=cwd, state_dir=state_dir, top_k=top_k)
    return {
        **trigger,
        "skill_id": skill.get("skill_id") if isinstance(skill, dict) else None,
        "skill_content": skill.get("content") if isinstance(skill, dict) else "",
        **focused,
    }

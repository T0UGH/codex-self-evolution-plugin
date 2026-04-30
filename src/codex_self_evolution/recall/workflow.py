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


def render_focused_recall_markdown(payload: dict[str, Any]) -> str:
    """Render recall output for direct model consumption.

    JSON remains the machine-readable interface. This markdown form is the
    short command's default because Codex can read it directly and continue
    when recall has no match.
    """
    query = str(payload.get("query") or "").strip()
    triggered = payload.get("triggered")
    count = int(payload.get("count") or 0)
    status = "matched" if count else "no_match"
    if triggered is False:
        status = "not_triggered"
    if payload.get("error"):
        status = "error"

    lines = ["## Focused Recall", "", f"Status: {status}"]
    if query:
        lines.append(f"Query: {query}")
    lines.append(f"Results: {count}")

    if status == "not_triggered":
        lines.extend([
            "",
            "Recall was not triggered by the current policy. Continue with the current repo and conversation context.",
        ])
        return "\n".join(lines).rstrip() + "\n"

    if status == "error":
        lines.extend([
            "",
            f"Recall failed softly: {payload.get('error')}",
            "Continue with the current repo and conversation context. Do not invent prior context.",
        ])
        return "\n".join(lines).rstrip() + "\n"

    results = payload.get("results") or []
    if not results:
        lines.extend([
            "",
            "No matching recall was found. Continue with the current repo and conversation context. Do not invent prior context.",
        ])
        return "\n".join(lines).rstrip() + "\n"

    for index, item in enumerate(results, start=1):
        summary = str(item.get("summary") or item.get("id") or f"Recall {index}").strip()
        content = str(item.get("content") or "").strip()
        source_paths = item.get("source_paths") or []
        provenance = ", ".join(str(path) for path in source_paths if str(path).strip())
        lines.extend(["", f"### {index}. {summary}", "", content])
        if provenance:
            lines.extend(["", f"Provenance: {provenance}"])
    return "\n".join(lines).rstrip() + "\n"


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

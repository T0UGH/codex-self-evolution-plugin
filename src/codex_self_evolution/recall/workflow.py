from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config_file import load_config
from .search import search_recall
from ..session_recall.archive import default_db_path
from ..session_recall.render import budget_payload, render_markdown
from ..session_recall.repo import collect_repo_metadata
from ..session_recall.store import SessionRecallStore


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


def build_focused_recall(
    query: str,
    cwd: str | Path,
    state_dir: str | Path | None = None,
    top_k: int = 3,
    *,
    global_scope: bool = False,
    recent: bool = False,
    before: int = 3,
    after: int = 5,
    budget_chars: int = 12000,
    message_chars: int = 1200,
    tool_message_chars: int = 600,
    current_session_id: str = "",
) -> dict[str, Any]:
    config = load_config().config
    if config.session_recall.enabled:
        db_path = default_db_path(state_dir=state_dir)
        if db_path.exists():
            meta = collect_repo_metadata(cwd)
            store = SessionRecallStore(db_path)
            try:
                if recent:
                    session_results = store.recent(
                        repo_fingerprint=meta["repo_fingerprint"],
                        global_scope=global_scope,
                        limit=top_k,
                    )
                    payload = budget_payload(
                        query=query,
                        scope="global" if global_scope else "repo",
                        results=session_results,
                        budget_chars=budget_chars,
                        message_chars=message_chars,
                        tool_message_chars=tool_message_chars,
                        recent=True,
                    )
                    payload["recent"] = True
                    return payload
                session_results = store.search(
                    query=query,
                    repo_fingerprint=meta["repo_fingerprint"],
                    global_scope=global_scope,
                    limit=top_k,
                    before=before,
                    after=after,
                    current_session_id=current_session_id,
                )
                if session_results:
                    return budget_payload(
                        query=query,
                        scope="global" if global_scope else "repo",
                        results=session_results,
                        budget_chars=budget_chars,
                        message_chars=message_chars,
                        tool_message_chars=tool_message_chars,
                    )
            finally:
                store.close()

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
    if payload.get("budget") is not None or any(
        isinstance(item, dict) and "messages" in item
        for item in (payload.get("results") or [])
    ):
        return render_markdown(payload)

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

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config_file import load_config
from .archive import default_db_path
from .render import budget_payload, render_markdown
from .repo import collect_repo_metadata
from .store import SessionRecallStore


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
    all_terms: bool = False,
    windows_per_session: int = 2,
    include_background: bool = False,
) -> dict[str, Any]:
    """Build model-readable recall from the session_recall SQLite/FTS store only."""
    scope = "global" if global_scope else "repo"
    config = load_config().config
    if not config.session_recall.enabled:
        return _empty_payload(query=query, scope=scope)

    db_path = default_db_path(state_dir=state_dir)
    if not db_path.exists():
        return _empty_payload(query=query, scope=scope)

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
                scope=scope,
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
            all_terms=all_terms,
            windows_per_session=windows_per_session,
            include_background=include_background,
        )
        return budget_payload(
            query=query,
            scope=scope,
            results=session_results,
            budget_chars=budget_chars,
            message_chars=message_chars,
            tool_message_chars=tool_message_chars,
        )
    finally:
        store.close()


def render_focused_recall_markdown(payload: dict[str, Any]) -> str:
    """Render session recall output for direct model consumption."""
    return render_markdown(payload)


def _empty_payload(*, query: str, scope: str) -> dict[str, Any]:
    """Return a no-match payload matching session recall renderer expectations."""
    return {
        "query": query,
        "scope": scope,
        "count": 0,
        "results": [],
        "focused_recall": "",
        "status": "no_match",
        "budget": {
            "budget_chars": 0,
            "used_chars": 0,
            "truncated": False,
            "truncation_reason": "",
        },
    }

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import PACKAGE_ROOT, build_paths
from ..storage import ensure_runtime_dirs, load_stable_memory, repo_fingerprint


def session_start(cwd: str | Path | None = None, state_dir: str | Path | None = None) -> dict:
    paths = build_paths(repo_root=cwd, state_dir=state_dir)
    ensure_runtime_dirs(paths)
    policy = (PACKAGE_ROOT / "session_recall" / "policy.md").read_text(encoding="utf-8")
    memory_selection = load_stable_memory(paths)
    memory_text = memory_selection.content
    memory_heading = "## memory_summary.md" if memory_selection.source == "memory_summary.md" else "## MEMORY.md"
    combined_prefix = "\n\n".join(
        section
        for section in [
            "# Stable Background",
            memory_heading + "\n" + (memory_text or "_No entries yet._\n"),
        ]
        if section
    )
    return {
        "hook": "SessionStart",
        "cwd": str(paths.repo_root),
        "repo_fingerprint": repo_fingerprint(paths.repo_root),
        "state_dir": str(paths.state_dir),
        "stable_background": {
            "current_memory_md": memory_text,
            "memory_path": str(paths.memory_dir / "MEMORY.md"),
            "memory_source": memory_selection.source,
            "memory_source_path": str(memory_selection.source_path),
            "memory_summary_path": str(memory_selection.summary_path),
            "memory_summary_meta_path": str(memory_selection.summary_meta_path),
            "memory_fallback_used": memory_selection.fallback_used,
            "memory_fallback_reason": memory_selection.fallback_reason,
            "memory_refs_dir": str(paths.memory_refs_dir),
            "legacy_user_md_ignored": (paths.memory_dir / "USER.md").exists(),
            "combined_prefix": combined_prefix,
        },
        "recall": {
            "policy": policy,
            "skill": {
                "skill_id": "csep-session-recall",
                "title": "CSEP Session Recall",
                "provided_by": "plugin",
            },
            "trigger_defaults": {"same_repo_first": True, "same_cwd_first": True, "auto_trigger": True},
        },
        "runtime": {
            "session_context": {
                "thread_start_injected": True,
                "repo_root": str(paths.repo_root),
                "state_dir": str(paths.state_dir),
            },
        },
    }


def format_session_start_for_codex(session_result: dict[str, Any]) -> dict[str, Any]:
    """Wrap a ``session_start()`` result into Codex SessionStart hook protocol.

    Codex reads ``~/.codex/hooks.json`` SessionStart entries; when the hook
    emits JSON of the form::

        {"hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "..."}}

    the ``additionalContext`` string is injected into the session as a
    ``DeveloperInstructions`` message. Verified against codex-cli 0.122.0
    (2026-04-20 release). Older project notes claimed this field was
    "parsed but not supported"; that caveat is stale.

    Per-repo memory stays per-repo because ``cwd`` routes ``session_start()``
    to ``~/.codex-self-evolution/projects/<mangled-cwd>/`` automatically via
    ``build_paths``; Codex sees only context relevant to this session.

    ``additionalContext`` = ``stable_background.combined_prefix`` (MEMORY.md)
    + a short recall skill pointer. Empty MD files yield
    a short "No entries yet" stub, not a crash — the hook is safe to install
    on a fresh machine before any reflection job has written memory.
    """
    prefix = (session_result.get("stable_background") or {}).get("combined_prefix", "").strip()
    policy = (session_result.get("recall") or {}).get("policy", "").strip()
    pieces: list[str] = []
    if prefix:
        pieces.append(prefix)
    if policy:
        # Tag with a header so the model can distinguish "stable background I
        # already know" from "here's how to pull more on demand".
        pieces.append("## Recall Policy\n\n" + policy)
    additional_context = "\n\n".join(pieces)
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": additional_context,
        }
    }

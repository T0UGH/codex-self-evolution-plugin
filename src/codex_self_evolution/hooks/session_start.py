from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import PACKAGE_ROOT, build_paths
from ..storage import ensure_runtime_dirs, load_memory_files, repo_fingerprint


def session_start(cwd: str | Path | None = None, state_dir: str | Path | None = None) -> dict:
    paths = build_paths(repo_root=cwd, state_dir=state_dir)
    ensure_runtime_dirs(paths)
    policy = (PACKAGE_ROOT / "recall" / "policy.md").read_text(encoding="utf-8")
    session_recall_skill = (PACKAGE_ROOT / "recall" / "session_recall.md").read_text(encoding="utf-8")
    memory_files = load_memory_files(paths)
    combined_prefix = "\n\n".join(
        section
        for section in [
            "# Stable Background",
            "## USER.md\n" + (memory_files["USER.md"] or "_No entries yet._\n"),
            "## MEMORY.md\n" + (memory_files["MEMORY.md"] or "_No entries yet._\n"),
            "## Recall Contract\n\n" + session_recall_skill,
        ]
        if section
    )
    return {
        "hook": "SessionStart",
        "cwd": str(paths.repo_root),
        "repo_fingerprint": repo_fingerprint(paths.repo_root),
        "state_dir": str(paths.state_dir),
        "stable_background": {
            "current_user_md": memory_files["USER.md"],
            "current_memory_md": memory_files["MEMORY.md"],
            "combined_prefix": combined_prefix,
        },
        "recall": {
            "policy": policy,
            "skill": {
                "skill_id": "session_recall",
                "title": "Session Recall",
                "content": session_recall_skill,
            },
            "trigger_defaults": {"same_repo_first": True, "same_cwd_first": True, "auto_trigger": True},
        },
        "runtime": {
            "managed_skills_manifest_path": str(paths.skills_dir / "manifest.json"),
            "review_snapshots_dir": str(paths.review_snapshots_dir),
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
    (2026-04-20 release); see ``docs/todo.md`` 2026-04-21 P0-0 entry for
    the research trail and gotchas. Docs previously claimed this field was
    "parsed but not supported" — that caveat is stale.

    Per-repo memory stays per-repo because ``cwd`` routes ``session_start()``
    to ``~/.codex-self-evolution/projects/<mangled-cwd>/`` automatically via
    ``build_paths``; Codex sees only context relevant to this session.

    ``additionalContext`` = ``stable_background.combined_prefix`` (USER.md +
    MEMORY.md + session_recall skill) + recall policy. Empty MD files yield
    a short "No entries yet" stub, not a crash — the hook is safe to install
    on a fresh machine before any reviewer has run.
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

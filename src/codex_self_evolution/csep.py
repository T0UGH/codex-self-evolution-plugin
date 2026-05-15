from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from . import cli as full_cli
from .logging_setup import configure as configure_logging, get_logger
from .session_recall.workflow import build_focused_recall, render_focused_recall_markdown
from .session_recall.archive import archive_from_hook_payload, archive_transcript, backfill_sessions, default_db_path

_MAIN_CLI_COMMANDS = {
    "session-start",
    "session-stop",
    "session-reflect",
    "status",
    "config",
    "migrate-worktrees",
}


def build_parser() -> argparse.ArgumentParser:
    """Build the short csep command surface."""
    parser = argparse.ArgumentParser(
        prog="csep",
        description="Short runtime commands for codex-self-evolution.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_main_runtime_commands(subparsers)

    recall = subparsers.add_parser(
        "recall",
        help="Run focused recall for a model-generated query. Defaults to Markdown output.",
    )
    recall.add_argument("query", nargs="*", help="Focused recall query. Quote it when it contains spaces.")
    recall.add_argument("--cwd", default=None, help="Repo/current working directory. Defaults to $PWD.")
    recall.add_argument("--state-dir", default=None)
    recall.add_argument("--global", dest="global_scope", action="store_true")
    recall.add_argument("--recent", action="store_true")
    recall.add_argument("--limit", "--top-k", dest="limit", type=int, default=3)
    recall.add_argument("--before", type=int, default=3)
    recall.add_argument("--after", type=int, default=5)
    recall.add_argument("--budget-chars", type=int, default=12000)
    recall.add_argument("--message-chars", type=int, default=1200)
    recall.add_argument("--tool-message-chars", type=int, default=600)
    recall.add_argument("--current-session-id", default="")
    recall.add_argument("--format", choices=("markdown", "json"), default="markdown")

    archive = subparsers.add_parser("session-archive", help="Archive one Codex session transcript into the local recall store.")
    archive.add_argument("--from-hook-payload")
    archive.add_argument("--transcript-path")
    archive.add_argument("--cwd")
    archive.add_argument("--session-id")
    archive.add_argument("--state-dir")
    archive.add_argument("--cleanup-payload", action="store_true")

    ingest = subparsers.add_parser("session-ingest", help="Backfill Codex session transcripts into the local recall store.")
    ingest.add_argument("--backfill", action="store_true", required=True)
    ingest.add_argument("--root")
    ingest.add_argument("--cwd")
    ingest.add_argument("--state-dir")
    ingest.add_argument("--since-days", type=int)
    ingest.add_argument("--limit-files", type=int)
    return parser


def _add_main_runtime_commands(subparsers: argparse._SubParsersAction) -> None:
    """Register csep aliases for the retained codex-self-evolution runtime commands."""
    session_parser = subparsers.add_parser(
        "session-start",
        help="Codex SessionStart hook entrypoint.",
    )
    session_parser.add_argument("--cwd")
    session_parser.add_argument("--state-dir")
    session_parser.add_argument(
        "--from-stdin",
        action="store_true",
        help="Read a Codex SessionStart hook JSON payload from stdin.",
    )

    stop_parser = subparsers.add_parser(
        "session-stop",
        help="Codex Stop hook entrypoint.",
    )
    stop_parser.add_argument("--state-dir")
    stop_parser.add_argument(
        "--from-stdin",
        action="store_true",
        help="Read a Codex native Stop hook JSON payload from stdin.",
    )

    reflect_parser = subparsers.add_parser(
        "session-reflect",
        help="Run or inspect session reflection jobs.",
    )
    reflect_mode = reflect_parser.add_mutually_exclusive_group(required=True)
    reflect_mode.add_argument("--hook-payload")
    reflect_mode.add_argument("--job")
    reflect_mode.add_argument("--status", action="store_true")
    reflect_parser.add_argument("--home")

    status_parser = subparsers.add_parser(
        "status",
        help="Output a read-only diagnostic snapshot as JSON.",
    )
    status_parser.add_argument("--home")

    config_parser = subparsers.add_parser(
        "config",
        help="Inspect, initialise, or validate config.toml.",
    )
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)
    config_show = config_sub.add_parser("show", help="Print resolved config.")
    config_show.add_argument("--home")
    config_show.add_argument("--raw", action="store_true")
    config_init = config_sub.add_parser("init", help="Write a starter config.toml.")
    config_init.add_argument("--home")
    config_init.add_argument("--force", action="store_true")
    config_validate = config_sub.add_parser("validate", help="Validate config.toml.")
    config_validate.add_argument("--home")
    config_path = config_sub.add_parser("path", help="Print config.toml path.")
    config_path.add_argument("--home")

    migrate_parser = subparsers.add_parser(
        "migrate-worktrees",
        help="Consolidate git-worktree buckets.",
    )
    migrate_parser.add_argument("--home")
    migrate_parser.add_argument("--apply", action="store_true")


def _query_hash(query: str) -> str:
    return hashlib.sha1(query.encode("utf-8")).hexdigest()[:12]


def _log_recall(started: float, *, cwd: str, query: str, result: dict[str, Any], output_format: str, exit_code: int) -> None:
    duration_ms = int((time.monotonic() - started) * 1000)
    get_logger().info(
        "csep recall completed",
        extra={
            "kind": "csep-recall",
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "cwd": cwd,
            "query_hash": _query_hash(query),
            "count": int(result.get("count") or 0),
            "status": result.get("status") or ("matched" if result.get("count") else "no_match"),
            "output_format": output_format,
        },
    )


def _handle_recall(args: argparse.Namespace) -> int:
    started = time.monotonic()
    configure_logging()
    query = " ".join(args.query).strip()
    cwd = str(Path(args.cwd or os.getcwd()).expanduser().resolve())
    if not query and not args.recent:
        raise SystemExit("csep recall requires a query unless --recent is used")
    try:
        result = build_focused_recall(
            query=query,
            cwd=cwd,
            state_dir=args.state_dir,
            top_k=max(1, args.limit),
            global_scope=args.global_scope,
            recent=args.recent,
            before=args.before,
            after=args.after,
            budget_chars=args.budget_chars,
            message_chars=args.message_chars,
            tool_message_chars=args.tool_message_chars,
            current_session_id=args.current_session_id,
        )
        result.update({"triggered": True, "reasons": ["self_invoked"], "status": "matched" if result["count"] else "no_match"})
    except Exception as exc:  # noqa: BLE001 - recall is a soft dependency for the model.
        result = {
            "query": query,
            "count": 0,
            "results": [],
            "focused_recall": "",
            "triggered": True,
            "reasons": ["self_invoked"],
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        }

    if args.format == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(render_focused_recall_markdown(result), end="")
    _log_recall(started, cwd=cwd, query=query, result=result, output_format=args.format, exit_code=0)
    return 0


def _handle_session_archive(args: argparse.Namespace) -> int:
    configure_logging()
    db_path = default_db_path(state_dir=args.state_dir)
    if args.from_hook_payload:
        result = archive_from_hook_payload(
            args.from_hook_payload,
            db_path=db_path,
            cleanup_payload=args.cleanup_payload,
        )
    else:
        if not args.transcript_path or not args.cwd or not args.session_id:
            raise SystemExit("session-archive requires --from-hook-payload or --transcript-path + --cwd + --session-id")
        result = archive_transcript(
            args.transcript_path,
            session_id=args.session_id,
            cwd=args.cwd,
            db_path=db_path,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "archived" else 1


def _handle_session_ingest(args: argparse.Namespace) -> int:
    configure_logging()
    db_path = default_db_path(state_dir=args.state_dir)
    result = backfill_sessions(
        root=args.root,
        cwd=args.cwd,
        since_days=args.since_days,
        limit_files=args.limit_files,
        db_path=db_path,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in _MAIN_CLI_COMMANDS:
        return full_cli.main(argv, prog="csep")
    if args.command == "recall":
        return _handle_recall(args)
    if args.command == "session-archive":
        return _handle_session_archive(args)
    if args.command == "session-ingest":
        return _handle_session_ingest(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

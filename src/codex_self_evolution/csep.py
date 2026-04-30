from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from .logging_setup import configure as configure_logging, get_logger
from .recall.workflow import build_focused_recall, render_focused_recall_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="csep",
        description="Short runtime commands for codex-self-evolution.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    recall = subparsers.add_parser(
        "recall",
        help="Run focused recall for a model-generated query. Defaults to Markdown output.",
    )
    recall.add_argument("query", nargs="+", help="Focused recall query. Quote it when it contains spaces.")
    recall.add_argument("--cwd", default=None, help="Repo/current working directory. Defaults to $PWD.")
    recall.add_argument("--state-dir", default=None)
    recall.add_argument("--top-k", type=int, default=3)
    recall.add_argument("--format", choices=("markdown", "json"), default="markdown")
    return parser


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
    try:
        result = build_focused_recall(
            query=query,
            cwd=cwd,
            state_dir=args.state_dir,
            top_k=max(1, args.top_k),
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "recall":
        return _handle_recall(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

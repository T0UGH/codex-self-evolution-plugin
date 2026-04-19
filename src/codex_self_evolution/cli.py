from __future__ import annotations

import argparse
import json
from pathlib import Path

from .compiler.engine import preflight_compile, run_compile
from .hooks.session_start import session_start
from .hooks.stop_review import stop_review
from .recall.search import search_recall
from .recall.workflow import build_focused_recall, evaluate_recall_trigger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-self-evolution")
    subparsers = parser.add_subparsers(dest="command", required=True)

    session_parser = subparsers.add_parser("session-start")
    session_parser.add_argument("--cwd", required=True)
    session_parser.add_argument("--state-dir")

    stop_parser = subparsers.add_parser("stop-review")
    stop_parser.add_argument("--hook-payload", required=True)
    stop_parser.add_argument("--state-dir")

    compile_parser = subparsers.add_parser("compile")
    compile_parser.add_argument("--state-dir")
    compile_parser.add_argument("--repo-root")
    compile_parser.add_argument("--once", action="store_true")
    compile_parser.add_argument("--backend", default="script")

    preflight_parser = subparsers.add_parser("compile-preflight")
    preflight_parser.add_argument("--state-dir")
    preflight_parser.add_argument("--repo-root")

    recall_parser = subparsers.add_parser("recall")
    recall_parser.add_argument("--query", required=True)
    recall_parser.add_argument("--cwd", required=True)
    recall_parser.add_argument("--state-dir")

    trigger_parser = subparsers.add_parser("recall-trigger")
    trigger_parser.add_argument("--query", required=True)
    trigger_parser.add_argument("--cwd", required=True)
    trigger_parser.add_argument("--state-dir")
    trigger_parser.add_argument("--explicit", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "session-start":
        result = session_start(cwd=args.cwd, state_dir=args.state_dir)
    elif args.command == "stop-review":
        result = stop_review(hook_payload=args.hook_payload, state_dir=args.state_dir)
    elif args.command == "compile":
        result = run_compile(repo_root=args.repo_root, state_dir=args.state_dir, backend=args.backend)
    elif args.command == "compile-preflight":
        result = preflight_compile(repo_root=args.repo_root, state_dir=args.state_dir)
    elif args.command == "recall":
        result = {"query": args.query, "results": search_recall(query=args.query, cwd=args.cwd, state_dir=args.state_dir)}
    elif args.command == "recall-trigger":
        trigger = evaluate_recall_trigger(query=args.query, explicit=args.explicit)
        result = {**trigger, **(build_focused_recall(query=args.query, cwd=args.cwd, state_dir=args.state_dir) if trigger["triggered"] else {"query": args.query, "count": 0, "results": [], "focused_recall": ""})}
    else:
        parser.error(f"unknown command: {args.command}")
        return 2

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

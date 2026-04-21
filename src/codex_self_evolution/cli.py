from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .compiler.engine import preflight_compile, run_compile, scan_all_projects
from .hooks.codex_bridge import map_codex_stop_payload
from .hooks.session_start import format_session_start_for_codex, session_start
from .hooks.stop_review import stop_review
from .recall.search import search_recall
from .recall.workflow import build_focused_recall, evaluate_recall_trigger, evaluate_session_recall


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-self-evolution")
    subparsers = parser.add_subparsers(dest="command", required=True)

    session_parser = subparsers.add_parser("session-start")
    # Required for manual/test invocation; ignored when --from-stdin reads cwd
    # from the Codex hook payload.
    session_parser.add_argument("--cwd")
    session_parser.add_argument("--state-dir")
    session_parser.add_argument(
        "--from-stdin",
        action="store_true",
        help="Read a Codex SessionStart hook JSON payload from stdin, extract "
             "cwd, build the stable-background bundle, and emit Codex "
             "hookSpecificOutput JSON so the context is injected as "
             "DeveloperInstructions in the model's session.",
    )

    stop_parser = subparsers.add_parser("stop-review")
    stop_parser.add_argument("--hook-payload")
    stop_parser.add_argument("--state-dir")
    stop_parser.add_argument(
        "--from-stdin",
        action="store_true",
        help="Read a Codex native Stop hook JSON payload from stdin, map it to the "
             "stop_review schema, fire a detached background reviewer, and emit "
             '{"continue": true} so Codex unblocks within its hook timeout.',
    )
    stop_parser.add_argument(
        "--cleanup-payload",
        action="store_true",
        help="Delete --hook-payload after the reviewer finishes (internal use by --from-stdin).",
    )

    compile_parser = subparsers.add_parser("compile")
    compile_parser.add_argument("--state-dir")
    compile_parser.add_argument("--repo-root")
    compile_parser.add_argument("--once", action="store_true")
    compile_parser.add_argument("--backend", default="script")

    preflight_parser = subparsers.add_parser("compile-preflight")
    preflight_parser.add_argument("--state-dir")
    preflight_parser.add_argument("--repo-root")

    scan_parser = subparsers.add_parser(
        "scan",
        help="Run preflight+compile on every project bucket under <home>/projects/. "
             "Designed for launchd/cron scheduling — a single invocation drains all "
             "repos that have pending suggestions, with per-bucket exception isolation.",
    )
    scan_parser.add_argument(
        "--home",
        help="Override CODEX_SELF_EVOLUTION_HOME for this invocation (default "
             "~/.codex-self-evolution). Mostly useful in tests.",
    )
    # Unlike `compile`, scan defaults to agent:opencode since it runs unattended
    # and users who care about LLM cost will have flipped opencode off anyway.
    # Fallback to script still kicks in automatically if opencode is unavailable.
    scan_parser.add_argument("--backend", default="agent:opencode")

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


def _run_stop_review(args: argparse.Namespace) -> dict:
    """Synchronous stop_review with optional post-run payload cleanup."""
    try:
        return stop_review(hook_payload=args.hook_payload, state_dir=args.state_dir)
    finally:
        if args.cleanup_payload and args.hook_payload:
            try:
                Path(args.hook_payload).unlink()
            except OSError:
                pass


def _handle_session_start_from_stdin(args: argparse.Namespace) -> int:
    """Codex SessionStart hook entry point.

    Codex sends a JSON payload on stdin (fields per
    developers.openai.com/codex/hooks: session_id, transcript_path, cwd,
    hook_event_name, model, source) and expects a JSON response on stdout
    within the hook timeout. We return a Codex ``hookSpecificOutput`` shape
    whose ``additionalContext`` text gets injected as ``DeveloperInstructions``
    in the model session — verified against codex-cli 0.122.0.

    Error discipline: this hook runs at session startup and must never block.
    Any parse or runtime failure falls through to ``{"continue": true,
    "warning": ...}`` so Codex can still start — worst case the user's
    session just won't have the stable-background prefix injected.
    """
    try:
        raw = sys.stdin.read()
        codex_payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"continue": True, "warning": f"invalid codex payload: {exc}"}))
        return 0

    if not isinstance(codex_payload, dict):
        print(json.dumps({"continue": True, "warning": "codex payload is not an object"}))
        return 0

    # Prefer the cwd Codex tells us about. Fall back to --cwd only for manual
    # shell testing of the hook command outside a real Codex session.
    cwd = codex_payload.get("cwd") if isinstance(codex_payload.get("cwd"), str) else None
    if not cwd:
        cwd = args.cwd
    if not cwd:
        print(json.dumps({"continue": True, "warning": "no cwd in codex payload or --cwd flag"}))
        return 0

    try:
        session_result = session_start(cwd=cwd, state_dir=args.state_dir)
        codex_output = format_session_start_for_codex(session_result)
    except Exception as exc:  # noqa: BLE001 — never block session startup
        print(json.dumps({"continue": True, "warning": f"session_start failed: {exc}"}))
        return 0

    print(json.dumps(codex_output))
    return 0


def _handle_stop_from_stdin(args: argparse.Namespace) -> int:
    """Codex Stop hook entry point.

    Codex will send a JSON object on stdin and expects a JSON response on
    stdout within its per-hook timeout (typically 5-10s). We:

    1. Read + parse the Codex payload from stdin.
    2. Map it to the plugin's native stop_review schema.
    3. Persist the mapped payload to a temp file.
    4. Spawn a *detached* child process that re-invokes this CLI with
       ``stop-review --hook-payload <tmp> --cleanup-payload`` so the reviewer
       (which can take tens of seconds against a real provider) runs in the
       background and cleans its own tempfile.
    5. Print ``{"continue": true}`` and return immediately.

    The child runs via ``sys.executable -m codex_self_evolution.cli`` to avoid
    any dependency on ``uvx`` / PATH at runtime — whatever interpreter is
    running this module can re-enter itself.
    """
    try:
        raw = sys.stdin.read()
        codex_payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"continue": True, "warning": f"invalid codex payload: {exc}"}))
        return 0

    if not isinstance(codex_payload, dict):
        print(json.dumps({"continue": True, "warning": "codex payload is not an object"}))
        return 0

    mapped = map_codex_stop_payload(codex_payload)

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        prefix="codex-self-evolution-stop-",
        suffix=".json",
        delete=False,
    ) as handle:
        json.dump(mapped, handle)
        tmp_path = handle.name

    child_argv = [
        sys.executable,
        "-m",
        "codex_self_evolution.cli",
        "stop-review",
        "--hook-payload",
        tmp_path,
        "--cleanup-payload",
    ]
    if args.state_dir:
        child_argv.extend(["--state-dir", args.state_dir])

    # Background reviewer can fail silently (network, provider, schema). Point
    # stderr/stdout at a per-pid log file so we can post-mortem without turning
    # the hook into a foreground blocker.
    log_dir = Path(tempfile.gettempdir()) / "codex-self-evolution"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"stop-review-{os.getpid()}-{int(os.times()[4])}.log"
    try:
        log_handle = open(log_path, "w", encoding="utf-8")
    except OSError:
        log_handle = subprocess.DEVNULL  # type: ignore[assignment]

    try:
        subprocess.Popen(  # noqa: S603 — trusted argv
            child_argv,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        # The tempfile will leak, but that's preferable to failing the hook.
        print(json.dumps({"continue": True, "warning": f"failed to spawn reviewer: {exc}"}))
        return 0

    print(json.dumps({"continue": True}))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "session-start":
        if args.from_stdin:
            return _handle_session_start_from_stdin(args)
        if not args.cwd:
            parser.error("session-start requires --cwd or --from-stdin")
        result = session_start(cwd=args.cwd, state_dir=args.state_dir)
    elif args.command == "stop-review":
        if args.from_stdin:
            return _handle_stop_from_stdin(args)
        if not args.hook_payload:
            parser.error("stop-review requires --hook-payload or --from-stdin")
        result = _run_stop_review(args)
    elif args.command == "compile":
        result = run_compile(repo_root=args.repo_root, state_dir=args.state_dir, backend=args.backend)
    elif args.command == "compile-preflight":
        result = preflight_compile(repo_root=args.repo_root, state_dir=args.state_dir)
    elif args.command == "scan":
        result = scan_all_projects(home=args.home, backend=args.backend)
    elif args.command == "recall":
        result = {"query": args.query, "results": search_recall(query=args.query, cwd=args.cwd, state_dir=args.state_dir)}
    elif args.command == "recall-trigger":
        session_payload = session_start(cwd=args.cwd, state_dir=args.state_dir)
        result = evaluate_session_recall(
            query=args.query,
            cwd=args.cwd,
            state_dir=args.state_dir,
            session_payload=session_payload,
            explicit=args.explicit,
        )
    else:
        parser.error(f"unknown command: {args.command}")
        return 2

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

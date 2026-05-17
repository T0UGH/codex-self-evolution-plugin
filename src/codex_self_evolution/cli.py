from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from . import __version__
from .config_file import (
    ConfigError,
    config_to_dict,
    get_config_path,
    load_config,
)
from .config_file_template import CONFIG_TEMPLATE
from .diagnostics import collect_status
from .env_loader import hydrate_env_for_subprocesses
from .hooks.session_start import format_session_start_for_codex, session_start
from .logging_setup import configure as configure_logging, get_logger
from .migrate import run_migration
from .session_reflection.runner import (
    enqueue_reflection_from_payload,
    run_reflection_job,
    session_reflection_status,
)


def build_parser(prog: str = "codex-self-evolution") -> argparse.ArgumentParser:
    """Build the retained runtime parser under the requested console-script name."""
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--version", action="version", version=f"{prog} {__version__}")
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

    stop_parser = subparsers.add_parser("session-stop")
    stop_parser.add_argument("--state-dir")
    stop_parser.add_argument(
        "--from-stdin",
        action="store_true",
        help="Read a Codex native Stop hook JSON payload from stdin, archive the session, "
             "evaluate the deterministic reflection trigger policy, enqueue a session-reflection "
             'job when needed, and emit {"continue": true}.',
    )

    reflect_parser = subparsers.add_parser("session-reflect")
    reflect_mode = reflect_parser.add_mutually_exclusive_group(required=True)
    reflect_mode.add_argument("--hook-payload")
    reflect_mode.add_argument("--job")
    reflect_mode.add_argument("--status", action="store_true")
    reflect_parser.add_argument("--home")

    status_parser = subparsers.add_parser(
        "status",
        help="Read-only diagnostic snapshot: which hooks are wired, whether "
             "which API keys are set (reports names only, never values), CLI "
             "tool versions, and per-bucket session state. Outputs JSON.",
    )
    status_parser.add_argument(
        "--home",
        help="Override CODEX_SELF_EVOLUTION_HOME (default ~/.codex-self-evolution).",
    )

    config_parser = subparsers.add_parser(
        "config",
        help="Inspect / initialise / validate ~/.codex-self-evolution/config.toml "
             "— the single source of truth for retained session-level behavior.",
    )
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)

    config_show = config_sub.add_parser(
        "show",
        help="Print the fully-resolved configuration — including whether each "
             "value came from config.toml or defaults.",
    )
    config_show.add_argument("--home")
    config_show.add_argument(
        "--raw",
        action="store_true",
        help="Print raw config.toml contents instead of the merged resolution.",
    )

    config_init = config_sub.add_parser(
        "init",
        help="Write a starter config.toml skeleton. Refuses to overwrite by default.",
    )
    config_init.add_argument("--home")
    config_init.add_argument("--force", action="store_true",
                              help="Overwrite an existing config.toml.")

    config_validate = config_sub.add_parser(
        "validate",
        help="Load + lint the current config. Exits 0 on clean, 1 on warnings, 2 on parse error.",
    )
    config_validate.add_argument("--home")

    config_path = config_sub.add_parser(
        "path",
        help="Print the absolute path to config.toml (whether or not it exists).",
    )
    config_path.add_argument("--home")

    migrate_parser = subparsers.add_parser(
        "migrate-worktrees",
        help="Consolidate buckets that belong to git worktrees of the same logical "
             "repo. Without --apply runs in dry-run mode and prints the plan.",
    )
    migrate_parser.add_argument(
        "--home",
        help="Override CODEX_SELF_EVOLUTION_HOME for this invocation.",
    )
    migrate_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the migration. Without this flag the command "
             "only prints the plan (dry-run).",
    )

    return parser


def _spawn_session_archive_from_stop_payload(
    codex_payload: dict,
    args: argparse.Namespace,
    logger,
) -> None:
    """Spawn a detached session archive child from the raw Codex Stop payload."""
    try:
        session_recall = load_config().config.session_recall
    except ConfigError as exc:
        logger.warning(
            "session recall config unavailable; skipping stop-hook archive",
            extra={"kind": "session_recall_archive_skipped", "error": str(exc)[:400]},
        )
        return
    if not session_recall.enabled or not session_recall.stop_hook_archive:
        return

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        prefix="codex-self-evolution-session-archive-",
        suffix=".json",
        delete=False,
    ) as handle:
        json.dump(codex_payload, handle)
        tmp_path = handle.name

    child_argv = [
        sys.executable,
        "-m",
        "codex_self_evolution.csep",
        "session-archive",
        "--from-hook-payload",
        tmp_path,
        "--cleanup-payload",
    ]
    if args.state_dir:
        child_argv.extend(["--state-dir", args.state_dir])

    log_dir = Path(tempfile.gettempdir()) / "codex-self-evolution"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"session-archive-{os.getpid()}-{int(os.times()[4])}.log"
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
        if hasattr(log_handle, "close"):
            log_handle.close()
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass
        logger.warning(
            "failed to spawn session archive",
            extra={"kind": "session_recall_archive_spawn_failed", "error": str(exc)[:400]},
        )


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


def _handle_session_stop_from_stdin(args: argparse.Namespace) -> int:
    """Codex Stop hook entry point.

    Codex will send a JSON object on stdin and expects a JSON response on
    stdout within its per-hook timeout (typically 5-10s). We:

    1. Read + parse the Codex payload from stdin.
    2. Enqueue a session-reflection job using the raw Codex payload.
    3. Spawn a *detached* child process that re-invokes this CLI with
       ``session-reflect --job <job_id>`` so app-server reflection runs in the
       background.
    4. Print ``{"continue": true}`` and return immediately.

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

    _spawn_session_archive_from_stop_payload(codex_payload, args, get_logger())

    try:
        queued = enqueue_reflection_from_payload(codex_payload, home=args.state_dir)
    except Exception as exc:  # noqa: BLE001 — Stop hook must not block Codex.
        print(json.dumps({"continue": True, "warning": f"failed to enqueue reflection job: {exc}"}))
        return 0

    job_id = queued.get("job_id")
    if queued.get("status") != "queued" or not job_id:
        print(json.dumps({"continue": True}))
        return 0

    child_argv = [
        sys.executable,
        "-m",
        "codex_self_evolution.csep",
        "session-reflect",
        "--job",
        str(job_id),
    ]
    if args.state_dir:
        child_argv.extend(["--home", args.state_dir])

    # Background reflection can fail silently (app server, provider, schema). Point
    # stderr/stdout at a per-pid log file so we can post-mortem without turning
    # the hook into a foreground blocker.
    log_dir = Path("/tmp") / "codex-self-evolution"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"session-reflect-{os.getpid()}-{int(os.times()[4])}.log"
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
        if hasattr(log_handle, "close"):
            log_handle.close()
        print(json.dumps({"continue": True, "warning": f"failed to spawn reflection worker: {exc}"}))
        return 0

    if hasattr(log_handle, "close"):
        log_handle.close()
    print(json.dumps({"continue": True}))
    return 0


def _handle_session_reflect(args: argparse.Namespace) -> dict[str, Any]:
    """Dispatch the session-reflection CLI modes and return JSON-serializable output."""
    if args.job:
        return run_reflection_job(args.job, home=args.home)
    if args.status:
        return session_reflection_status(home=args.home)

    payload_path = Path(args.hook_payload)
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("session-reflect --hook-payload must contain a JSON object")
    queued = enqueue_reflection_from_payload(payload, home=args.home)
    if queued.get("status") == "queued" and queued.get("job_id"):
        return run_reflection_job(str(queued["job_id"]), home=args.home)
    return queued


def main(argv: list[str] | None = None, *, prog: str = "codex-self-evolution") -> int:
    """Run retained runtime commands for either the long or short entrypoint."""
    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)

    # Install the JSON-lines file logger before anything that might fail.
    # Every main() invocation is a fresh short-lived process (hook or a
    # user-typed command), so reconfiguring on entry is cheap and keeps
    # test isolation tight — configure() also acts as a reset.
    configure_logging()
    logger = get_logger()

    # Hydrate ~/.codex-self-evolution/.env.provider into os.environ so local
    # subprocesses can find provider keys when launched by hooks.
    hydrated = hydrate_env_for_subprocesses()
    if hydrated:
        # Values are NEVER logged; only the key names that just entered scope.
        logger.info("env provider hydrated", extra={"kind": "env_hydrate", "keys": sorted(hydrated)})

    started = time.monotonic()

    try:
        if args.command == "session-start":
            if args.from_stdin:
                exit_code = _handle_session_start_from_stdin(args)
                _log_command(logger, args.command, started, exit_code=exit_code)
                return exit_code
            if not args.cwd:
                parser.error("session-start requires --cwd or --from-stdin")
            result = session_start(cwd=args.cwd, state_dir=args.state_dir)
        elif args.command == "session-stop":
            if args.from_stdin:
                exit_code = _handle_session_stop_from_stdin(args)
                _log_command(logger, args.command, started, exit_code=exit_code, mode="from_stdin")
                return exit_code
            parser.error("session-stop requires --from-stdin")
        elif args.command == "session-reflect":
            result = _handle_session_reflect(args)
        elif args.command == "status":
            result = collect_status(home=args.home)
        elif args.command == "migrate-worktrees":
            result = run_migration(
                home=Path(args.home).expanduser().resolve() if args.home else None,
                apply=args.apply,
            )
        elif args.command == "config":
            result = _handle_config_subcommand(args)
            if result.get("_exit_code") is not None:
                exit_code = result.pop("_exit_code")
                print(json.dumps(result, indent=2, sort_keys=True))
                _log_command(logger, args.command, started, exit_code=exit_code,
                             subcommand=args.config_command)
                return exit_code
        else:
            parser.error(f"unknown command: {args.command}")
            return 2

        print(json.dumps(result, indent=2, sort_keys=True))
        log_extras = _observability_extras(args.command, result)
        _log_command(logger, args.command, started, exit_code=0, **log_extras)
        return 0
    except SystemExit:
        # argparse calls sys.exit(2) for bad args; re-raise so the user still
        # sees the usage message. Don't bother logging — argparse already
        # printed to stderr and nothing interesting ran.
        raise
    except Exception as exc:  # noqa: BLE001 — log everything, let caller decide
        # Record the failure before re-raising so the log captures what the
        # user won't see in their terminal (if stderr was piped somewhere).
        _log_command(
            logger, args.command, started,
            exit_code=1,
            error_type=type(exc).__name__,
            error_message=str(exc)[:400],
        )
        raise


def _handle_config_subcommand(args: argparse.Namespace) -> dict:
    """Dispatcher for retained config subcommands.

    Returns a dict; callers check for ``_exit_code`` to handle non-zero
    exit paths (validate warnings, migrate no-ops, etc.) uniformly.
    """
    home = Path(args.home).expanduser().resolve() if args.home else None
    if args.config_command == "path":
        return {"_exit_code": 0, "config_path": str(get_config_path(home))}

    if args.config_command == "init":
        path = get_config_path(home)
        existed_before = path.exists()
        if existed_before and not args.force:
            return {
                "_exit_code": 1,
                "status": "exists",
                "config_path": str(path),
                "error": "config.toml already exists; use --force to overwrite",
            }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        return {
            "_exit_code": 0,
            "status": "overwritten" if existed_before else "created",
            "config_path": str(path),
        }

    if args.config_command == "show":
        if args.raw:
            path = get_config_path(home)
            if path.is_file():
                return {"_exit_code": 0, "config_path": str(path),
                        "config_exists": True, "raw": path.read_text(encoding="utf-8")}
            return {"_exit_code": 0, "config_path": str(path),
                    "config_exists": False, "raw": ""}
        try:
            loaded = load_config(home=home)
        except ConfigError as exc:
            return {"_exit_code": 2, "status": "parse_error", "error": str(exc)}
        env_provider_view = _env_provider_api_key_summary(home)
        return {
            "_exit_code": 0,
            "config_path": str(loaded.config_path),
            "config_exists": loaded.config_exists,
            "schema_version": loaded.config.schema_version,
            "resolved": config_to_dict(loaded.config),
            "sources": loaded.sources,
            "warnings": loaded.warnings,
            "env_provider": env_provider_view,
        }

    if args.config_command == "validate":
        try:
            loaded = load_config(home=home)
        except ConfigError as exc:
            return {"_exit_code": 2, "status": "parse_error", "error": str(exc)}
        exit_code = 1 if loaded.warnings else 0
        return {
            "_exit_code": exit_code,
            "status": "ok" if exit_code == 0 else "warnings",
            "config_path": str(loaded.config_path),
            "config_exists": loaded.config_exists,
            "warnings": loaded.warnings,
        }

    return {"_exit_code": 2, "error": f"unknown config subcommand: {args.config_command}"}


def _env_provider_api_key_summary(home: Path | None) -> dict:
    """Show API key presence (never values) for `config show` output."""
    from .diagnostics import _check_env_provider
    from .config import get_home_dir

    home_dir = home or get_home_dir()
    data = _check_env_provider(home_dir)
    return {
        "path": data["path"],
        "exists": data["exists"],
        "keys_set": data["keys_set"],
        "keys_unset": data["keys_unset"],
        "other_keys_set": data["other_keys_set"],
    }
def _observability_extras(command: str | None, result: object) -> dict:
    """Return compact per-command log extras for retained commands only."""
    if not isinstance(result, dict):
        return {}
    if command == "session-stop":
        return {"mode": "from_stdin"}
    if command == "session-reflect":
        status = result.get("status")
        job_id = result.get("job_id")
        extras: dict[str, object] = {}
        if status:
            extras["status"] = status
        if job_id:
            extras["job_id"] = job_id
        return extras
    return {}


def _log_command(
    logger, command: str, started: float, *, exit_code: int, **extras,
) -> None:
    """Emit one structured summary line per CLI invocation.

    Called at the boundary of ``main()`` so each hook or manual CLI call
    leaves exactly one record behind. Start with the boundary and push inward
    only if an investigation actually needs it.
    """
    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "cli command completed",
        extra={
            "kind": command or "unknown",
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            **extras,
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())

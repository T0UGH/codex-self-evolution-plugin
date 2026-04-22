from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .compiler.engine import preflight_compile, run_compile, scan_all_projects
from .config_file import (
    ConfigError,
    LoadResult,
    config_to_dict,
    get_config_path,
    load_config,
)
from .config_file_template import CONFIG_TEMPLATE
from .diagnostics import collect_status
from .env_loader import hydrate_env_for_subprocesses
from .hooks.codex_bridge import map_codex_stop_payload
from .hooks.session_start import format_session_start_for_codex, session_start
from .hooks.stop_review import stop_review
from .logging_setup import configure as configure_logging, get_logger
from .migrate import run_migration
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

    status_parser = subparsers.add_parser(
        "status",
        help="Read-only diagnostic snapshot: which hooks are wired, whether "
             "launchd scheduler is loaded, which API keys are set (reports "
             "names only, never values), CLI tool versions, and per-bucket "
             "pending/done/failed counts + last compile receipt. Outputs JSON.",
    )
    status_parser.add_argument(
        "--home",
        help="Override CODEX_SELF_EVOLUTION_HOME (default ~/.codex-self-evolution).",
    )

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

    config_parser = subparsers.add_parser(
        "config",
        help="Inspect / initialise / validate ~/.codex-self-evolution/config.toml "
             "— the single source of truth for plugin behavior (provider, "
             "model, backend, timeouts). Sibling .env.provider keeps API keys.",
    )
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)

    config_show = config_sub.add_parser(
        "show",
        help="Print the fully-resolved configuration — including which layer "
             "(env var / config.toml / default) each value came from.",
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

    config_migrate = config_sub.add_parser(
        "migrate-from-env",
        help="Scan os.environ (including .env.provider) for legacy reviewer/compile "
             "settings and write them to config.toml so behavior is explicitly "
             "captured in one place. Does not modify .env.provider — you can "
             "unset the legacy vars yourself afterward.",
    )
    config_migrate.add_argument("--home")
    config_migrate.add_argument("--force", action="store_true",
                                 help="Overwrite an existing config.toml.")

    config_use = config_sub.add_parser(
        "use",
        help="Switch active_profile by rewriting only that line in config.toml. "
             "Preserves comments + other settings.",
    )
    config_use.add_argument("profile", help="Name of a [profiles.<name>] section defined in config.toml.")
    config_use.add_argument("--home")

    config_list = config_sub.add_parser(
        "list-profiles",
        help="List every [profiles.<name>] defined in config.toml, "
             "marking the active one.",
    )
    config_list.add_argument("--home")

    config_migrate_v2 = config_sub.add_parser(
        "migrate-to-v2",
        help="Rewrite a schema_version=1 config.toml so the legacy [reviewer] "
             "block becomes a [profiles.default] section + active_profile.",
    )
    config_migrate_v2.add_argument("--home")
    config_migrate_v2.add_argument("--force", action="store_true",
                                    help="Overwrite an existing v2 config.toml.")

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

    # Install the JSON-lines file logger before anything that might fail.
    # Every main() invocation is a fresh short-lived process (hook, scheduler,
    # or a user-typed command), so reconfiguring on entry is cheap and keeps
    # test isolation tight — configure() also acts as a reset.
    configure_logging()
    logger = get_logger()

    # Hydrate ~/.codex-self-evolution/.env.provider into os.environ so that
    # subprocesses (opencode for compile, urllib for the MiniMax reviewer)
    # can find their API keys even when we're launched by launchd with a
    # minimal PATH+HOME-only environment. Without this, every launchd scan
    # fired opencode with no MINIMAX_API_KEY, MiniMax returned 401, opencode
    # emitted a type:"error" event that the old extractor silently discarded,
    # and every receipt fell back to script backend — observed 2026-04-22.
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
        elif args.command == "stop-review":
            if args.from_stdin:
                exit_code = _handle_stop_from_stdin(args)
                _log_command(logger, args.command, started, exit_code=exit_code, mode="from_stdin")
                return exit_code
            if not args.hook_payload:
                parser.error("stop-review requires --hook-payload or --from-stdin")
            result = _run_stop_review(args)
        elif args.command == "compile":
            result = run_compile(repo_root=args.repo_root, state_dir=args.state_dir, backend=args.backend)
        elif args.command == "compile-preflight":
            result = preflight_compile(repo_root=args.repo_root, state_dir=args.state_dir)
        elif args.command == "scan":
            result = scan_all_projects(home=args.home, backend=args.backend)
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
    """Dispatcher for ``codex-self-evolution config <subcommand>``.

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

    if args.config_command == "use":
        try:
            loaded = load_config(home=home)
        except ConfigError as exc:
            return {"_exit_code": 2, "status": "parse_error", "error": str(exc)}
        target = args.profile
        if target not in loaded.config.profile_names:
            return {
                "_exit_code": 1,
                "status": "unknown_profile",
                "requested": target,
                "available": loaded.config.profile_names,
                "error": f"no [profiles.{target}] section found; available: "
                         f"{loaded.config.profile_names}",
            }
        path = loaded.config_path
        if not path.is_file():
            return {"_exit_code": 1, "status": "no_config",
                    "error": "config.toml does not exist; run `config init` first"}
        _rewrite_active_profile(path, target)
        return {
            "_exit_code": 0,
            "status": "switched",
            "active_profile": target,
            "config_path": str(path),
        }

    if args.config_command == "list-profiles":
        try:
            loaded = load_config(home=home)
        except ConfigError as exc:
            return {"_exit_code": 2, "status": "parse_error", "error": str(exc)}
        return {
            "_exit_code": 0,
            "active_profile": loaded.config.active_profile,
            "profiles": loaded.config.profile_names,
            "config_path": str(loaded.config_path),
            "config_exists": loaded.config_exists,
        }

    if args.config_command == "migrate-to-v2":
        path = get_config_path(home)
        if not path.is_file():
            return {"_exit_code": 1, "status": "no_config",
                    "error": "config.toml does not exist; nothing to migrate"}
        try:
            loaded = load_config(home=home)
        except ConfigError as exc:
            return {"_exit_code": 2, "status": "parse_error", "error": str(exc)}
        # Nothing to do if already schema 2 with profiles.
        if loaded.config.schema_version >= 2 and loaded.config.profile_names \
                and not any("deprecated" in w for w in loaded.warnings):
            return {"_exit_code": 0, "status": "already_v2",
                    "config_path": str(path)}
        new_text = _render_v2_from_loaded(loaded)
        # Write atomically via temp + rename so a failed migration doesn't
        # leave the user with a half-written config.
        tmp_path = path.with_suffix(".toml.tmp")
        tmp_path.write_text(new_text, encoding="utf-8")
        tmp_path.replace(path)
        return {
            "_exit_code": 0,
            "status": "migrated",
            "config_path": str(path),
            "active_profile": "default",
        }

    if args.config_command == "migrate-from-env":
        # Pull env-driven values into a persisted config.toml so behavior
        # is explicit rather than implicit. Only writes the fields whose
        # sources are environment overrides — defaults stay commented out.
        path = get_config_path(home)
        if path.exists() and not args.force:
            return {
                "_exit_code": 1,
                "status": "exists",
                "config_path": str(path),
                "error": "config.toml already exists; use --force to overwrite",
            }
        loaded = load_config(home=home)
        toml_text = _render_migrated_toml(loaded)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(toml_text, encoding="utf-8")
        migrated = [k for k, v in loaded.sources.items() if v.startswith("env:")]
        return {
            "_exit_code": 0,
            "status": "migrated",
            "config_path": str(path),
            "migrated_fields": sorted(migrated),
            "hint": "Review the file; unset the legacy env vars in .env.provider once you've confirmed.",
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
    """Show API key presence (never values) for `config show` output.

    Reuses the same logic as diagnostics._check_env_provider — the
    reviewer provider decision often hinges on "is this key set at all?",
    so surfacing it alongside resolved config saves a round-trip to
    `status`.
    """
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


def _rewrite_active_profile(path: Path, new_value: str) -> None:
    """Update ``active_profile = "X"`` inline, preserving comments + layout.

    Three cases to handle:

    1. ``active_profile = ...`` already exists: replace the RHS only.
    2. It doesn't exist but ``schema_version = ...`` does: insert the new
       line right after schema_version so the two version/selection lines
       travel together.
    3. Neither exists: prepend a schema + active line block.

    Uses text-level edits instead of round-tripping TOML because tomllib
    is read-only and hand-rolling a full TOML writer drops user comments.
    """
    import re

    text = path.read_text(encoding="utf-8")
    active_re = re.compile(r'^(\s*active_profile\s*=\s*)(?:"[^"]*"|\S+)\s*$', re.MULTILINE)
    if active_re.search(text):
        new_text = active_re.sub(rf'\g<1>"{new_value}"', text)
    else:
        schema_re = re.compile(r'^(\s*schema_version\s*=\s*\d+)\s*$', re.MULTILINE)
        if schema_re.search(text):
            new_text = schema_re.sub(
                rf'\g<1>\nactive_profile = "{new_value}"',
                text,
                count=1,
            )
        else:
            new_text = f'schema_version = 2\nactive_profile = "{new_value}"\n\n' + text
    path.write_text(new_text, encoding="utf-8")


def _render_v2_from_loaded(loaded: LoadResult) -> str:
    """Produce a schema-2 config.toml from a loaded schema-1 state.

    Writes the legacy [reviewer] block's values as a [profiles.default]
    section and sets ``active_profile = "default"``. Non-reviewer sections
    (compile, scheduler, log) carry over verbatim.
    """
    cfg = loaded.config
    lines = [
        "# Migrated from schema_version = 1 by `config migrate-to-v2`.",
        "# The previous top-level [reviewer] block is now [profiles.default].",
        "",
        "schema_version = 2",
        f'active_profile = "default"',
        "",
        "[profiles.default]",
    ]
    for attr in ("provider", "model", "base_url"):
        value = getattr(cfg.reviewer, attr)
        if value:
            lines.append(f'{attr} = "{_toml_escape(str(value))}"')
    lines.append(f"timeout_seconds = {cfg.reviewer.timeout_seconds}")
    lines.append(f"max_tokens = {cfg.reviewer.max_tokens}")
    lines.append(f"max_retries = {cfg.reviewer.max_retries}")
    if cfg.reviewer.retry_backoff:
        formatted = ", ".join(str(v) for v in cfg.reviewer.retry_backoff)
        lines.append(f"retry_backoff = [{formatted}]")

    # compile section
    lines.extend([
        "",
        "[compile]",
        f'backend = "{_toml_escape(cfg.compile.backend)}"',
        f'allow_fallback = {"true" if cfg.compile.allow_fallback else "false"}',
    ])
    if cfg.compile.opencode.model or cfg.compile.opencode.agent or cfg.compile.opencode.timeout_seconds != 900.0:
        lines.append("")
        lines.append("[compile.opencode]")
        if cfg.compile.opencode.model:
            lines.append(f'model = "{_toml_escape(cfg.compile.opencode.model)}"')
        if cfg.compile.opencode.agent:
            lines.append(f'agent = "{_toml_escape(cfg.compile.opencode.agent)}"')
        lines.append(f"timeout_seconds = {cfg.compile.opencode.timeout_seconds}")

    lines.extend([
        "",
        "[scheduler]",
        f'backend = "{_toml_escape(cfg.scheduler.backend)}"',
        f"interval_seconds = {cfg.scheduler.interval_seconds}",
    ])
    lines.extend([
        "",
        "[log]",
        f"retention_days = {cfg.log.retention_days}",
    ])
    return "\n".join(lines).rstrip() + "\n"


def _render_migrated_toml(loaded: LoadResult) -> str:
    """Emit a config.toml that captures the current env-driven values as
    TOML settings. Kept deliberately small: only writes fields whose
    source was an env var (legacy or new), so defaults stay clean.

    We hand-roll TOML output because Python's stdlib ``tomllib`` is
    read-only; pulling in ``tomli-w`` would break our zero-deps promise.
    Output format matches the template: section headers + key = value.
    """
    cfg = loaded.config
    srcs = loaded.sources

    def emit(key: str, value: Any) -> str:
        if isinstance(value, bool):
            return f"{key} = {'true' if value else 'false'}"
        if isinstance(value, (int, float)):
            return f"{key} = {value}"
        if isinstance(value, list):
            inner = ", ".join(_toml_string_or_number(v) for v in value)
            return f"{key} = [{inner}]"
        return f'{key} = "{_toml_escape(str(value))}"'

    def from_env(path: str) -> bool:
        return srcs.get(path, "default").startswith("env:")

    lines = [
        "# Generated by `codex-self-evolution config migrate-from-env`.",
        "# Captured legacy env-driven values; review before relying on.",
        "",
        "schema_version = 1",
        "",
    ]

    # reviewer section
    reviewer_lines: list[str] = []
    for attr, path in [
        ("provider", "reviewer.provider"),
        ("model", "reviewer.model"),
        ("base_url", "reviewer.base_url"),
        ("timeout_seconds", "reviewer.timeout_seconds"),
    ]:
        if from_env(path):
            reviewer_lines.append(emit(attr, getattr(cfg.reviewer, attr)))
    if reviewer_lines:
        lines.append("[reviewer]")
        lines.extend(reviewer_lines)
        lines.append("")

    # compile section
    compile_lines: list[str] = []
    if from_env("compile.backend"):
        compile_lines.append(emit("backend", cfg.compile.backend))
    if compile_lines:
        lines.append("[compile]")
        lines.extend(compile_lines)
        lines.append("")

    # compile.opencode
    opencode_lines: list[str] = []
    for attr, path in [
        ("model", "compile.opencode.model"),
        ("agent", "compile.opencode.agent"),
    ]:
        if from_env(path):
            opencode_lines.append(emit(attr, getattr(cfg.compile.opencode, attr)))
    if opencode_lines:
        lines.append("[compile.opencode]")
        lines.extend(opencode_lines)
        lines.append("")

    # If no env-driven values were found, still emit something useful so
    # users don't see an empty file and think migration failed.
    if len(lines) <= 5:
        lines.append("# No legacy env-driven overrides found in your environment.")
        lines.append("# Run `codex-self-evolution config init` for the full template instead.")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _toml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _toml_string_or_number(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return f'"{_toml_escape(str(v))}"'


def _observability_extras(command: str | None, result: object) -> dict:
    """Surface the reviewer-action breakdown into the per-invocation log line
    so a week of plugin.log entries is enough to answer "did Phase 1 work?"
    without jq-iterating every receipt.

    - ``compile``: pull memory_action_stats straight from run_compile result
    - ``scan``: pull the aggregate across all buckets touched this run
    Anything else: no extras (keeps session-start / stop-review log lines tight).
    """
    if not isinstance(result, dict):
        return {}
    if command == "compile":
        stats = result.get("memory_action_stats") or {}
        extras: dict = {}
        if stats:
            extras["memory_action_stats"] = stats
        fallback = result.get("fallback_backend")
        if fallback:
            extras["fallback_backend"] = fallback
        discarded = result.get("discarded_count") or 0
        if discarded:
            extras["discarded_count"] = discarded
        return extras
    if command == "scan":
        aggregate = result.get("aggregate") or {}
        # Only log when something actually ran — skip-empty scans would
        # otherwise bloat plugin.log with a dozen zero-count lines.
        if aggregate.get("buckets_processed", 0) > 0 or aggregate.get("total_memory_suggestions", 0) > 0:
            return {"aggregate": aggregate}
        return {}
    if command == "stop-review":
        # The background reviewer is where MiniMax actually runs. Without
        # these fields you can't tell whether "0 memory_updates at compile
        # time" meant "reviewer emitted none" (working-as-intended, SKIP
        # list too strict) or "all reviewer calls 529'd" (upstream outage).
        extras: dict = {}
        provider = result.get("reviewer_provider")
        if provider:
            extras["reviewer_provider"] = provider
        for key in ("suggestion_count", "skipped_suggestion_count"):
            value = result.get(key)
            if isinstance(value, int):
                extras[key] = value
        families = result.get("suggestion_families")
        if isinstance(families, dict) and families:
            extras["suggestion_families"] = families
        return extras
    return {}


def _log_command(
    logger, command: str, started: float, *, exit_code: int, **extras,
) -> None:
    """Emit one structured summary line per CLI invocation.

    Called at the boundary of ``main()`` so each hook / scheduler / manual
    CLI call leaves exactly one record behind. Per-step logs inside compile /
    reviewer / scan are intentionally NOT added here — start with the
    boundary and push inward only if an investigation actually needs it.
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

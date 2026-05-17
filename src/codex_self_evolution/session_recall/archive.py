from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from ..config import get_home_dir
from ..session_reflection.guard import evaluate_archive_guard
from .parser import parse_claude_jsonl, parse_codex_jsonl
from .store import SessionRecallStore

DISCOVERY_MTIME_WINDOW_SECONDS = 15 * 60
DISCOVERY_MAX_RECENT_FILES = 80


def default_db_path(home: str | Path | None = None, state_dir: str | Path | None = None) -> Path:
    if state_dir:
        return Path(state_dir).expanduser().resolve() / "session_recall" / "state.db"
    root = Path(home).expanduser().resolve() if home else get_home_dir()
    return root / "session_recall" / "state.db"


def archive_transcript(
    transcript_path: str | Path,
    *,
    session_id: str,
    cwd: str,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    store = SessionRecallStore(db_path or default_db_path())
    try:
        parsed = parse_codex_jsonl(transcript_path, session_id=session_id, cwd=cwd)
        result = store.archive(parsed)
        return result
    except Exception as exc:  # noqa: BLE001 - archive is best-effort at hook boundary.
        store.record_error(source_path=str(transcript_path), session_id=session_id, cwd=cwd, error=f"{type(exc).__name__}: {exc}")
        return {
            "status": "error",
            "session_id": session_id,
            "message_count": 0,
            "error": f"{type(exc).__name__}: {exc}",
            "db_path": str(store.db_path),
        }
    finally:
        store.close()


def archive_claude_transcript(
    transcript_path: str | Path,
    *,
    session_id: str = "",
    cwd: str = "",
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    store = SessionRecallStore(db_path or default_db_path())
    try:
        parsed = parse_claude_jsonl(transcript_path, session_id=session_id, cwd=cwd)
        result = store.archive(parsed)
        return result
    except Exception as exc:  # noqa: BLE001 - archive is best-effort at ingest boundary.
        store.record_error(source_path=str(transcript_path), session_id=session_id, cwd=cwd, error=f"{type(exc).__name__}: {exc}")
        return {
            "status": "error",
            "session_id": session_id,
            "message_count": 0,
            "error": f"{type(exc).__name__}: {exc}",
            "db_path": str(store.db_path),
        }
    finally:
        store.close()


def archive_from_hook_payload(
    payload_path: str | Path,
    *,
    db_path: str | Path | None = None,
    cleanup_payload: bool = False,
    sessions_root: str | Path | None = None,
) -> dict[str, Any]:
    path = Path(payload_path).expanduser().resolve()
    resolved_db_path = Path(db_path).expanduser().resolve() if db_path else default_db_path()
    home = _home_from_db_path(resolved_db_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        store = SessionRecallStore(resolved_db_path)
        try:
            store.record_error(source_path=str(path), error=f"{type(exc).__name__}: {exc}")
        finally:
            store.close()
        return {"status": "error", "session_id": "", "message_count": 0, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if cleanup_payload:
            try:
                path.unlink()
            except OSError:
                pass
    if not isinstance(payload, dict):
        store = SessionRecallStore(resolved_db_path)
        try:
            store.record_error(source_path=str(path), error="payload is not an object")
        finally:
            store.close()
        return {"status": "error", "session_id": "", "message_count": 0, "error": "payload is not an object"}
    session_id = str(payload.get("session_id") or payload.get("thread_id") or "unknown-session")
    transcript_path = str(payload.get("transcript_path") or payload.get("codex_transcript_path") or "")
    cwd = str(payload.get("cwd") or ".")

    guard = evaluate_archive_guard(payload, home=home)
    if guard.skip:
        return {
            "status": "skipped",
            "reason": guard.reason,
            "detail": guard.detail,
            "session_id": session_id,
            "message_count": 0,
            "db_path": str(resolved_db_path),
        }

    if not transcript_path:
        discovery = discover_transcript_for_hook_payload(payload, sessions_root=sessions_root)
        if discovery["status"] == "found":
            discovered_guard = evaluate_archive_guard({**payload, "transcript_path": discovery["path"]}, home=home)
            if discovered_guard.skip:
                return {
                    "status": "skipped",
                    "reason": discovered_guard.reason,
                    "detail": discovered_guard.detail,
                    "session_id": session_id,
                    "message_count": 0,
                    "db_path": str(resolved_db_path),
                    "discovery_status": discovery["status"],
                    "discovery_reason": discovery["reason"],
                    "discovered_transcript_path": discovery["path"],
                }
            result = archive_transcript(
                discovery["path"],
                session_id=session_id,
                cwd=cwd,
                db_path=resolved_db_path,
            )
            result["discovery_status"] = discovery["status"]
            result["discovery_reason"] = discovery["reason"]
            result["discovered_transcript_path"] = discovery["path"]
            return result

        store = SessionRecallStore(resolved_db_path)
        error = f"missing transcript_path; {discovery['status']}"
        if discovery.get("candidate_count"):
            error += f"; candidates={discovery['candidate_count']}"
        keys = ",".join(sorted(str(key) for key in payload.keys()))
        if keys:
            error += f"; payload_keys={keys}"
        try:
            store.record_error(session_id=session_id, cwd=cwd, error=error)
        finally:
            store.close()
        return {"status": "error", "session_id": session_id, "message_count": 0, "error": error}
    return archive_transcript(transcript_path, session_id=session_id, cwd=cwd, db_path=resolved_db_path)


def discover_transcript_for_hook_payload(
    payload: dict[str, Any],
    *,
    sessions_root: str | Path | None = None,
) -> dict[str, Any]:
    """Find a high-confidence transcript for a Stop payload missing transcript_path."""
    root = Path(sessions_root).expanduser().resolve() if sessions_root else Path.home() / ".codex" / "sessions"
    if not root.exists():
        return {"status": "discovery_not_found", "reason": "sessions_root_missing", "path": "", "candidate_count": 0}

    session_id = str(payload.get("session_id") or payload.get("thread_id") or "")
    if session_id:
        matches = _unique_existing_files(path for path in root.rglob("*.jsonl") if session_id in path.name)
        if len(matches) == 1:
            return _discovered(matches[0], "filename_session_id")
        if len(matches) > 1:
            return {
                "status": "discovery_ambiguous",
                "reason": "filename_session_id",
                "path": "",
                "candidate_count": len(matches),
            }

    recent_files = _recent_transcripts(root)
    if session_id:
        meta_matches = [
            path for path in recent_files
            if _session_meta(path).get("id") == session_id
        ]
        if len(meta_matches) == 1:
            return _discovered(meta_matches[0], "meta_session_id")
        if len(meta_matches) > 1:
            return {
                "status": "discovery_ambiguous",
                "reason": "meta_session_id",
                "path": "",
                "candidate_count": len(meta_matches),
            }

    cwd = str(payload.get("cwd") or "")
    if cwd:
        cwd_matches = [
            path for path in recent_files
            if _same_path(str(_session_meta(path).get("cwd") or ""), cwd)
        ]
        if len(cwd_matches) == 1:
            return _discovered(cwd_matches[0], "recent_cwd_unique")
        if len(cwd_matches) > 1:
            return {
                "status": "discovery_ambiguous",
                "reason": "recent_cwd_unique",
                "path": "",
                "candidate_count": len(cwd_matches),
            }

    return {"status": "discovery_not_found", "reason": "no_high_confidence_match", "path": "", "candidate_count": 0}


def backfill_sessions(
    root: str | Path | None = None,
    *,
    cwd: str | None = None,
    since_days: int | None = None,
    limit_files: int | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    root_path = Path(root).expanduser().resolve() if root else Path.home() / ".codex" / "sessions"
    resolved_db_path = Path(db_path).expanduser().resolve() if db_path else default_db_path()
    started_at = _utc_timestamp()
    cutoff = None
    if since_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=max(0, since_days))
    files = sorted(
        root_path.rglob("*.jsonl"),
        key=lambda path: (_file_mtime(path), str(path)),
        reverse=True,
    ) if root_path.exists() else []
    if cutoff is not None:
        files = [
            path for path in files
            if datetime.fromtimestamp(_file_mtime(path), UTC) >= cutoff
        ]
    if limit_files is not None:
        files = files[: max(0, limit_files)]
    processed = 0
    successful = 0
    new_sessions = 0
    updated_sessions = 0
    unchanged_sessions = 0
    errors = 0
    for path in files:
        processed += 1
        session_id = _session_id_from_filename(path)
        result = archive_transcript(path, session_id=session_id, cwd=cwd or str(path.parent), db_path=resolved_db_path)
        if result.get("status") == "archived":
            successful += 1
            if result.get("new_session"):
                new_sessions += 1
            elif result.get("updated_session"):
                updated_sessions += 1
            elif result.get("unchanged_session"):
                unchanged_sessions += 1
        else:
            errors += 1
    finished_at = _utc_timestamp()
    store = SessionRecallStore(resolved_db_path)
    try:
        store.record_ingest_run(
            source="backfill",
            root=str(root_path),
            since_days=since_days,
            limit_files=limit_files,
            processed_files=processed,
            successful_files=successful,
            new_sessions=new_sessions,
            updated_sessions=updated_sessions,
            unchanged_sessions=unchanged_sessions,
            error_count=errors,
            started_at=started_at,
            finished_at=finished_at,
        )
    finally:
        store.close()
    return {
        "status": "completed",
        "root": str(root_path),
        "processed_files": processed,
        "processed_successfully": successful,
        "archived_sessions": successful,
        "new_sessions": new_sessions,
        "updated_sessions": updated_sessions,
        "unchanged_sessions": unchanged_sessions,
        "error_count": errors,
        "started_at": started_at,
        "finished_at": finished_at,
        "db_path": str(resolved_db_path),
    }


def backfill_claude_sessions(
    root: str | Path | None = None,
    *,
    cwd: str | None = None,
    since_days: int | None = None,
    limit_files: int | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    root_path = Path(root).expanduser().resolve() if root else Path.home() / ".claude" / "projects"
    resolved_db_path = Path(db_path).expanduser().resolve() if db_path else default_db_path()
    started_at = _utc_timestamp()
    cutoff = None
    if since_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=max(0, since_days))
    files = sorted(
        root_path.rglob("*.jsonl"),
        key=lambda path: (_file_mtime(path), str(path)),
        reverse=True,
    ) if root_path.exists() else []
    if cutoff is not None:
        files = [
            path for path in files
            if datetime.fromtimestamp(_file_mtime(path), UTC) >= cutoff
        ]
    if limit_files is not None:
        files = files[: max(0, limit_files)]
    processed = 0
    successful = 0
    new_sessions = 0
    updated_sessions = 0
    unchanged_sessions = 0
    errors = 0
    for path in files:
        processed += 1
        result = archive_claude_transcript(path, cwd=cwd or "", db_path=resolved_db_path)
        if result.get("status") == "archived":
            successful += 1
            if result.get("new_session"):
                new_sessions += 1
            elif result.get("updated_session"):
                updated_sessions += 1
            elif result.get("unchanged_session"):
                unchanged_sessions += 1
        else:
            errors += 1
    finished_at = _utc_timestamp()
    store = SessionRecallStore(resolved_db_path)
    try:
        store.record_ingest_run(
            source="claude_code",
            root=str(root_path),
            since_days=since_days,
            limit_files=limit_files,
            processed_files=processed,
            successful_files=successful,
            new_sessions=new_sessions,
            updated_sessions=updated_sessions,
            unchanged_sessions=unchanged_sessions,
            error_count=errors,
            started_at=started_at,
            finished_at=finished_at,
        )
    finally:
        store.close()
    return {
        "status": "completed",
        "root": str(root_path),
        "source": "claude_code",
        "processed_files": processed,
        "processed_successfully": successful,
        "archived_sessions": successful,
        "new_sessions": new_sessions,
        "updated_sessions": updated_sessions,
        "unchanged_sessions": unchanged_sessions,
        "error_count": errors,
        "started_at": started_at,
        "finished_at": finished_at,
        "db_path": str(resolved_db_path),
    }


def _session_id_from_filename(path: Path) -> str:
    stem = path.stem
    parts = stem.split("-")
    if len(parts) >= 8:
        return "-".join(parts[-5:])
    return stem


def _home_from_db_path(db_path: Path) -> Path:
    """Infer CSEP home from the standard session_recall/state.db path."""
    if db_path.name == "state.db" and db_path.parent.name == "session_recall":
        return db_path.parent.parent
    return get_home_dir()


def _unique_existing_files(paths: Iterable[Path]) -> list[Path]:
    return sorted({path.expanduser().resolve() for path in paths if path.is_file()})


def _recent_transcripts(root: Path) -> list[Path]:
    now = time.time()
    files = [
        path for path in root.rglob("*.jsonl")
        if path.is_file() and now - _file_mtime(path) <= DISCOVERY_MTIME_WINDOW_SECONDS
    ]
    return sorted(files, key=lambda path: (_file_mtime(path), str(path)), reverse=True)[:DISCOVERY_MAX_RECENT_FILES]


def _session_meta(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for idx, raw in enumerate(handle):
                if idx >= 32:
                    break
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(entry, dict) and entry.get("type") == "session_meta":
                    payload = entry.get("payload")
                    return payload if isinstance(payload, dict) else {}
    except OSError:
        return {}
    return {}


def _same_path(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return Path(left).expanduser().resolve(strict=False) == Path(right).expanduser().resolve(strict=False)


def _discovered(path: Path, reason: str) -> dict[str, Any]:
    return {
        "status": "found",
        "reason": reason,
        "path": str(path),
        "candidate_count": 1,
    }


def _file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

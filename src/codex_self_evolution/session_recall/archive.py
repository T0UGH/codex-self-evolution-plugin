from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import get_home_dir
from .parser import parse_codex_jsonl
from .store import SessionRecallStore


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


def archive_from_hook_payload(
    payload_path: str | Path,
    *,
    db_path: str | Path | None = None,
    cleanup_payload: bool = False,
) -> dict[str, Any]:
    path = Path(payload_path).expanduser().resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        store = SessionRecallStore(db_path or default_db_path())
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
        store = SessionRecallStore(db_path or default_db_path())
        try:
            store.record_error(source_path=str(path), error="payload is not an object")
        finally:
            store.close()
        return {"status": "error", "session_id": "", "message_count": 0, "error": "payload is not an object"}
    session_id = str(payload.get("session_id") or payload.get("thread_id") or "unknown-session")
    transcript_path = str(payload.get("transcript_path") or payload.get("codex_transcript_path") or "")
    cwd = str(payload.get("cwd") or ".")
    if not transcript_path:
        store = SessionRecallStore(db_path or default_db_path())
        try:
            store.record_error(session_id=session_id, cwd=cwd, error="missing transcript_path")
        finally:
            store.close()
        return {"status": "error", "session_id": session_id, "message_count": 0, "error": "missing transcript_path"}
    return archive_transcript(transcript_path, session_id=session_id, cwd=cwd, db_path=db_path)


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


def _session_id_from_filename(path: Path) -> str:
    stem = path.stem
    parts = stem.split("-")
    if len(parts) >= 8:
        return "-".join(parts[-5:])
    return stem


def _file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

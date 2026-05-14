from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import ParsedMessage, ParsedSession

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    session_path TEXT,
    source TEXT NOT NULL DEFAULT 'codex_jsonl',
    cwd TEXT,
    repo_root TEXT,
    repo_fingerprint TEXT,
    worktree_root TEXT,
    git_branch TEXT,
    source_missing INTEGER DEFAULT 0,
    started_at TEXT,
    updated_at TEXT,
    message_count INTEGER DEFAULT 0,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    message_uid TEXT NOT NULL,
    message_index INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    raw_json TEXT,
    metadata_json TEXT,
    timestamp TEXT,
    tool_name TEXT,
    raw_event_type TEXT,
    UNIQUE(session_id, message_uid)
);

CREATE INDEX IF NOT EXISTS idx_sessions_repo ON sessions(repo_fingerprint);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_session_index ON messages(session_id, message_index);

CREATE TABLE IF NOT EXISTS ingest_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT,
    session_id TEXT,
    cwd TEXT,
    error TEXT,
    created_at TEXT
);
"""

FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content='messages',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


class SessionRecallStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.executescript(SCHEMA_SQL)
        row = self._conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        if row is None:
            self._conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
        elif int(row["version"]) != SCHEMA_VERSION:
            raise RuntimeError(f"unsupported session recall schema version: {row['version']}")
        self._conn.executescript(FTS_SQL)
        self._conn.commit()

    def archive(self, parsed: ParsedSession) -> dict[str, Any]:
        if not parsed.messages:
            raise ValueError("archive requires at least one readable message")

        now = _utc_now()
        meta = parsed.metadata
        existing = self._conn.execute(
            "SELECT message_count FROM sessions WHERE session_id = ?",
            (parsed.session_id,),
        ).fetchone()
        started_at = str(meta.get("timestamp") or meta.get("started_at") or now)
        self._conn.execute(
            """
            INSERT INTO sessions (
                session_id, session_path, source, cwd, repo_root, repo_fingerprint,
                worktree_root, git_branch, source_missing, started_at, updated_at,
                message_count, metadata_json
            ) VALUES (?, ?, 'codex_jsonl', ?, ?, ?, ?, ?, 0, ?, ?, 0, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                session_path = excluded.session_path,
                cwd = excluded.cwd,
                repo_root = excluded.repo_root,
                repo_fingerprint = excluded.repo_fingerprint,
                worktree_root = excluded.worktree_root,
                git_branch = excluded.git_branch,
                source_missing = 0,
                updated_at = excluded.updated_at,
                metadata_json = excluded.metadata_json
            """,
            (
                parsed.session_id,
                str(parsed.session_path),
                parsed.cwd,
                str(meta.get("repo_root") or parsed.cwd),
                str(meta.get("repo_fingerprint") or ""),
                str(meta.get("worktree_root") or parsed.cwd),
                str(meta.get("git_branch") or ""),
                started_at if existing is None else str(meta.get("started_at") or started_at),
                now,
                json.dumps(meta, ensure_ascii=False, sort_keys=True),
            ),
        )
        inserted = 0
        for msg in parsed.messages:
            before = self._conn.total_changes
            self._conn.execute(
                """
                INSERT OR IGNORE INTO messages (
                    session_id, message_uid, message_index, role, content, raw_json,
                    metadata_json, timestamp, tool_name, raw_event_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msg.session_id,
                    msg.message_uid,
                    msg.message_index,
                    msg.role,
                    msg.content,
                    msg.raw_json,
                    json.dumps(msg.metadata, ensure_ascii=False, sort_keys=True),
                    msg.timestamp,
                    msg.tool_name,
                    msg.raw_event_type,
                ),
            )
            if self._conn.total_changes > before:
                inserted += 1

        count = self._conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE session_id = ?",
            (parsed.session_id,),
        ).fetchone()["c"]
        self._conn.execute(
            "UPDATE sessions SET message_count = ?, updated_at = ? WHERE session_id = ?",
            (count, now, parsed.session_id),
        )
        fts_count = self._conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM messages_fts
            JOIN messages m ON m.id = messages_fts.rowid
            WHERE m.session_id = ?
            """,
            (parsed.session_id,),
        ).fetchone()["c"]
        self._conn.commit()
        if fts_count < 1:
            raise RuntimeError("archive did not create searchable FTS rows")
        return {
            "status": "archived",
            "session_id": parsed.session_id,
            "message_count": int(count),
            "inserted_messages": inserted,
            "db_path": str(self.db_path),
        }

    def record_error(self, *, source_path: str = "", session_id: str = "", cwd: str = "", error: str) -> None:
        self._conn.execute(
            "INSERT INTO ingest_errors(source_path, session_id, cwd, error, created_at) VALUES (?, ?, ?, ?, ?)",
            (source_path, session_id, cwd, error, _utc_now()),
        )
        self._conn.commit()

    def search(
        self,
        query: str,
        *,
        repo_fingerprint: str = "",
        global_scope: bool = False,
        limit: int = 3,
        before: int = 3,
        after: int = 5,
        current_session_id: str = "",
    ) -> list[dict[str, Any]]:
        fts_query = _sanitize_fts5_query(query)
        if not fts_query:
            return []
        params: list[Any] = [fts_query]
        where = ["messages_fts MATCH ?"]
        if repo_fingerprint and not global_scope:
            where.append("s.repo_fingerprint = ?")
            params.append(repo_fingerprint)
        if current_session_id:
            where.append("m.session_id != ?")
            params.append(current_session_id)
        sql = f"""
            SELECT
                m.id, m.session_id, m.message_index, m.role, m.content, m.tool_name,
                snippet(messages_fts, 0, '>>>', '<<<', '...', 20) AS snippet,
                bm25(messages_fts) AS fts_rank,
                s.session_path, s.cwd, s.repo_root, s.repo_fingerprint,
                s.worktree_root, s.git_branch, s.updated_at, s.started_at
            FROM messages_fts
            JOIN messages m ON m.id = messages_fts.rowid
            JOIN sessions s ON s.session_id = m.session_id
            WHERE {" AND ".join(where)}
            ORDER BY fts_rank
            LIMIT 50
        """
        try:
            rows = [dict(row) for row in self._conn.execute(sql, params).fetchall()]
        except sqlite3.OperationalError:
            return []
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row["session_id"], []).append(row)

        ranked: list[tuple[float, dict[str, Any]]] = []
        for session_id, hits in grouped.items():
            best = max(hits, key=_hit_score)
            window = self._message_window(session_id, int(best["message_index"]), before=before, after=after)
            result = {
                "session_id": session_id,
                "score": _hit_score(best),
                "matched": best["snippet"],
                "source": best["session_path"],
                "repo": best["repo_root"],
                "cwd": best["cwd"],
                "repo_fingerprint": best["repo_fingerprint"],
                "worktree": best["worktree_root"],
                "branch": best["git_branch"],
                "started_at": best["started_at"],
                "updated_at": best["updated_at"],
                "hit_count": len(hits),
                "other_hits": [
                    {"message_index": h["message_index"], "role": h["role"], "snippet": h["snippet"]}
                    for h in hits
                    if h["id"] != best["id"]
                ],
                "messages": window,
            }
            ranked.append((_hit_score(best), result))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in ranked[: max(1, limit)]]

    def recent(
        self,
        *,
        repo_fingerprint: str = "",
        global_scope: bool = False,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = []
        if repo_fingerprint and not global_scope:
            where.append("repo_fingerprint = ?")
            params.append(repo_fingerprint)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        rows = self._conn.execute(
            f"""
            SELECT session_id, session_path, cwd, repo_root, repo_fingerprint,
                   worktree_root, git_branch, started_at, updated_at, message_count
            FROM sessions
            {where_sql}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            [*params, max(1, limit)],
        ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            preview = self._conn.execute(
                "SELECT role, content, tool_name FROM messages WHERE session_id = ? ORDER BY message_index LIMIT 1",
                (item["session_id"],),
            ).fetchone()
            item["preview"] = _preview(dict(preview)) if preview else ""
            item["matched"] = item["preview"]
            item["source"] = item["session_path"]
            item["repo"] = item["repo_root"]
            item["worktree"] = item["worktree_root"]
            item["branch"] = item["git_branch"]
            results.append(item)
        return results

    def stats(self) -> dict[str, Any]:
        if not self.db_path.exists():
            return {"db_exists": False}
        session_count = self._conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]
        message_count = self._conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        error_count = self._conn.execute("SELECT COUNT(*) AS c FROM ingest_errors").fetchone()["c"]
        latest_error = self._conn.execute(
            "SELECT source_path, session_id, error, created_at FROM ingest_errors ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "db_exists": True,
            "db_path": str(self.db_path),
            "session_count": int(session_count),
            "message_count": int(message_count),
            "ingest_error_count": int(error_count),
            "latest_error": dict(latest_error) if latest_error else None,
        }

    def _message_window(self, session_id: str, message_index: int, *, before: int, after: int) -> list[dict[str, Any]]:
        start = max(0, message_index - max(0, before))
        end = message_index + max(0, after)
        rows = self._conn.execute(
            """
            SELECT message_index, role, content, tool_name, raw_event_type
            FROM messages
            WHERE session_id = ? AND message_index BETWEEN ? AND ?
            ORDER BY message_index
            """,
            (session_id, start, end),
        ).fetchall()
        return [dict(row) for row in rows]


def _hit_score(row: dict[str, Any]) -> float:
    role_weight = {"user": 30.0, "assistant": 25.0, "developer": 18.0, "system": 15.0, "tool": 5.0}.get(
        str(row.get("role") or ""),
        10.0,
    )
    try:
        rank = -float(row.get("fts_rank") or 0.0)
    except (TypeError, ValueError):
        rank = 0.0
    return role_weight + rank


def _preview(row: dict[str, Any], limit: int = 160) -> str:
    label = str(row.get("role") or "message")
    tool = str(row.get("tool_name") or "")
    content = str(row.get("content") or "").replace("\n", " ").strip()
    if len(content) > limit:
        content = content[: limit - 15].rstrip() + " [truncated]"
    prefix = f"{label}:{tool}" if tool else label
    return f"[{prefix}] {content}"


def _sanitize_fts5_query(query: str) -> str:
    cleaned = str(query or "").strip()
    cleaned = re.sub(r"[^\S\r\n]+", " ", cleaned)
    cleaned = re.sub(r"[+{}()^]", " ", cleaned)
    cleaned = re.sub(r"\*+", "*", cleaned)
    cleaned = re.sub(r"(^|\s)\*", r"\1", cleaned)
    cleaned = re.sub(r"\b(\w+(?:[.-]\w+)+)\b", r'"\1"', cleaned)
    cleaned = re.sub(r"(?i)^(AND|OR|NOT)\b\s*", "", cleaned.strip())
    cleaned = re.sub(r"(?i)\s+(AND|OR|NOT)\s*$", "", cleaned.strip())
    return cleaned.strip()


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

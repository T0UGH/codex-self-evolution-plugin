from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import ParsedMessage, ParsedSession

SCHEMA_VERSION = 1
DISPLAY_ROLES = {"user", "assistant", "tool"}
BACKGROUND_ROLES = {"developer", "system"}

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

CREATE TABLE IF NOT EXISTS ingest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,
    root TEXT,
    since_days INTEGER,
    limit_files INTEGER,
    processed_files INTEGER DEFAULT 0,
    successful_files INTEGER DEFAULT 0,
    new_sessions INTEGER DEFAULT 0,
    updated_sessions INTEGER DEFAULT 0,
    unchanged_sessions INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    metadata_json TEXT
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

        archived_at = _utc_now()
        meta = parsed.metadata
        existing = self._conn.execute(
            "SELECT message_count FROM sessions WHERE session_id = ?",
            (parsed.session_id,),
        ).fetchone()
        source_updated_at = str(meta.get("source_updated_at") or archived_at)
        started_at = str(meta.get("timestamp") or meta.get("started_at") or source_updated_at)
        source = str(meta.get("source") or "codex_jsonl")
        metadata_json = json.dumps({**meta, "archived_at": archived_at}, ensure_ascii=False, sort_keys=True)
        self._conn.execute(
            """
            INSERT INTO sessions (
                session_id, session_path, source, cwd, repo_root, repo_fingerprint,
                worktree_root, git_branch, source_missing, started_at, updated_at,
                message_count, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 0, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                session_path = excluded.session_path,
                source = excluded.source,
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
                source,
                parsed.cwd,
                str(meta.get("repo_root") or parsed.cwd),
                str(meta.get("repo_fingerprint") or ""),
                str(meta.get("worktree_root") or parsed.cwd),
                str(meta.get("git_branch") or ""),
                started_at if existing is None else str(meta.get("started_at") or started_at),
                source_updated_at,
                metadata_json,
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
            (count, source_updated_at, parsed.session_id),
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
            "new_session": existing is None,
            "updated_session": existing is not None and inserted > 0,
            "unchanged_session": existing is not None and inserted == 0,
            "source_updated_at": source_updated_at,
            "archived_at": archived_at,
            "db_path": str(self.db_path),
        }

    def record_error(self, *, source_path: str = "", session_id: str = "", cwd: str = "", error: str) -> None:
        self._conn.execute(
            "INSERT INTO ingest_errors(source_path, session_id, cwd, error, created_at) VALUES (?, ?, ?, ?, ?)",
            (source_path, session_id, cwd, error, _utc_now()),
        )
        self._conn.commit()

    def record_ingest_run(
        self,
        *,
        source: str,
        root: str,
        since_days: int | None,
        limit_files: int | None,
        processed_files: int,
        successful_files: int,
        new_sessions: int,
        updated_sessions: int,
        unchanged_sessions: int,
        error_count: int,
        started_at: str,
        finished_at: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO ingest_runs (
                source, root, since_days, limit_files, processed_files, successful_files,
                new_sessions, updated_sessions, unchanged_sessions, error_count,
                started_at, finished_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source,
                root,
                since_days,
                limit_files,
                processed_files,
                successful_files,
                new_sessions,
                updated_sessions,
                unchanged_sessions,
                error_count,
                started_at,
                finished_at,
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
            ),
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
        all_terms: bool = False,
        windows_per_session: int = 2,
        include_background: bool = False,
    ) -> list[dict[str, Any]]:
        query = str(query or "").strip()
        if not query:
            return []
        rows: list[dict[str, Any]] = []
        query_mode = "strict"
        for fts_query, mode in _candidate_fts_queries(query, all_terms=all_terms):
            rows = self._search_fts_rows(
                fts_query,
                repo_fingerprint=repo_fingerprint,
                global_scope=global_scope,
                current_session_id=current_session_id,
            )
            if rows:
                query_mode = mode
                break
        if not rows and not all_terms and not _has_explicit_fts(query):
            rows = self._search_like_rows(
                _query_needles(query),
                repo_fingerprint=repo_fingerprint,
                global_scope=global_scope,
                current_session_id=current_session_id,
            )
            if rows:
                query_mode = "like_fallback"
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row["session_id"], []).append(row)

        ranked: list[tuple[float, dict[str, Any]]] = []
        for session_id, hits in grouped.items():
            anchors = _select_anchor_hits(
                hits,
                before=before,
                after=after,
                windows_per_session=windows_per_session,
                include_background=include_background,
            )
            best = anchors[0] if anchors else max(hits, key=_hit_score)
            windows = [
                {
                    "matched": anchor["snippet"],
                    "anchor": {
                        "message_index": anchor["message_index"],
                        "role": anchor["role"],
                        "tool_name": anchor.get("tool_name") or "",
                        "matched_by": anchor.get("matched_by") or query_mode,
                    },
                    "messages": self._message_window(
                        session_id,
                        int(anchor["message_index"]),
                        before=before,
                        after=after,
                        include_background=include_background,
                    ),
                }
                for anchor in anchors
            ]
            result = {
                "session_id": session_id,
                "score": _session_score(hits),
                "query_mode": query_mode,
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
                "windows": windows,
                "messages": windows[0]["messages"] if windows else [],
            }
            ranked.append((_session_score(hits), result))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in ranked[: max(1, limit)]]

    def _search_fts_rows(
        self,
        fts_query: str,
        *,
        repo_fingerprint: str,
        global_scope: bool,
        current_session_id: str,
    ) -> list[dict[str, Any]]:
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
                'fts' AS matched_by,
                s.session_path, s.cwd, s.repo_root, s.repo_fingerprint,
                s.worktree_root, s.git_branch, s.updated_at, s.started_at
            FROM messages_fts
            JOIN messages m ON m.id = messages_fts.rowid
            JOIN sessions s ON s.session_id = m.session_id
            WHERE {" AND ".join(where)}
            ORDER BY fts_rank
            LIMIT 150
        """
        try:
            return [dict(row) for row in self._conn.execute(sql, params).fetchall()]
        except sqlite3.OperationalError:
            return []

    def _search_like_rows(
        self,
        needles: list[str],
        *,
        repo_fingerprint: str,
        global_scope: bool,
        current_session_id: str,
    ) -> list[dict[str, Any]]:
        needles = [needle for needle in needles if needle][:8]
        if not needles:
            return []
        params: list[Any] = []
        needle_clauses: list[str] = []
        for needle in needles:
            needle_clauses.append("(m.content LIKE ? OR m.tool_name LIKE ?)")
            pattern = f"%{needle}%"
            params.extend([pattern, pattern])
        where = [f"({' OR '.join(needle_clauses)})"]
        if repo_fingerprint and not global_scope:
            where.append("s.repo_fingerprint = ?")
            params.append(repo_fingerprint)
        if current_session_id:
            where.append("m.session_id != ?")
            params.append(current_session_id)
        rows = self._conn.execute(
            f"""
            SELECT
                m.id, m.session_id, m.message_index, m.role, m.content, m.tool_name,
                0.0 AS fts_rank,
                'like' AS matched_by,
                s.session_path, s.cwd, s.repo_root, s.repo_fingerprint,
                s.worktree_root, s.git_branch, s.updated_at, s.started_at
            FROM messages m
            JOIN sessions s ON s.session_id = m.session_id
            WHERE {" AND ".join(where)}
            ORDER BY s.updated_at DESC, m.message_index
            LIMIT 150
            """,
            params,
        ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["snippet"] = _content_snippet(str(item.get("content") or ""), needles)
            output.append(item)
        return output

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
                   worktree_root, git_branch, started_at, updated_at, message_count,
                   metadata_json
            FROM sessions
            {where_sql}
            ORDER BY COALESCE(started_at, updated_at) DESC, updated_at DESC
            LIMIT ?
            """,
            [*params, max(1, limit)],
        ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            metadata = _decode_json_object(str(item.pop("metadata_json") or ""))
            item["source_updated_at"] = metadata.get("source_updated_at") or item["updated_at"]
            item["archived_at"] = metadata.get("archived_at") or item["updated_at"]
            preview = self._conn.execute(
                """
                SELECT role, content, tool_name
                FROM messages
                WHERE session_id = ?
                  AND NOT (role = 'user' AND content LIKE '# AGENTS.md instructions%')
                  AND NOT (role = 'user' AND content LIKE '<environment_context>%')
                ORDER BY
                    CASE role
                        WHEN 'user' THEN 0
                        WHEN 'assistant' THEN 1
                        WHEN 'tool' THEN 2
                        ELSE 3
                    END,
                    message_index
                LIMIT 1
                """,
                (item["session_id"],),
            ).fetchone()
            if preview is None:
                preview = self._conn.execute(
                    """
                    SELECT role, content, tool_name
                    FROM messages
                    WHERE session_id = ?
                    ORDER BY message_index
                    LIMIT 1
                    """,
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
        latest_run = self._conn.execute(
            """
            SELECT source, root, since_days, limit_files, processed_files, successful_files,
                   new_sessions, updated_sessions, unchanged_sessions, error_count,
                   started_at, finished_at
            FROM ingest_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        return {
            "db_exists": True,
            "db_path": str(self.db_path),
            "session_count": int(session_count),
            "message_count": int(message_count),
            "ingest_error_count": int(error_count),
            "ingest_error_count_total": int(error_count),
            "latest_error": dict(latest_error) if latest_error else None,
            "latest_ingest_run": dict(latest_run) if latest_run else None,
        }

    def _message_window(
        self,
        session_id: str,
        message_index: int,
        *,
        before: int,
        after: int,
        include_background: bool = False,
    ) -> list[dict[str, Any]]:
        start = max(0, message_index - max(0, before))
        end = message_index + max(0, after)
        role_filter = "" if include_background else "AND role NOT IN ('developer', 'system')"
        rows = self._conn.execute(
            f"""
            SELECT message_index, role, content, tool_name, raw_event_type
            FROM messages
            WHERE session_id = ? AND message_index BETWEEN ? AND ?
            {role_filter}
            ORDER BY message_index
            """,
            (session_id, start, end),
        ).fetchall()
        return [dict(row) for row in rows]


def _hit_score(row: dict[str, Any]) -> float:
    role_weight = {"user": 30.0, "assistant": 25.0, "tool": 20.0, "developer": 2.0, "system": 1.0}.get(
        str(row.get("role") or ""),
        10.0,
    )
    try:
        rank = -float(row.get("fts_rank") or 0.0)
    except (TypeError, ValueError):
        rank = 0.0
    return role_weight + rank


def _session_score(hits: list[dict[str, Any]]) -> float:
    best = max((_hit_score(hit) for hit in hits), default=0.0)
    hit_bonus = min(20.0, max(0, len(hits) - 1) * 3.0)
    recency = _recency_score(str(hits[0].get("updated_at") or "")) if hits else 0.0
    return best + hit_bonus + recency


def _recency_score(value: str) -> float:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    age_days = max(0.0, (datetime.now(UTC) - dt).total_seconds() / 86400)
    return max(0.0, 8.0 - min(8.0, age_days / 7))


def _select_anchor_hits(
    hits: list[dict[str, Any]],
    *,
    before: int,
    after: int,
    windows_per_session: int,
    include_background: bool,
) -> list[dict[str, Any]]:
    limit = max(1, int(windows_per_session))
    visible_hits = hits if include_background else [hit for hit in hits if str(hit.get("role") or "") in DISPLAY_ROLES]
    candidates = sorted(visible_hits or hits, key=_hit_score, reverse=True)
    selected: list[dict[str, Any]] = []
    overlap_distance = max(1, before + after + 1)
    for hit in candidates:
        idx = int(hit.get("message_index") or 0)
        if any(abs(idx - int(existing.get("message_index") or 0)) <= overlap_distance for existing in selected):
            continue
        selected.append(hit)
        if len(selected) >= limit:
            break
    if not selected and hits:
        selected.append(max(hits, key=_hit_score))
    selected.sort(key=lambda hit: int(hit.get("message_index") or 0))
    return selected


def _preview(row: dict[str, Any], limit: int = 160) -> str:
    label = str(row.get("role") or "message")
    tool = str(row.get("tool_name") or "")
    content = str(row.get("content") or "").replace("\n", " ").strip()
    if len(content) > limit:
        content = content[: limit - 15].rstrip() + " [truncated]"
    prefix = f"{label}:{tool}" if tool else label
    return f"[{prefix}] {content}"


def _candidate_fts_queries(query: str, *, all_terms: bool) -> list[tuple[str, str]]:
    if "|" in query:
        or_query = _or_fts_query(_query_needles(query))
        return [(or_query, "or_alias")] if or_query else []
    strict = _sanitize_fts5_query(query)
    if all_terms or _has_explicit_fts(query):
        return [(strict, "strict")] if strict else []
    candidates = [(strict, "strict")] if strict else []
    or_query = _or_fts_query(_query_needles(query))
    if or_query and or_query != strict:
        candidates.append((or_query, "or_fallback"))
    return candidates


def _has_explicit_fts(query: str) -> bool:
    return bool(re.search(r'["*]|\b(?:AND|OR|NOT)\b', query, re.IGNORECASE))


def _query_needles(query: str) -> list[str]:
    text = str(query or "").strip()
    if "|" in text:
        raw_parts = text.split("|")
    else:
        raw_parts = [match[0] or match[1] for match in re.findall(r'"([^"]+)"|(\S+)', text)]
    needles: list[str] = []
    for part in raw_parts:
        cleaned = _clean_needle(part)
        if cleaned and cleaned.upper() not in {"AND", "OR", "NOT"} and cleaned not in needles:
            needles.append(cleaned)
    return needles


def _clean_needle(value: str) -> str:
    cleaned = str(value or "").strip().strip('"').strip("'")
    cleaned = re.sub(r"[+{}()^]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _or_fts_query(needles: list[str]) -> str:
    fragments = [_quote_fts_fragment(needle) for needle in needles]
    fragments = [fragment for fragment in fragments if fragment]
    return " OR ".join(fragments)


def _quote_fts_fragment(fragment: str) -> str:
    cleaned = _clean_needle(fragment).replace('"', " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""
    if cleaned.endswith("*") and re.fullmatch(r"[\w.-]+\*", cleaned):
        return _sanitize_fts5_query(cleaned)
    return f'"{cleaned}"'


def _content_snippet(content: str, needles: list[str], *, radius: int = 70) -> str:
    folded = content.replace("\n", " ")
    lower = folded.lower()
    match_at = -1
    match_len = 0
    for needle in needles:
        idx = lower.find(needle.lower())
        if idx >= 0 and (match_at < 0 or idx < match_at):
            match_at = idx
            match_len = len(needle)
    if match_at < 0:
        return _preview({"role": "message", "content": folded}, limit=160)
    start = max(0, match_at - radius)
    end = min(len(folded), match_at + match_len + radius)
    prefix = "..." if start else ""
    suffix = "..." if end < len(folded) else ""
    return f"{prefix}{folded[start:match_at]}>>>{folded[match_at:match_at + match_len]}<<<{folded[match_at + match_len:end]}{suffix}"


def _decode_json_object(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


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

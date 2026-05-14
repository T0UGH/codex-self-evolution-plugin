# Hermes Session Recall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现第一版 Hermes 风格 session recall：Stop hook 归档 Codex transcript 到本机 SQLite/FTS，`csep recall` 从归档库返回受预算控制的原始消息窗口，并保留旧 `recall/index.json` fallback。

**Architecture:** 新增 `codex_self_evolution.session_recall` 包，集中管理 SQLite schema、Codex jsonl parser、archive/backfill、FTS search 和 Markdown/JSON rendering。`csep` 暴露 `session-archive`、`session-ingest --backfill` 和增强后的 `recall`；`codex-self-evolution stop-review --from-stdin` 只负责额外 spawn 一个独立 detached `session-archive` 子进程，不等待、不重试、不阻断现有 stop-review。配置新增 `[session_recall]`，默认启用，旧 recall 行为作为软 fallback。

**Tech Stack:** Python 3.11+ stdlib only, `sqlite3` FTS5, `argparse`, existing config/logging/storage helpers, pytest via `uv run --extra dev pytest -q`.

---

## File Structure

- Create: `src/codex_self_evolution/session_recall/__init__.py`
  - Re-export public archive/search functions used by CLI and tests.
- Create: `src/codex_self_evolution/session_recall/models.py`
  - Dataclasses: `SessionArchiveInput`, `ParsedSession`, `ParsedMessage`, `ArchiveResult`, `RecallQuery`, `RecallResult`.
- Create: `src/codex_self_evolution/session_recall/repo.py`
  - Worktree-aware repo metadata helpers: repo root, git common dir fingerprint, worktree root, branch.
- Create: `src/codex_self_evolution/session_recall/parser.py`
  - Best-effort Codex jsonl parser; extracts readable messages, `message_uid`, raw json, metadata.
- Create: `src/codex_self_evolution/session_recall/store.py`
  - SQLite schema versioning, upsert archive, FTS5, search, recent listing.
- Create: `src/codex_self_evolution/session_recall/archive.py`
  - Orchestrates single-session archive and backfill; writes ingest errors without retry queue.
- Create: `src/codex_self_evolution/session_recall/render.py`
  - Budgeted Markdown/JSON shape, output truncation, secret-like redaction.
- Create: `tests/test_session_recall_parser.py`
- Create: `tests/test_session_recall_store.py`
- Create: `tests/test_session_recall_archive.py`
- Create: `tests/test_session_recall_cli.py`
- Modify: `src/codex_self_evolution/config_file.py`
  - Add `SessionRecallConfig`, parse `[session_recall]`, expose sources.
- Modify: `src/codex_self_evolution/config_file_template.py`
  - Add documented `[session_recall]` block.
- Modify: `src/codex_self_evolution/cli.py`
  - Stop hook spawns independent `session-archive` child when enabled.
- Modify: `src/codex_self_evolution/csep.py`
  - Add `session-archive`, `session-ingest --backfill`, `recall --recent`, budgets, `--global`.
- Modify: `src/codex_self_evolution/recall/workflow.py`
  - Prefer session recall store; fallback to current `search_recall()`.
- Modify: `src/codex_self_evolution/diagnostics.py`
  - Surface archive counts / latest errors in `status`.
- Modify: tests around config, bridge, csep CLI, diagnostics.

## Task 1: Config Surface

**Files:**
- Modify: `src/codex_self_evolution/config_file.py`
- Modify: `src/codex_self_evolution/config_file_template.py`
- Test: `tests/test_config_file.py`

- [ ] **Step 1: Write failing config tests**

Append tests that assert defaults and TOML overrides:

```python
def test_session_recall_config_defaults(tmp_path):
    from codex_self_evolution.config_file import load_config

    result = load_config(home=tmp_path, env={})

    assert result.config.session_recall.enabled is True
    assert result.config.session_recall.stop_hook_archive is True
    assert result.sources["session_recall.enabled"] == "default"
    assert result.sources["session_recall.stop_hook_archive"] == "default"


def test_session_recall_config_reads_toml(tmp_path):
    from codex_self_evolution.config_file import load_config

    (tmp_path / "config.toml").write_text(
        "schema_version = 2\n\n"
        "[session_recall]\n"
        "enabled = false\n"
        "stop_hook_archive = false\n",
        encoding="utf-8",
    )

    result = load_config(home=tmp_path, env={})

    assert result.config.session_recall.enabled is False
    assert result.config.session_recall.stop_hook_archive is False
    assert result.sources["session_recall.enabled"] == "config.toml"
    assert result.sources["session_recall.stop_hook_archive"] == "config.toml"
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
uv run --extra dev pytest -q tests/test_config_file.py::test_session_recall_config_defaults tests/test_config_file.py::test_session_recall_config_reads_toml
```

Expected: fail because `PluginConfig` has no `session_recall`.

- [ ] **Step 3: Implement config dataclass and parsing**

Add near other config dataclasses:

```python
@dataclass
class SessionRecallConfig:
    enabled: bool = True
    stop_hook_archive: bool = True
```

Add to `PluginConfig`:

```python
session_recall: SessionRecallConfig = field(default_factory=SessionRecallConfig)
```

In `load_config()` after scheduler or skill_synthesis parsing:

```python
    session_recall_toml = raw_toml.get("session_recall", {}) or {}
    sr_enabled = session_recall_toml.get("enabled")
    if isinstance(sr_enabled, bool):
        config.session_recall.enabled = sr_enabled
        sources["session_recall.enabled"] = "config.toml"
    else:
        sources["session_recall.enabled"] = "default"

    sr_stop_hook = session_recall_toml.get("stop_hook_archive")
    if isinstance(sr_stop_hook, bool):
        config.session_recall.stop_hook_archive = sr_stop_hook
        sources["session_recall.stop_hook_archive"] = "config.toml"
    else:
        sources["session_recall.stop_hook_archive"] = "default"
```

Add accepted keys to the unknown-key allowlist:

```python
"session_recall", "session_recall.enabled", "session_recall.stop_hook_archive",
```

- [ ] **Step 4: Update config template**

Add a block after `[scheduler]`:

```toml
# ===========================================================================
# [session_recall] — local durable session archive + FTS recall
# ===========================================================================

[session_recall]
enabled = true
stop_hook_archive = true
```

- [ ] **Step 5: Verify**

Run:

```bash
uv run --extra dev pytest -q tests/test_config_file.py
```

Expected: pass.

## Task 2: Repo Metadata Helpers

**Files:**
- Create: `src/codex_self_evolution/session_recall/repo.py`
- Test: `tests/test_session_recall_store.py`

- [ ] **Step 1: Write tests for non-git and git fallback**

```python
def test_collect_repo_metadata_falls_back_to_cwd(tmp_path):
    from codex_self_evolution.session_recall.repo import collect_repo_metadata

    cwd = tmp_path / "plain"
    cwd.mkdir()

    meta = collect_repo_metadata(cwd)

    assert meta["cwd"] == str(cwd.resolve())
    assert meta["repo_root"] == str(cwd.resolve())
    assert meta["worktree_root"] == str(cwd.resolve())
    assert meta["repo_fingerprint"]
    assert meta["git_branch"] == ""
```

- [ ] **Step 2: Implement helper**

Create `repo.py`:

```python
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def collect_repo_metadata(cwd: str | Path) -> dict[str, str]:
    resolved = Path(cwd).expanduser().resolve()
    worktree = _git(resolved, "rev-parse", "--show-toplevel")
    common = _git(resolved, "rev-parse", "--git-common-dir")
    branch = _git(resolved, "branch", "--show-current")
    origin = _git(resolved, "config", "--get", "remote.origin.url")

    worktree_root = Path(worktree).resolve() if worktree else resolved
    if common:
        common_path = Path(common)
        if not common_path.is_absolute():
            common_path = (worktree_root / common_path).resolve()
        stable = str(common_path)
        repo_root = str(common_path.parent if common_path.name == ".git" else common_path)
    else:
        stable = str(resolved)
        repo_root = str(resolved)

    if origin:
        stable = f"{origin}|{stable}"

    return {
        "cwd": str(resolved),
        "repo_root": repo_root,
        "worktree_root": str(worktree_root),
        "repo_fingerprint": hashlib.sha1(stable.encode("utf-8")).hexdigest(),
        "git_branch": branch,
    }
```

- [ ] **Step 3: Verify**

Run:

```bash
uv run --extra dev pytest -q tests/test_session_recall_store.py::test_collect_repo_metadata_falls_back_to_cwd
```

Expected: pass.

## Task 3: Parser

**Files:**
- Create: `src/codex_self_evolution/session_recall/models.py`
- Create: `src/codex_self_evolution/session_recall/parser.py`
- Test: `tests/test_session_recall_parser.py`

- [ ] **Step 1: Write parser tests**

```python
import json


def test_parse_codex_jsonl_extracts_readable_messages(tmp_path):
    from codex_self_evolution.session_recall.parser import parse_codex_jsonl

    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"id": "s1"}}),
                json.dumps({"role": "user", "content": "请记住这个方案"}),
                json.dumps({"type": "agent_message", "text": "已经记录"}),
                json.dumps({"role": "tool", "tool_name": "exec_command", "content": "pytest failed"}),
                json.dumps({"type": "token_count", "tokens": 100}),
            ]
        ),
        encoding="utf-8",
    )

    parsed = parse_codex_jsonl(transcript, session_id="fallback", cwd=str(tmp_path))

    assert parsed.session_id == "s1"
    assert [m.role for m in parsed.messages] == ["user", "assistant", "tool"]
    assert parsed.messages[0].content == "请记住这个方案"
    assert parsed.messages[2].tool_name == "exec_command"
    assert parsed.messages[2].raw_json.startswith("{")
```

```python
def test_parse_codex_jsonl_uses_raw_line_hash_for_uid(tmp_path):
    from codex_self_evolution.session_recall.parser import parse_codex_jsonl

    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps({"role": "user", "content": "hello"}) + "\n", encoding="utf-8")

    parsed1 = parse_codex_jsonl(transcript, session_id="s1", cwd=str(tmp_path))
    parsed2 = parse_codex_jsonl(transcript, session_id="s1", cwd=str(tmp_path))

    assert parsed1.messages[0].message_uid == parsed2.messages[0].message_uid
```

- [ ] **Step 2: Implement dataclasses**

In `models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ParsedMessage:
    session_id: str
    message_uid: str
    message_index: int
    role: str
    content: str
    raw_json: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    tool_name: str = ""
    raw_event_type: str = ""


@dataclass(frozen=True)
class ParsedSession:
    session_id: str
    session_path: Path
    cwd: str
    messages: list[ParsedMessage]
    metadata: dict[str, Any] = field(default_factory=dict)
```

- [ ] **Step 3: Implement parser**

In `parser.py`, support role/content, `agent_message`, `user_message`, content parts, tool names, and skip empty/telemetry events. Compute `message_uid` from stable ids first, fallback `sha256(session_id + raw_line)`.

- [ ] **Step 4: Verify**

Run:

```bash
uv run --extra dev pytest -q tests/test_session_recall_parser.py
```

Expected: pass.

## Task 4: SQLite Store

**Files:**
- Create: `src/codex_self_evolution/session_recall/store.py`
- Test: `tests/test_session_recall_store.py`

- [ ] **Step 1: Write store tests**

Add tests for schema init, idempotent upsert, FTS search, same repo filter, tool role lower ranking, recent.

```python
def test_store_archives_and_searches_messages(tmp_path):
    from codex_self_evolution.session_recall.models import ParsedMessage, ParsedSession
    from codex_self_evolution.session_recall.store import SessionRecallStore

    store = SessionRecallStore(tmp_path / "state.db")
    parsed = ParsedSession(
        session_id="s1",
        session_path=tmp_path / "s1.jsonl",
        cwd=str(tmp_path),
        metadata={"repo_fingerprint": "repo-a", "repo_root": str(tmp_path), "worktree_root": str(tmp_path), "git_branch": "main"},
        messages=[
            ParsedMessage("s1", "m1", 0, "user", "hermes recall design", "{}", raw_event_type="message"),
            ParsedMessage("s1", "m2", 1, "tool", "hermes noisy stdout", "{}", tool_name="exec_command", raw_event_type="tool"),
        ],
    )

    result = store.archive(parsed)
    assert result["status"] == "archived"

    again = store.archive(parsed)
    assert again["inserted_messages"] == 0

    hits = store.search("hermes", repo_fingerprint="repo-a", limit=3)
    assert hits[0]["session_id"] == "s1"
    assert hits[0]["hit_count"] >= 1
```

- [ ] **Step 2: Implement schema**

Use `sqlite3` with WAL, schema tables:

```sql
schema_version(version INTEGER NOT NULL)
sessions(session_id TEXT PRIMARY KEY, session_path TEXT, source TEXT, cwd TEXT, repo_root TEXT,
repo_fingerprint TEXT, worktree_root TEXT, git_branch TEXT, source_missing INTEGER DEFAULT 0,
started_at TEXT, updated_at TEXT, message_count INTEGER DEFAULT 0, metadata_json TEXT)
messages(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, message_uid TEXT NOT NULL,
message_index INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, raw_json TEXT,
metadata_json TEXT, timestamp TEXT, tool_name TEXT, raw_event_type TEXT,
UNIQUE(session_id, message_uid))
messages_fts(content, content='messages', content_rowid='id')
ingest_errors(id INTEGER PRIMARY KEY AUTOINCREMENT, source_path TEXT, session_id TEXT, cwd TEXT,
error TEXT, created_at TEXT)
```

Add triggers to keep `messages_fts` synced on insert/update/delete.

- [ ] **Step 3: Implement archive upsert**

`archive(parsed)` inserts session row, inserts messages with `INSERT OR IGNORE`, updates message count and timestamps. It fails when `parsed.messages` is empty. It verifies at least one inserted/existing message can be found through FTS for that session.

- [ ] **Step 4: Implement search and recent**

`search(query, repo_fingerprint=None, include_global=False, limit=3, before=3, after=5, current_session_id=None)`:

- sanitize FTS5 query.
- query up to 50 message hits.
- filter repo unless global.
- exclude current session when provided.
- group by session.
- role score: user/assistant above tool.
- recent score small tie breaker.
- fetch one best window per session.

`recent(repo_fingerprint=None, include_global=False, limit=10)` sorts by `updated_at DESC`.

- [ ] **Step 5: Verify**

Run:

```bash
uv run --extra dev pytest -q tests/test_session_recall_store.py
```

Expected: pass.

## Task 5: Archive and Backfill Orchestration

**Files:**
- Create: `src/codex_self_evolution/session_recall/archive.py`
- Create: `src/codex_self_evolution/session_recall/__init__.py`
- Test: `tests/test_session_recall_archive.py`

- [ ] **Step 1: Write archive tests**

Cover hook payload archive, single transcript archive, backfill limits, ingest error on bad path.

```python
def test_archive_from_hook_payload(tmp_path):
    import json
    from codex_self_evolution.session_recall.archive import archive_from_hook_payload

    transcript = tmp_path / "s.jsonl"
    transcript.write_text(json.dumps({"role": "user", "content": "archive me"}) + "\n", encoding="utf-8")
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"session_id": "s1", "transcript_path": str(transcript), "cwd": str(tmp_path)}), encoding="utf-8")

    result = archive_from_hook_payload(payload, db_path=tmp_path / "state.db")

    assert result["status"] == "archived"
    assert result["session_id"] == "s1"
    assert result["message_count"] == 1
```

- [ ] **Step 2: Implement archive functions**

Functions:

```python
def default_db_path(home: Path | None = None) -> Path
def archive_transcript(transcript_path: Path, *, session_id: str, cwd: str, db_path: Path | None = None) -> dict
def archive_from_hook_payload(payload_path: Path, *, db_path: Path | None = None, cleanup_payload: bool = False) -> dict
def backfill_sessions(root: Path | None = None, *, since_days: int | None = None, limit_files: int | None = None, db_path: Path | None = None) -> dict
```

Default DB path: `get_home_dir() / "session_recall" / "state.db"`.

- [ ] **Step 3: Verify**

Run:

```bash
uv run --extra dev pytest -q tests/test_session_recall_archive.py
```

Expected: pass.

## Task 6: CSEP CLI

**Files:**
- Modify: `src/codex_self_evolution/csep.py`
- Modify: `src/codex_self_evolution/recall/workflow.py`
- Create: `src/codex_self_evolution/session_recall/render.py`
- Test: `tests/test_session_recall_cli.py`
- Modify: `tests/test_csep_cli.py`

- [ ] **Step 1: Write CLI tests**

Tests must cover:

- `csep session-archive --transcript-path ... --cwd ... --session-id ...`
- `csep session-ingest --backfill --limit-files 1`
- `csep recall "query"` uses SQLite store when present.
- `csep recall --recent` works without query.
- `--global` crosses repo filter.
- `--budget-chars` truncates output with marker.
- existing fallback test still passes when SQLite missing or disabled.

- [ ] **Step 2: Extend parser**

Change recall query to `nargs="*"` so `--recent` can omit query. Add:

```python
recall.add_argument("--global", dest="global_scope", action="store_true")
recall.add_argument("--recent", action="store_true")
recall.add_argument("--limit", "--top-k", dest="limit", type=int, default=3)
recall.add_argument("--before", type=int, default=3)
recall.add_argument("--after", type=int, default=5)
recall.add_argument("--budget-chars", type=int, default=12000)
recall.add_argument("--message-chars", type=int, default=1200)
recall.add_argument("--tool-message-chars", type=int, default=600)
recall.add_argument("--current-session-id")
```

Add parsers for `session-archive` and `session-ingest --backfill`.

- [ ] **Step 3: Implement rendering**

Markdown starts with:

```text
## Focused Recall
Status: matched
Scope: repo
Results: 3
Budget: 9340/12000 chars
```

Apply per-message and total budget. Tool messages use `tool_message_chars`.

- [ ] **Step 4: Verify**

Run:

```bash
uv run --extra dev pytest -q tests/test_session_recall_cli.py tests/test_csep_cli.py tests/test_recall_search.py
```

Expected: pass.

## Task 7: Stop Hook Integration

**Files:**
- Modify: `src/codex_self_evolution/cli.py`
- Test: `tests/test_codex_bridge.py`

- [ ] **Step 1: Write hook integration tests**

Update `test_cli_from_stdin_spawns_background_reviewer_and_returns_continue` or add a new test that expects two `Popen` calls when session recall is enabled:

```python
def test_cli_from_stdin_spawns_archive_and_reviewer(monkeypatch, capsys, tmp_path):
    calls = []

    class FakePopen:
        def __init__(self, argv, **kwargs):
            calls.append((argv, kwargs))

    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(_codex_payload(cwd=str(tmp_path)))))

    assert cli.main(["stop-review", "--from-stdin"]) == 0

    assert len(calls) == 2
    assert calls[0][0][1:4] == ["-m", "codex_self_evolution.csep", "session-archive"]
    assert "--from-hook-payload" in calls[0][0]
    assert calls[1][0][1:4] == ["-m", "codex_self_evolution.cli", "stop-review"]
    assert all(kwargs.get("start_new_session") is True for _, kwargs in calls)
```

Add a disabled-config test by writing `config.toml` with `[session_recall] stop_hook_archive = false` and assert only reviewer is spawned.

- [ ] **Step 2: Implement spawn helper**

In `cli.py`, add `_spawn_session_archive_from_stop_payload(codex_payload, args, logger) -> None`.

Implementation:

- Load config with `load_config()`.
- If `not config.session_recall.enabled` or `not config.session_recall.stop_hook_archive`, return.
- Write raw Codex payload to its own temp file.
- Spawn:

```python
[
    sys.executable,
    "-m",
    "codex_self_evolution.csep",
    "session-archive",
    "--from-hook-payload",
    archive_tmp,
    "--cleanup-payload",
]
```

- Add `--state-dir` if supplied.
- Log to `/tmp/codex-self-evolution/session-archive-<pid>-<ticks>.log`.
- On `OSError`, log warning but do not alter hook response.

- [ ] **Step 3: Call helper before reviewer spawn**

In `_handle_stop_from_stdin`, after JSON validation and before `map_codex_stop_payload`, call the helper with raw `codex_payload`.

- [ ] **Step 4: Verify**

Run:

```bash
uv run --extra dev pytest -q tests/test_codex_bridge.py
```

Expected: pass.

## Task 8: Diagnostics

**Files:**
- Modify: `src/codex_self_evolution/diagnostics.py`
- Test: `tests/test_diagnostics.py`

- [ ] **Step 1: Write status test**

Add a test that creates a temp session recall DB with one archived session and asserts status includes:

```python
status["session_recall"]["db_exists"] is True
status["session_recall"]["session_count"] == 1
status["session_recall"]["message_count"] == 1
status["session_recall"]["ingest_error_count"] == 0
```

- [ ] **Step 2: Implement collector**

Add `_check_session_recall(home: str | None) -> dict[str, Any]` and include it in `collect_status()`.

It should never raise; on SQLite error, return `{"db_exists": True, "error": "..."}`.

- [ ] **Step 3: Verify**

Run:

```bash
uv run --extra dev pytest -q tests/test_diagnostics.py
```

Expected: pass.

## Task 9: Documentation and Final Verification

**Files:**
- Modify: `README.md`
- Modify: `README_zh.md`
- Modify if needed: `docs/superpowers/specs/2026-05-14-hermes-style-session-recall-design.md`

- [ ] **Step 1: Update docs**

Document:

```bash
csep session-archive --transcript-path <path> --cwd <repo> --session-id <id>
csep session-ingest --backfill
csep recall "hermes OR recall" --global
csep recall --recent
```

Mention `[session_recall] enabled = true` and `stop_hook_archive = true`.

- [ ] **Step 2: Run focused tests**

Run:

```bash
uv run --extra dev pytest -q tests/test_session_recall_parser.py tests/test_session_recall_store.py tests/test_session_recall_archive.py tests/test_session_recall_cli.py tests/test_codex_bridge.py tests/test_config_file.py tests/test_diagnostics.py
```

Expected: all pass.

- [ ] **Step 3: Run full suite**

Run:

```bash
uv run --extra dev pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Whitespace check**

Run:

```bash
/usr/bin/git diff --check
```

Expected: no output.

- [ ] **Step 5: Local smoke**

Use temp state and transcript:

```bash
tmpdir="$(mktemp -d)"
printf '{"role":"user","content":"hermes session recall smoke"}\n' > "$tmpdir/session.jsonl"
CODEX_SELF_EVOLUTION_HOME="$tmpdir/home" uv run csep session-archive --transcript-path "$tmpdir/session.jsonl" --cwd "$PWD" --session-id smoke
CODEX_SELF_EVOLUTION_HOME="$tmpdir/home" uv run csep recall "hermes session recall" --cwd "$PWD"
```

Expected: archive reports success and recall output includes `hermes session recall smoke`.

- [ ] **Step 6: Commit**

```bash
/usr/bin/git status --short
/usr/bin/git add src tests README.md README_zh.md docs/superpowers/plans/2026-05-14-hermes-session-recall.md
/usr/bin/git commit -m "feat: add hermes-style session recall archive"
```

Expected: commit created on `feature/hermes-session-recall`.

## Self-Review

- Spec coverage: the tasks cover config, archive CLI, backfill, Stop hook archive, SQLite/FTS, tool output, budgets, recent, repo/worktree metadata, no purge, no retry queue, fallback, diagnostics, and docs.
- Known deliberate omission: no `UserPromptSubmit`, no LLM summary, no vector search, no purge, no no-arg `session-ingest`.
- Execution mode: use inline `superpowers:executing-plans`; subagents are not used because the user did not explicitly request subagent delegation.

import json

from codex_self_evolution.session_recall.models import ParsedMessage, ParsedSession


def _parsed_session(tmp_path, *, session_id="s1", repo_fingerprint="repo-a"):
    return ParsedSession(
        session_id=session_id,
        session_path=tmp_path / f"{session_id}.jsonl",
        cwd=str(tmp_path),
        metadata={
            "repo_fingerprint": repo_fingerprint,
            "repo_root": str(tmp_path),
            "worktree_root": str(tmp_path),
            "git_branch": "main",
            "started_at": "2026-01-01T00:00:00Z",
            "source_updated_at": "2026-01-01T00:00:01Z",
        },
        messages=[
            ParsedMessage(session_id, "m1", 0, "user", "hermes recall design", "{}"),
            ParsedMessage(session_id, "m2", 1, "tool", "hermes noisy stdout", "{}", tool_name="exec_command"),
            ParsedMessage(session_id, "m3", 2, "assistant", "final design decision", "{}"),
        ],
    )


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


def test_store_archives_and_searches_messages(tmp_path):
    from codex_self_evolution.session_recall.store import SessionRecallStore

    store = SessionRecallStore(tmp_path / "state.db")
    parsed = _parsed_session(tmp_path)

    result = store.archive(parsed)
    assert result["status"] == "archived"
    assert result["message_count"] == 3
    assert result["new_session"] is True

    again = store.archive(parsed)
    assert again["inserted_messages"] == 0
    assert again["new_session"] is False
    assert again["unchanged_session"] is True

    hits = store.search("hermes", repo_fingerprint="repo-a", limit=3)
    assert hits[0]["session_id"] == "s1"
    assert hits[0]["hit_count"] >= 1
    assert "messages" in hits[0]


def test_store_preserves_context_labels_in_session_metadata(tmp_path):
    from codex_self_evolution.session_recall.store import SessionRecallStore

    store = SessionRecallStore(tmp_path / "state.db")
    parsed = _parsed_session(tmp_path)
    parsed.metadata["context_labels"] = ["agent_injected_context", "local_repo_code"]

    store.archive(parsed)

    row = store._conn.execute(
        "SELECT metadata_json FROM sessions WHERE session_id = ?",
        (parsed.session_id,),
    ).fetchone()
    metadata = json.loads(row["metadata_json"])
    assert metadata["context_labels"] == ["agent_injected_context", "local_repo_code"]


def test_store_filters_repo_unless_global(tmp_path):
    from codex_self_evolution.session_recall.store import SessionRecallStore

    store = SessionRecallStore(tmp_path / "state.db")
    store.archive(_parsed_session(tmp_path, session_id="s1", repo_fingerprint="repo-a"))
    store.archive(_parsed_session(tmp_path, session_id="s2", repo_fingerprint="repo-b"))

    local = store.search("hermes", repo_fingerprint="repo-a", limit=10)
    assert {item["session_id"] for item in local} == {"s1"}

    global_hits = store.search("hermes", repo_fingerprint="repo-a", global_scope=True, limit=10)
    assert {item["session_id"] for item in global_hits} == {"s1", "s2"}


def test_store_supports_fts5_phrase_or_not_and_prefix(tmp_path):
    from codex_self_evolution.session_recall.store import SessionRecallStore

    store = SessionRecallStore(tmp_path / "state.db")
    store.archive(_parsed_session(tmp_path))

    assert store.search('"hermes recall"', repo_fingerprint="repo-a")
    assert store.search("hermes OR missing", repo_fingerprint="repo-a")
    assert store.search("herm*", repo_fingerprint="repo-a")
    assert store.search("hermes NOT stdout", repo_fingerprint="repo-a")


def test_store_supports_pipe_or_alias_and_strict_fallback(tmp_path):
    from codex_self_evolution.session_recall.store import SessionRecallStore

    store = SessionRecallStore(tmp_path / "state.db")
    store.archive(
        ParsedSession(
            session_id="alpha",
            session_path=tmp_path / "alpha.jsonl",
            cwd=str(tmp_path),
            metadata={"repo_fingerprint": "repo-a", "repo_root": str(tmp_path), "worktree_root": str(tmp_path)},
            messages=[ParsedMessage("alpha", "m1", 0, "user", "alpha only", "{}")],
        )
    )
    store.archive(
        ParsedSession(
            session_id="beta",
            session_path=tmp_path / "beta.jsonl",
            cwd=str(tmp_path),
            metadata={"repo_fingerprint": "repo-a", "repo_root": str(tmp_path), "worktree_root": str(tmp_path)},
            messages=[ParsedMessage("beta", "m1", 0, "user", "beta only", "{}")],
        )
    )

    assert {hit["session_id"] for hit in store.search("alpha|beta", repo_fingerprint="repo-a", limit=10)} == {
        "alpha",
        "beta",
    }
    assert store.search("alpha beta gamma", repo_fingerprint="repo-a")
    assert store.search("alpha beta gamma", repo_fingerprint="repo-a", all_terms=True) == []


def test_store_returns_multiple_evidence_windows_per_session(tmp_path):
    from codex_self_evolution.session_recall.store import SessionRecallStore

    store = SessionRecallStore(tmp_path / "state.db")
    messages = [ParsedMessage("s1", f"m{idx}", idx, "assistant", f"filler {idx}", "{}") for idx in range(8)]
    messages[0] = ParsedMessage("s1", "m0", 0, "user", "alpha decision", "{}")
    messages[7] = ParsedMessage("s1", "m7", 7, "assistant", "beta decision", "{}")
    store.archive(
        ParsedSession(
            session_id="s1",
            session_path=tmp_path / "s1.jsonl",
            cwd=str(tmp_path),
            metadata={"repo_fingerprint": "repo-a", "repo_root": str(tmp_path), "worktree_root": str(tmp_path)},
            messages=messages,
        )
    )

    hits = store.search("alpha|beta", repo_fingerprint="repo-a", windows_per_session=2, before=0, after=0)

    assert len(hits[0]["windows"]) == 2
    assert [window["anchor"]["message_index"] for window in hits[0]["windows"]] == [0, 7]


def test_store_prefers_user_anchor_over_developer_background(tmp_path):
    from codex_self_evolution.session_recall.store import SessionRecallStore

    store = SessionRecallStore(tmp_path / "state.db")
    store.archive(
        ParsedSession(
            session_id="s1",
            session_path=tmp_path / "s1.jsonl",
            cwd=str(tmp_path),
            metadata={"repo_fingerprint": "repo-a", "repo_root": str(tmp_path), "worktree_root": str(tmp_path)},
            messages=[
                ParsedMessage("s1", "m0", 0, "developer", "needle background", "{}"),
                ParsedMessage("s1", "m1", 1, "user", "needle user evidence", "{}"),
            ],
        )
    )

    hits = store.search("needle", repo_fingerprint="repo-a", windows_per_session=1, before=0, after=0)

    assert hits[0]["windows"][0]["anchor"]["role"] == "user"
    assert hits[0]["messages"][0]["role"] == "user"

    with_background = store.search(
        "needle",
        repo_fingerprint="repo-a",
        windows_per_session=1,
        before=1,
        after=0,
        include_background=True,
    )
    assert [message["role"] for message in with_background[0]["messages"]] == ["developer", "user"]


def test_store_like_fallback_searches_tool_name(tmp_path):
    from codex_self_evolution.session_recall.store import SessionRecallStore

    store = SessionRecallStore(tmp_path / "state.db")
    store.archive(
        ParsedSession(
            session_id="tool-hit",
            session_path=tmp_path / "tool.jsonl",
            cwd=str(tmp_path),
            metadata={"repo_fingerprint": "repo-a", "repo_root": str(tmp_path), "worktree_root": str(tmp_path)},
            messages=[
                ParsedMessage(
                    "tool-hit",
                    "m1",
                    0,
                    "tool",
                    "command output",
                    "{}",
                    tool_name="exec_command",
                )
            ],
        )
    )

    hits = store.search("exec_command", repo_fingerprint="repo-a")

    assert hits[0]["session_id"] == "tool-hit"
    assert hits[0]["windows"][0]["anchor"]["matched_by"] == "like"


def test_tool_hit_ranks_below_user_hit(tmp_path):
    from codex_self_evolution.session_recall.store import SessionRecallStore

    store = SessionRecallStore(tmp_path / "state.db")
    store.archive(
        ParsedSession(
            session_id="tool-only",
            session_path=tmp_path / "tool.jsonl",
            cwd=str(tmp_path),
            metadata={"repo_fingerprint": "repo-a", "repo_root": str(tmp_path), "worktree_root": str(tmp_path)},
            messages=[ParsedMessage("tool-only", "m1", 0, "tool", "needle", "{}", tool_name="exec_command")],
        )
    )
    store.archive(
        ParsedSession(
            session_id="user-hit",
            session_path=tmp_path / "user.jsonl",
            cwd=str(tmp_path),
            metadata={"repo_fingerprint": "repo-a", "repo_root": str(tmp_path), "worktree_root": str(tmp_path)},
            messages=[ParsedMessage("user-hit", "m1", 0, "user", "needle", "{}")],
        )
    )

    hits = store.search("needle", repo_fingerprint="repo-a", limit=2)
    assert hits[0]["session_id"] == "user-hit"


def test_recent_defaults_to_repo_scope(tmp_path):
    from codex_self_evolution.session_recall.store import SessionRecallStore

    store = SessionRecallStore(tmp_path / "state.db")
    store.archive(_parsed_session(tmp_path, session_id="s1", repo_fingerprint="repo-a"))
    store.archive(_parsed_session(tmp_path, session_id="s2", repo_fingerprint="repo-b"))

    recent = store.recent(repo_fingerprint="repo-a", limit=10)
    assert {item["session_id"] for item in recent} == {"s1"}
    global_recent = store.recent(repo_fingerprint="repo-a", global_scope=True, limit=10)
    assert {item["session_id"] for item in global_recent} == {"s1", "s2"}


def test_recent_orders_by_session_time_not_archive_time(tmp_path):
    from codex_self_evolution.session_recall.store import SessionRecallStore

    store = SessionRecallStore(tmp_path / "state.db")
    newer = _parsed_session(tmp_path, session_id="newer", repo_fingerprint="repo-a")
    newer.metadata["started_at"] = "2026-02-01T00:00:00Z"
    newer.metadata["source_updated_at"] = "2026-02-01T00:00:01Z"
    older = _parsed_session(tmp_path, session_id="older", repo_fingerprint="repo-a")
    older.metadata["started_at"] = "2026-01-01T00:00:00Z"
    older.metadata["source_updated_at"] = "2026-01-01T00:00:01Z"

    store.archive(newer)
    store.archive(older)

    recent = store.recent(repo_fingerprint="repo-a", limit=2)
    assert [item["session_id"] for item in recent] == ["newer", "older"]
    assert recent[0]["source_updated_at"] == "2026-02-01T00:00:01Z"
    assert recent[0]["archived_at"]


def test_recent_preview_uses_first_user_message(tmp_path):
    from codex_self_evolution.session_recall.store import SessionRecallStore

    store = SessionRecallStore(tmp_path / "state.db")
    store.archive(
        ParsedSession(
            session_id="s1",
            session_path=tmp_path / "s1.jsonl",
            cwd=str(tmp_path),
            metadata={"repo_fingerprint": "repo-a", "repo_root": str(tmp_path), "worktree_root": str(tmp_path)},
            messages=[
                ParsedMessage("s1", "m0", 0, "developer", "background instructions", "{}"),
                ParsedMessage("s1", "m1", 1, "user", "real user task", "{}"),
            ],
        )
    )

    recent = store.recent(repo_fingerprint="repo-a", limit=1)

    assert "real user task" in recent[0]["preview"]
    assert "background instructions" not in recent[0]["preview"]

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

    again = store.archive(parsed)
    assert again["inserted_messages"] == 0

    hits = store.search("hermes", repo_fingerprint="repo-a", limit=3)
    assert hits[0]["session_id"] == "s1"
    assert hits[0]["hit_count"] >= 1
    assert "messages" in hits[0]


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

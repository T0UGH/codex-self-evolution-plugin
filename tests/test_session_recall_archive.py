import json
import os


def _write_transcript(path, *, session_id="s1", cwd="", message="archive me"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join([
            json.dumps({
                "type": "session_meta",
                "payload": {
                    "id": session_id,
                    "cwd": cwd or str(path.parent),
                },
            }),
            json.dumps({"role": "user", "content": message}),
            "",
        ]),
        encoding="utf-8",
    )


def test_archive_from_hook_payload(tmp_path):
    from codex_self_evolution.session_recall.archive import archive_from_hook_payload

    transcript = tmp_path / "s.jsonl"
    transcript.write_text(json.dumps({"role": "user", "content": "archive me"}) + "\n", encoding="utf-8")
    payload = tmp_path / "payload.json"
    payload.write_text(
        json.dumps({"session_id": "s1", "transcript_path": str(transcript), "cwd": str(tmp_path)}),
        encoding="utf-8",
    )

    result = archive_from_hook_payload(payload, db_path=tmp_path / "state.db")

    assert result["status"] == "archived"
    assert result["session_id"] == "s1"
    assert result["message_count"] == 1


def test_archive_from_hook_payload_skips_registered_reflection_child(tmp_path):
    from codex_self_evolution.session_recall.archive import archive_from_hook_payload
    from codex_self_evolution.session_recall.store import SessionRecallStore
    from codex_self_evolution.session_reflection.state import register_child_thread

    home = tmp_path / "home"
    register_child_thread(
        child_thread_id="child-1",
        parent_session_id="parent-1",
        job_id="job-1",
        home=home,
    )
    payload = tmp_path / "payload.json"
    payload.write_text(
        json.dumps({"session_id": "child-1", "cwd": str(tmp_path)}),
        encoding="utf-8",
    )

    result = archive_from_hook_payload(payload, db_path=home / "session_recall" / "state.db")

    assert result == {
        "status": "skipped",
        "reason": "child_thread_registry",
        "detail": "child-1",
        "session_id": "child-1",
        "message_count": 0,
        "db_path": str((home / "session_recall" / "state.db").resolve()),
    }
    store = SessionRecallStore(home / "session_recall" / "state.db")
    try:
        assert store.stats()["ingest_error_count"] == 0
        assert store.stats()["session_count"] == 0
    finally:
        store.close()


def test_archive_from_hook_payload_skips_discovered_reflection_marker(tmp_path):
    from codex_self_evolution.session_recall.archive import archive_from_hook_payload
    from codex_self_evolution.session_recall.store import SessionRecallStore

    sessions_root = tmp_path / "sessions"
    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = sessions_root / "rollout-child-1.jsonl"
    _write_transcript(transcript, session_id="child-1", cwd=str(repo), message="CSEP_REFLECTION_CHILD=1")
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"session_id": "child-1", "cwd": str(repo)}), encoding="utf-8")

    result = archive_from_hook_payload(
        payload,
        db_path=tmp_path / "state.db",
        sessions_root=sessions_root,
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "reflection_marker"
    assert result["discovery_reason"] == "filename_session_id"
    store = SessionRecallStore(tmp_path / "state.db")
    try:
        assert store.stats()["ingest_error_count"] == 0
        assert store.stats()["session_count"] == 0
    finally:
        store.close()


def test_archive_from_hook_payload_discovers_transcript_by_filename_session_id(tmp_path):
    from codex_self_evolution.session_recall.archive import archive_from_hook_payload

    sessions_root = tmp_path / "sessions"
    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = sessions_root / "rollout-2026-05-17T00-00-00-s1.jsonl"
    _write_transcript(transcript, session_id="s1", cwd=str(repo), message="discovered by filename")
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"session_id": "s1", "cwd": str(repo)}), encoding="utf-8")

    result = archive_from_hook_payload(
        payload,
        db_path=tmp_path / "state.db",
        sessions_root=sessions_root,
    )

    assert result["status"] == "archived"
    assert result["session_id"] == "s1"
    assert result["discovery_status"] == "found"
    assert result["discovery_reason"] == "filename_session_id"
    assert result["discovered_transcript_path"] == str(transcript.resolve())
    assert result["message_count"] == 1


def test_archive_from_hook_payload_discovers_recent_unique_cwd(tmp_path):
    from codex_self_evolution.session_recall.archive import archive_from_hook_payload

    sessions_root = tmp_path / "sessions"
    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = sessions_root / "rollout-2026-05-17T00-00-00-real-session.jsonl"
    _write_transcript(transcript, session_id="real-session", cwd=str(repo), message="discovered by cwd")
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"session_id": "payload-session", "cwd": str(repo)}), encoding="utf-8")

    result = archive_from_hook_payload(
        payload,
        db_path=tmp_path / "state.db",
        sessions_root=sessions_root,
    )

    assert result["status"] == "archived"
    assert result["session_id"] == "real-session"
    assert result["discovery_reason"] == "recent_cwd_unique"
    assert result["message_count"] == 1


def test_archive_from_hook_payload_does_not_discover_ambiguous_recent_cwd(tmp_path):
    from codex_self_evolution.session_recall.archive import archive_from_hook_payload
    from codex_self_evolution.session_recall.store import SessionRecallStore

    sessions_root = tmp_path / "sessions"
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_transcript(sessions_root / "one.jsonl", session_id="one", cwd=str(repo), message="one")
    _write_transcript(sessions_root / "two.jsonl", session_id="two", cwd=str(repo), message="two")
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"session_id": "payload-session", "cwd": str(repo)}), encoding="utf-8")

    result = archive_from_hook_payload(
        payload,
        db_path=tmp_path / "state.db",
        sessions_root=sessions_root,
    )

    assert result["status"] == "error"
    assert "discovery_ambiguous" in result["error"]
    store = SessionRecallStore(tmp_path / "state.db")
    try:
        stats = store.stats()
        assert stats["ingest_error_count"] == 1
        assert stats["session_count"] == 0
    finally:
        store.close()


def test_archive_from_hook_payload_disambiguates_recent_cwd_by_last_assistant_message(tmp_path):
    from codex_self_evolution.session_recall.archive import archive_from_hook_payload

    sessions_root = tmp_path / "sessions"
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_transcript(sessions_root / "one.jsonl", session_id="one", cwd=str(repo), message="other session")
    matched = sessions_root / "two.jsonl"
    _write_transcript(
        matched,
        session_id="two",
        cwd=str(repo),
        message="The exact final assistant sentence that appears in the Stop payload.",
    )
    payload = tmp_path / "payload.json"
    payload.write_text(
        json.dumps(
            {
                "session_id": "payload-session",
                "cwd": str(repo),
                "last_assistant_message": "The exact final assistant sentence that appears in the Stop payload.",
            }
        ),
        encoding="utf-8",
    )

    result = archive_from_hook_payload(
        payload,
        db_path=tmp_path / "state.db",
        sessions_root=sessions_root,
    )

    assert result["status"] == "archived"
    assert result["session_id"] == "two"
    assert result["discovery_reason"] == "recent_last_assistant_message"
    assert result["discovered_transcript_path"] == str(matched.resolve())


def test_archive_from_hook_payload_discovers_recent_turn_id(tmp_path):
    from codex_self_evolution.session_recall.archive import archive_from_hook_payload

    sessions_root = tmp_path / "sessions"
    repo = tmp_path / "repo"
    repo.mkdir()
    transcript = sessions_root / "turn-match.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"id": "turn-session", "cwd": str(repo)}}),
                json.dumps({"id": "turn-abc", "role": "assistant", "content": "archive this turn"}),
                "",
            ]
        ),
        encoding="utf-8",
    )
    payload = tmp_path / "payload.json"
    payload.write_text(
        json.dumps({"session_id": "payload-session", "turn_id": "turn-abc", "cwd": str(repo)}),
        encoding="utf-8",
    )

    result = archive_from_hook_payload(
        payload,
        db_path=tmp_path / "state.db",
        sessions_root=sessions_root,
    )

    assert result["status"] == "archived"
    assert result["session_id"] == "turn-session"
    assert result["discovery_reason"] == "recent_turn_id"


def test_archive_from_hook_payload_records_error_for_missing_transcript(tmp_path):
    from codex_self_evolution.session_recall.archive import archive_from_hook_payload
    from codex_self_evolution.session_recall.store import SessionRecallStore

    payload = tmp_path / "payload.json"
    payload.write_text(
        json.dumps({"session_id": "s1", "transcript_path": str(tmp_path / "missing.jsonl"), "cwd": str(tmp_path)}),
        encoding="utf-8",
    )

    result = archive_from_hook_payload(payload, db_path=tmp_path / "state.db")

    assert result["status"] == "error"
    status = SessionRecallStore(tmp_path / "state.db").stats()
    assert status["ingest_error_count"] == 1


def test_backfill_sessions_honors_limit_files(tmp_path):
    from codex_self_evolution.session_recall.archive import backfill_sessions
    from codex_self_evolution.session_recall.store import SessionRecallStore

    root = tmp_path / "sessions"
    root.mkdir()
    for idx in range(3):
        (root / f"s{idx}.jsonl").write_text(
            json.dumps({"role": "user", "content": f"message {idx}"}) + "\n",
            encoding="utf-8",
        )

    result = backfill_sessions(root=root, cwd=str(tmp_path), db_path=tmp_path / "state.db", limit_files=2)

    assert result["processed_files"] == 2
    assert SessionRecallStore(tmp_path / "state.db").stats()["session_count"] == 2


def test_backfill_sessions_limits_newest_files_first(tmp_path):
    from codex_self_evolution.session_recall.archive import backfill_sessions
    from codex_self_evolution.session_recall.store import SessionRecallStore

    root = tmp_path / "sessions"
    root.mkdir()
    old = root / "old.jsonl"
    new = root / "new.jsonl"
    old.write_text(json.dumps({"role": "user", "content": "old message"}) + "\n", encoding="utf-8")
    new.write_text(json.dumps({"role": "user", "content": "new message"}) + "\n", encoding="utf-8")
    os.utime(old, (1_700_000_000, 1_700_000_000))
    os.utime(new, (1_800_000_000, 1_800_000_000))

    result = backfill_sessions(root=root, cwd=str(tmp_path), db_path=tmp_path / "state.db", limit_files=1)

    assert result["processed_files"] == 1
    assert result["processed_successfully"] == 1
    assert result["new_sessions"] == 1
    store = SessionRecallStore(tmp_path / "state.db")
    try:
        assert store.search("new", global_scope=True)
        assert store.search("old", global_scope=True) == []
        assert store.stats()["latest_ingest_run"]["new_sessions"] == 1
    finally:
        store.close()


def test_backfill_sessions_reports_unchanged_sessions_on_repeat(tmp_path):
    from codex_self_evolution.session_recall.archive import backfill_sessions

    root = tmp_path / "sessions"
    root.mkdir()
    (root / "one.jsonl").write_text(json.dumps({"role": "user", "content": "same"}) + "\n", encoding="utf-8")

    first = backfill_sessions(root=root, cwd=str(tmp_path), db_path=tmp_path / "state.db")
    second = backfill_sessions(root=root, cwd=str(tmp_path), db_path=tmp_path / "state.db")

    assert first["new_sessions"] == 1
    assert second["new_sessions"] == 0
    assert second["unchanged_sessions"] == 1

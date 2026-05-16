import json
import os


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

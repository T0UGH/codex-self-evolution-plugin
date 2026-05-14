from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from codex_self_evolution.skill_synthesis.evidence import (
    collect_evidence,
    load_evidence_index,
    materialize_evidence_workspace,
    update_evidence_index,
)
from codex_self_evolution.skill_synthesis.paths import build_skill_synthesis_paths


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_collect_evidence_uses_reviewer_timestamp_for_done_suggestions(tmp_path: Path) -> None:
    now = datetime(2026, 5, 13, tzinfo=UTC)
    bucket = tmp_path / "projects" / "-repo"
    _write_json(bucket / "suggestions" / "done" / "s1.json", {
        "schema_version": 1,
        "suggestion_id": "s1",
        "idempotency_key": "id1",
        "thread_id": "t1",
        "cwd": "/repo",
        "repo_fingerprint": "repo",
        "reviewer_timestamp": "2026-05-13T00:00:00Z",
        "suggestions": [
            {
                "family": "memory_updates",
                "summary": "Run status",
                "details": {"content": "Use status before changing scheduler."},
            },
        ],
        "source_authority": [],
        "state": "done",
    })

    evidence = collect_evidence(tmp_path, now=now, mode="incremental", lookback_hours=24, lookback_days=30)

    assert len(evidence) == 1
    assert evidence[0]["event_time_source"] == "reviewer_timestamp"
    assert evidence[0]["family"] == "memory_updates"


def test_collect_evidence_filters_old_items_by_lookback(tmp_path: Path) -> None:
    now = datetime(2026, 5, 13, tzinfo=UTC)
    old = now - timedelta(days=40)
    memory_path = tmp_path / "projects" / "-repo" / "memory" / "memory.json"
    _write_json(memory_path, {
        "global": [
            {"summary": "Old", "content": "Old workflow", "updated_at": old.isoformat().replace("+00:00", "Z")},
        ],
        "user": [],
    })

    evidence = collect_evidence(tmp_path, now=now, mode="full", lookback_hours=24, lookback_days=30)

    assert evidence == []


def test_materialize_workspace_redacts_and_records_original_path(tmp_path: Path) -> None:
    paths = build_skill_synthesis_paths(home=tmp_path, run_id="run-1")
    source = tmp_path / "projects" / "-repo" / "memory" / "memory.json"
    source.parent.mkdir(parents=True)
    source.write_text("{}", encoding="utf-8")
    evidence = [{
        "evidence_key": "ev1",
        "bucket": "-repo",
        "source_path": str(source),
        "original_path": str(source),
        "source_type": "memory",
        "family": "memory_updates",
        "event_time": "2026-05-13T00:00:00Z",
        "event_time_source": "record_updated_at",
        "summary": "Secret note",
        "content": "Run with api_key=sk-1234567890 during tests.",
        "content_hash": "hash",
        "seen_before": False,
    }]

    manifest = materialize_evidence_workspace(paths, evidence, skills_inventory={"synth": [], "non_synth": []}, synth_inventory=[])

    manifest_path = paths.run_input_dir / "MANIFEST.json"
    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["evidence"][0]["original_path"] == str(source)
    assert data["evidence"][0]["redaction_count"] == 1
    excerpt = Path(data["evidence"][0]["excerpt_path"]).read_text(encoding="utf-8")
    assert "sk-1234567890" not in excerpt
    assert manifest["evidence_count"] == 1


def test_evidence_index_updates_seen_counts(tmp_path: Path) -> None:
    path = tmp_path / "skill_synthesis" / "evidence_index.json"
    index = load_evidence_index(path)
    update_evidence_index(path, index, [{"evidence_key": "ev1"}], run_id="run-1", outcome="used")
    updated = load_evidence_index(path)

    assert updated["evidence"]["ev1"]["seen_count"] == 1
    assert updated["evidence"]["ev1"]["last_run_id"] == "run-1"

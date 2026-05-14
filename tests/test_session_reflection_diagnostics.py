"""Diagnostics must surface session-reflection state in the main status output."""
from __future__ import annotations

import json
from pathlib import Path

from codex_self_evolution.diagnostics import collect_status


def test_collect_status_includes_session_reflection_latest_job(tmp_path: Path) -> None:
    """A latest reflection job should be visible from codex-self-evolution status."""
    root = tmp_path / "session_reflection"
    root.mkdir()
    (root / "latest.json").write_text(
        json.dumps({
            "job_id": "reflect-job-1",
            "status": "succeeded",
            "updated_at": "2026-05-14T12:00:00Z",
            "error": "none",
        }),
        encoding="utf-8",
    )

    status = collect_status(home=tmp_path)

    reflection = status["session_reflection"]
    assert reflection["exists"] is True
    assert reflection["latest"]["job_id"] == "reflect-job-1"
    assert reflection["latest"]["status"] == "succeeded"
    assert reflection["latest"]["updated_at"] == "2026-05-14T12:00:00Z"
    assert reflection["latest"]["error"] == "none"

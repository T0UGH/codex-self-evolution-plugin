# Session Reflection Trigger Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic Stop-hook trigger policy that archives every session but only forks session reflection when per-session nudge rules or conservative user keywords are hit.

**Architecture:** Keep the slow Codex fork/review path in the existing `session_reflection` worker, and add a new foreground trigger layer that updates per-session sidecar state under `~/.codex-self-evolution/session_reflection/triggers/<stable_session_id>/`. The trigger layer owns counters, transcript byte-offset scanning, keyword matching, active-job deferral, and reset-by-snapshot; existing job, prompt, runner, CLI, and status surfaces consume that decision.

**Tech Stack:** Python 3.11+ stdlib only, pytest, existing CSEP config/state/CLI modules, Codex Stop hook payloads, JSON/JSONL sidecar files, `fcntl.flock` on macOS/Linux.

---

## Scope And Existing Context

This plan implements the spec in:

- `docs/superpowers/specs/2026-05-15-session-reflection-trigger-policy-design.md`

The current worker already has:

- `src/codex_self_evolution/session_reflection/state.py` for job files, latest pointer, child registry, and global worker lock.
- `src/codex_self_evolution/session_reflection/guard.py` for child recursion guard.
- `src/codex_self_evolution/session_reflection/runner.py` for enqueue, fork, prompt, receipt validation, and status.
- `src/codex_self_evolution/session_reflection/prompt.py` for child prompt contract.
- `src/codex_self_evolution/cli.py` for `stop-review --from-stdin` and `session-reflect`.

Do not implement:

- `skill_readable_chars`.
- keyword TOML override.
- retention configuration.
- a general fix for `storage.py:file_lock`; trigger state must use its own lock.

Use the repo's working test command:

```bash
uv run pytest -q
```

## File Structure

- Modify: `src/codex_self_evolution/config_file.py`
  - Add nested `SessionReflectionTriggerConfig`.
  - Parse `[session_reflection.trigger]`.
  - Expose trigger fields via `config_to_dict()` and source tracking.

- Modify: `src/codex_self_evolution/config_file_template.py`
  - Document default trigger settings in the generated config template.

- Modify: `src/codex_self_evolution/session_reflection/paths.py`
  - Add `triggers_dir`.
  - Add `SessionReflectionTriggerPaths`.
  - Add `build_session_reflection_trigger_paths()`.

- Create: `src/codex_self_evolution/session_reflection/trigger.py`
  - Per-session trigger state schema.
  - Per-session `fcntl.flock` lock.
  - Transcript byte-offset delta scanning.
  - Conservative keyword matching.
  - Decision evaluation.
  - Decision retention.
  - Counter snapshot reset.

- Modify: `src/codex_self_evolution/session_reflection/state.py`
  - Add `find_active_parent_job()`.
  - Keep historical job lookup separate from active-job guard.
  - Extend `create_job_from_payload()` to accept trigger decision fields.

- Modify: `src/codex_self_evolution/session_reflection/guard.py`
  - Keep recursion guard child-only.
  - Remove `succeeded` parent-job skip behavior from recursion guard.

- Modify: `src/codex_self_evolution/session_reflection/runner.py`
  - Use trigger policy in `enqueue_reflection_from_payload()`.
  - Pass review scope and `skill_generation_mode` into prompt.
  - Reset trigger counters after job validation result.
  - Include trigger snapshot in `session_reflection_status()`.

- Modify: `src/codex_self_evolution/session_reflection/prompt.py`
  - Add `review_memory`, `review_skills`, `trigger_reasons`, and `skill_generation_mode` to prompt contract.

- Modify: `src/codex_self_evolution/cli.py`
  - Stop hook continues to spawn archive in the background.
  - Only spawn `session-reflect` when trigger enqueue returns `queued`.
  - Preserve non-blocking `{"continue": true}` behavior.

- Create/Modify Tests:
  - `tests/test_session_reflection_trigger.py`
  - `tests/test_session_reflection_config.py`
  - `tests/test_session_reflection_state.py`
  - `tests/test_session_reflection_guard.py`
  - `tests/test_session_reflection_runner.py`
  - `tests/test_session_reflection_cli.py`

---

## Task 1: Config And Trigger Paths

**Files:**
- Modify: `src/codex_self_evolution/config_file.py`
- Modify: `src/codex_self_evolution/config_file_template.py`
- Modify: `src/codex_self_evolution/session_reflection/paths.py`
- Modify: `tests/test_session_reflection_config.py`

- [ ] **Step 1: Write failing config and path tests**

Append to `tests/test_session_reflection_config.py`:

```python
def test_session_reflection_trigger_defaults(tmp_path: Path) -> None:
    """Trigger policy defaults are enabled and source tracked."""
    loaded = load_config(home=tmp_path)
    trigger = loaded.config.session_reflection.trigger

    assert trigger.enabled is True
    assert trigger.memory_stop_interval == 3
    assert trigger.memory_context_chars == 16000
    assert trigger.skill_tool_call_interval == 15
    assert trigger.high_signal_immediate is True
    assert trigger.skill_generation_mode == "one_shot_active"
    assert trigger.active_job_stale_seconds == 1800
    assert loaded.sources["session_reflection.trigger.enabled"] == "default"
    assert loaded.sources["session_reflection.trigger.skill_generation_mode"] == "default"


def test_session_reflection_trigger_toml_values_apply(tmp_path: Path) -> None:
    """Nested [session_reflection.trigger] values override defaults."""
    (tmp_path / "config.toml").write_text(
        "\n".join(
            [
                "[session_reflection.trigger]",
                "enabled = false",
                "memory_stop_interval = 4",
                "memory_context_chars = 32000",
                "skill_tool_call_interval = 21",
                "high_signal_immediate = false",
                'skill_generation_mode = "evidence_first"',
                "active_job_stale_seconds = 60",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_config(home=tmp_path)
    trigger = loaded.config.session_reflection.trigger

    assert trigger.enabled is False
    assert trigger.memory_stop_interval == 4
    assert trigger.memory_context_chars == 32000
    assert trigger.skill_tool_call_interval == 21
    assert trigger.high_signal_immediate is False
    assert trigger.skill_generation_mode == "evidence_first"
    assert trigger.active_job_stale_seconds == 60
    assert loaded.sources["session_reflection.trigger.enabled"] == "config.toml"
    assert loaded.sources["session_reflection.trigger.active_job_stale_seconds"] == "config.toml"


def test_session_reflection_trigger_invalid_values_warn(tmp_path: Path) -> None:
    """Invalid trigger values fall back and emit warnings."""
    (tmp_path / "config.toml").write_text(
        "\n".join(
            [
                "[session_reflection.trigger]",
                "memory_stop_interval = 0",
                "memory_context_chars = -1",
                "skill_tool_call_interval = 0",
                'skill_generation_mode = "always"',
                "active_job_stale_seconds = 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_config(home=tmp_path)
    trigger = loaded.config.session_reflection.trigger

    assert trigger.memory_stop_interval == 3
    assert trigger.memory_context_chars == 16000
    assert trigger.skill_tool_call_interval == 15
    assert trigger.skill_generation_mode == "one_shot_active"
    assert trigger.active_job_stale_seconds == 1800
    assert any("session_reflection.trigger.memory_stop_interval" in item for item in loaded.warnings)
    assert any("session_reflection.trigger.skill_generation_mode" in item for item in loaded.warnings)


def test_session_reflection_trigger_paths(tmp_path: Path) -> None:
    """Trigger sidecar paths are session scoped and path safe."""
    from codex_self_evolution.session_reflection.paths import build_session_reflection_trigger_paths

    paths = build_session_reflection_trigger_paths("../unsafe/session", home=tmp_path)

    assert paths.root == tmp_path / "session_reflection" / "triggers"
    assert paths.session_dir.parent == paths.root
    assert paths.session_dir.name != "../unsafe/session"
    assert paths.state_path == paths.session_dir / "state.json"
    assert paths.decisions_path == paths.session_dir / "decisions.jsonl"
    assert paths.lock_path == paths.session_dir / "trigger.lock"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest -q tests/test_session_reflection_config.py::test_session_reflection_trigger_defaults tests/test_session_reflection_config.py::test_session_reflection_trigger_toml_values_apply tests/test_session_reflection_config.py::test_session_reflection_trigger_invalid_values_warn tests/test_session_reflection_config.py::test_session_reflection_trigger_paths
```

Expected: fail with missing `trigger` config and missing `build_session_reflection_trigger_paths`.

- [ ] **Step 3: Add trigger config dataclass**

In `src/codex_self_evolution/config_file.py`, add after `SessionReflectionConfig` or directly before it:

```python
@dataclass
class SessionReflectionTriggerConfig:
    """Deterministic Stop-hook trigger policy configuration."""

    enabled: bool = True
    memory_stop_interval: int = 3
    memory_context_chars: int = 16000
    skill_tool_call_interval: int = 15
    high_signal_immediate: bool = True
    skill_generation_mode: str = "one_shot_active"
    active_job_stale_seconds: int = DEFAULT_LOCK_STALE_SECONDS
```

Then add the field to `SessionReflectionConfig`:

```python
trigger: SessionReflectionTriggerConfig = field(default_factory=SessionReflectionTriggerConfig)
```

If `DEFAULT_LOCK_STALE_SECONDS` is not imported in `config_file.py`, add it to the existing import from `config`.

- [ ] **Step 4: Parse `[session_reflection.trigger]`**

In `_load_from_raw_toml()` near the existing session reflection parsing, add:

```python
    trigger_toml = reflection_toml.get("trigger", {}) or {}
    if not isinstance(trigger_toml, dict):
        warnings.append("session_reflection.trigger must be a table")
        trigger_toml = {}

    trigger_enabled = trigger_toml.get("enabled")
    if isinstance(trigger_enabled, bool):
        config.session_reflection.trigger.enabled = trigger_enabled
        sources["session_reflection.trigger.enabled"] = "config.toml"
    else:
        sources["session_reflection.trigger.enabled"] = "default"

    def _positive_int(field: str, default: int) -> int:
        value = trigger_toml.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            sources[f"session_reflection.trigger.{field}"] = "config.toml"
            return value
        sources[f"session_reflection.trigger.{field}"] = "default"
        if field in trigger_toml:
            warnings.append(f"session_reflection.trigger.{field} must be a positive integer")
        return default

    config.session_reflection.trigger.memory_stop_interval = _positive_int(
        "memory_stop_interval",
        config.session_reflection.trigger.memory_stop_interval,
    )
    config.session_reflection.trigger.memory_context_chars = _positive_int(
        "memory_context_chars",
        config.session_reflection.trigger.memory_context_chars,
    )
    config.session_reflection.trigger.skill_tool_call_interval = _positive_int(
        "skill_tool_call_interval",
        config.session_reflection.trigger.skill_tool_call_interval,
    )
    config.session_reflection.trigger.active_job_stale_seconds = _positive_int(
        "active_job_stale_seconds",
        config.session_reflection.trigger.active_job_stale_seconds,
    )

    high_signal = trigger_toml.get("high_signal_immediate")
    if isinstance(high_signal, bool):
        config.session_reflection.trigger.high_signal_immediate = high_signal
        sources["session_reflection.trigger.high_signal_immediate"] = "config.toml"
    else:
        sources["session_reflection.trigger.high_signal_immediate"] = "default"

    mode = trigger_toml.get("skill_generation_mode")
    if mode in {"one_shot_active", "evidence_first"}:
        config.session_reflection.trigger.skill_generation_mode = str(mode)
        sources["session_reflection.trigger.skill_generation_mode"] = "config.toml"
    else:
        sources["session_reflection.trigger.skill_generation_mode"] = "default"
        if mode not in (None, ""):
            warnings.append(
                "session_reflection.trigger.skill_generation_mode must be "
                "'one_shot_active' or 'evidence_first'"
            )
```

Add the new fields to the `KNOWN_CONFIG_PATHS` tuple:

```python
    "session_reflection.trigger", "session_reflection.trigger.enabled",
    "session_reflection.trigger.memory_stop_interval",
    "session_reflection.trigger.memory_context_chars",
    "session_reflection.trigger.skill_tool_call_interval",
    "session_reflection.trigger.high_signal_immediate",
    "session_reflection.trigger.skill_generation_mode",
    "session_reflection.trigger.active_job_stale_seconds",
```

- [ ] **Step 5: Add trigger path helpers**

In `src/codex_self_evolution/session_reflection/paths.py`, add:

```python
from ..storage import compute_stable_id


@dataclass(frozen=True)
class SessionReflectionTriggerPaths:
    """Filesystem paths for one session's trigger sidecar state."""

    home: Path
    root: Path
    session_dir: Path
    state_path: Path
    decisions_path: Path
    lock_path: Path
```

Add `triggers_dir: Path` to `SessionReflectionPaths`, and include it in `build_session_reflection_paths()`:

```python
triggers_dir=root / "triggers",
```

Then add:

```python
def build_session_reflection_trigger_paths(
    session_id: str,
    home: str | Path | None = None,
) -> SessionReflectionTriggerPaths:
    """Resolve sidecar trigger paths for one parent session id."""
    home_dir = Path(home).expanduser().resolve() if home else get_home_dir()
    root = home_dir / SESSION_REFLECTION_SUBDIR / "triggers"
    session_dir = root / compute_stable_id(session_id or "unknown-session")
    return SessionReflectionTriggerPaths(
        home=home_dir,
        root=root,
        session_dir=session_dir,
        state_path=session_dir / "state.json",
        decisions_path=session_dir / "decisions.jsonl",
        lock_path=session_dir / "trigger.lock",
    )
```

- [ ] **Step 6: Update config template**

In `src/codex_self_evolution/config_file_template.py`, add this block below the existing `[session_reflection]` defaults:

```toml
# [session_reflection.trigger] — deterministic Stop-hook nudge policy
[session_reflection.trigger]
enabled = true
memory_stop_interval = 3
memory_context_chars = 16000
skill_tool_call_interval = 15
high_signal_immediate = true
skill_generation_mode = "one_shot_active"
active_job_stale_seconds = 1800
```

- [ ] **Step 7: Run tests and commit**

Run:

```bash
uv run pytest -q tests/test_session_reflection_config.py
```

Expected: pass.

Commit:

```bash
git add src/codex_self_evolution/config_file.py src/codex_self_evolution/config_file_template.py src/codex_self_evolution/session_reflection/paths.py tests/test_session_reflection_config.py
git commit -m "feat: add session reflection trigger config"
```

---

## Task 2: Trigger State Sidecar, Lock, And Reset

**Files:**
- Create: `src/codex_self_evolution/session_reflection/trigger.py`
- Create: `tests/test_session_reflection_trigger.py`

- [ ] **Step 1: Write failing trigger state tests**

Create `tests/test_session_reflection_trigger.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_self_evolution.session_reflection.trigger import (
    TriggerLockBusy,
    append_decision,
    load_trigger_state,
    reset_counters_after_job,
    session_trigger_lock,
    trigger_paths_for_payload,
    write_trigger_state,
)


def _payload(repo: Path, **overrides: object) -> dict[str, object]:
    transcript = repo / "rollout.jsonl"
    transcript.write_text("", encoding="utf-8")
    payload: dict[str, object] = {
        "session_id": "parent-1",
        "turn_id": "turn-1",
        "transcript_path": str(transcript),
        "cwd": str(repo),
    }
    payload.update(overrides)
    return payload


def test_load_trigger_state_defaults(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    paths = trigger_paths_for_payload(_payload(repo), home=tmp_path)

    state = load_trigger_state(paths, session_id="parent-1")

    assert state["schema_version"] == 1
    assert state["session_id"] == "parent-1"
    assert state["last_counted_byte_offset"] == 0
    assert state["stops_since_memory_review"] == 0
    assert state["readable_chars_since_memory_review"] == 0
    assert state["tool_calls_since_skill_review"] == 0
    assert state["active_job_id"] is None


def test_write_trigger_state_and_append_decision(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    paths = trigger_paths_for_payload(_payload(repo), home=tmp_path)
    state = load_trigger_state(paths, session_id="parent-1")
    state["stops_since_memory_review"] = 2

    write_trigger_state(paths, state)
    append_decision(paths, {"status": "archive_only", "skip_reason": "below_threshold"})

    assert json.loads(paths.state_path.read_text(encoding="utf-8"))["stops_since_memory_review"] == 2
    lines = paths.decisions_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["decision"]["skip_reason"] == "below_threshold"


def test_append_decision_retains_last_200_entries(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    paths = trigger_paths_for_payload(_payload(repo), home=tmp_path)

    for idx in range(205):
        append_decision(paths, {"status": "archive_only", "idx": idx})

    rows = [json.loads(line) for line in paths.decisions_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 200
    assert rows[0]["decision"]["idx"] == 5
    assert rows[-1]["decision"]["idx"] == 204


def test_session_trigger_lock_is_non_blocking(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    paths = trigger_paths_for_payload(_payload(repo), home=tmp_path)

    with session_trigger_lock(paths):
        with pytest.raises(TriggerLockBusy):
            with session_trigger_lock(paths):
                raise AssertionError("nested lock should not be acquired")


def test_reset_counters_after_job_uses_snapshot_subtraction(tmp_path: Path) -> None:
    state = {
        "stops_since_memory_review": 5,
        "readable_chars_since_memory_review": 22000,
        "tool_calls_since_skill_review": 18,
        "last_memory_review_at": None,
        "last_skill_review_at": None,
        "active_job_id": "job-1",
    }
    job = {
        "job_id": "job-1",
        "review_memory": True,
        "review_skills": True,
        "counter_snapshot": {
            "stops_since_memory_review": 3,
            "readable_chars_since_memory_review": 16000,
            "tool_calls_since_skill_review": 15,
        },
    }

    updated = reset_counters_after_job(state, job, memory_succeeded=True, skill_succeeded=False, now="2026-05-15T00:00:00Z")

    assert updated["stops_since_memory_review"] == 2
    assert updated["readable_chars_since_memory_review"] == 6000
    assert updated["tool_calls_since_skill_review"] == 18
    assert updated["last_memory_review_at"] == "2026-05-15T00:00:00Z"
    assert updated["last_skill_review_at"] is None
    assert updated["active_job_id"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest -q tests/test_session_reflection_trigger.py
```

Expected: fail because `session_reflection.trigger` module does not exist.

- [ ] **Step 3: Implement trigger state and lock helpers**

Create `src/codex_self_evolution/session_reflection/trigger.py`:

```python
from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..storage import atomic_write_json, load_json, utc_now
from .paths import SessionReflectionTriggerPaths, build_session_reflection_trigger_paths

DECISION_RETENTION = 200


class TriggerLockBusy(RuntimeError):
    """Raised when a per-session trigger lock is already held."""


def payload_session_id(payload: dict[str, Any]) -> str:
    """Return the parent session id from a Codex Stop payload."""
    return str(payload.get("session_id") or payload.get("thread_id") or "unknown-session")


def trigger_paths_for_payload(
    payload: dict[str, Any],
    *,
    home: str | Path | None = None,
) -> SessionReflectionTriggerPaths:
    """Return trigger sidecar paths for a Stop payload."""
    return build_session_reflection_trigger_paths(payload_session_id(payload), home=home)


def default_trigger_state(session_id: str) -> dict[str, Any]:
    """Return a fresh trigger state object for one parent session."""
    now = utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": 1,
        "session_id": session_id,
        "last_counted_byte_offset": 0,
        "last_counted_message_index": 0,
        "last_counted_event_uid": "",
        "stops_since_memory_review": 0,
        "readable_chars_since_memory_review": 0,
        "tool_calls_since_skill_review": 0,
        "last_memory_review_at": None,
        "last_skill_review_at": None,
        "active_job_id": None,
        "last_decision": {},
        "updated_at": now,
    }


def load_trigger_state(paths: SessionReflectionTriggerPaths, *, session_id: str) -> dict[str, Any]:
    """Load trigger state or return defaults when no state exists."""
    if not paths.state_path.is_file():
        return default_trigger_state(session_id)
    raw = load_json(paths.state_path)
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        return default_trigger_state(session_id)
    state = default_trigger_state(session_id)
    state.update(raw)
    state["session_id"] = session_id
    return state


def write_trigger_state(paths: SessionReflectionTriggerPaths, state: dict[str, Any]) -> None:
    """Atomically write one session trigger state."""
    paths.session_dir.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")
    atomic_write_json(paths.state_path, state)


def append_decision(paths: SessionReflectionTriggerPaths, decision: dict[str, Any]) -> None:
    """Append a decision snapshot and retain only recent entries."""
    paths.session_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "schema_version": 1,
        "created_at": utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "decision": decision,
    }
    rows: list[str] = []
    if paths.decisions_path.is_file():
        rows = paths.decisions_path.read_text(encoding="utf-8").splitlines()
    rows.append(json.dumps(row, ensure_ascii=False, sort_keys=True))
    rows = rows[-DECISION_RETENTION:]
    tmp_path = paths.decisions_path.with_suffix(".jsonl.tmp")
    tmp_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    tmp_path.replace(paths.decisions_path)


@contextmanager
def session_trigger_lock(paths: SessionReflectionTriggerPaths) -> Iterator[None]:
    """Acquire a non-blocking per-session trigger lock."""
    paths.session_dir.mkdir(parents=True, exist_ok=True)
    with paths.lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise TriggerLockBusy(f"trigger lock busy: {paths.lock_path}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def reset_counters_after_job(
    state: dict[str, Any],
    job: dict[str, Any],
    *,
    memory_succeeded: bool,
    skill_succeeded: bool,
    now: str,
) -> dict[str, Any]:
    """Reset only the counter snapshot covered by a completed job."""
    updated = dict(state)
    snapshot = job.get("counter_snapshot") if isinstance(job.get("counter_snapshot"), dict) else {}
    if job.get("review_memory") and memory_succeeded:
        updated["stops_since_memory_review"] = max(
            0,
            int(updated.get("stops_since_memory_review") or 0)
            - int(snapshot.get("stops_since_memory_review") or 0),
        )
        updated["readable_chars_since_memory_review"] = max(
            0,
            int(updated.get("readable_chars_since_memory_review") or 0)
            - int(snapshot.get("readable_chars_since_memory_review") or 0),
        )
        updated["last_memory_review_at"] = now
    if job.get("review_skills") and skill_succeeded:
        updated["tool_calls_since_skill_review"] = max(
            0,
            int(updated.get("tool_calls_since_skill_review") or 0)
            - int(snapshot.get("tool_calls_since_skill_review") or 0),
        )
        updated["last_skill_review_at"] = now
    if updated.get("active_job_id") == job.get("job_id"):
        updated["active_job_id"] = None
    return updated
```

- [ ] **Step 4: Run trigger state tests**

Run:

```bash
uv run pytest -q tests/test_session_reflection_trigger.py
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/codex_self_evolution/session_reflection/trigger.py tests/test_session_reflection_trigger.py
git commit -m "feat: add reflection trigger sidecar state"
```

---

## Task 3: Transcript Delta And Keyword Decision Logic

**Files:**
- Modify: `src/codex_self_evolution/session_reflection/trigger.py`
- Modify: `tests/test_session_reflection_trigger.py`

- [ ] **Step 1: Add failing delta and keyword tests**

Append to `tests/test_session_reflection_trigger.py`:

```python
from codex_self_evolution.config_file import SessionReflectionTriggerConfig
from codex_self_evolution.session_reflection.trigger import evaluate_trigger_policy


def _append_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_evaluate_trigger_archive_only_updates_counters_and_offset(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    transcript = Path(str(payload["transcript_path"]))
    _append_jsonl(
        transcript,
        [
            {"type": "response_item", "payload": {"role": "user", "content": "hello"}},
            {"type": "response_item", "payload": {"role": "assistant", "content": "done"}},
        ],
    )
    cfg = SessionReflectionTriggerConfig(memory_stop_interval=3, memory_context_chars=16000, skill_tool_call_interval=15)

    result = evaluate_trigger_policy(payload, cfg, home=tmp_path)

    assert result["status"] == "archive_only"
    assert result["decision"]["skip_reason"] == "below_threshold"
    assert result["state"]["stops_since_memory_review"] == 1
    assert result["state"]["readable_chars_since_memory_review"] >= len("hellodone")
    assert result["state"]["last_counted_byte_offset"] == transcript.stat().st_size


def test_evaluate_trigger_memory_stop_interval_queues_memory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    paths = trigger_paths_for_payload(payload, home=tmp_path)
    state = load_trigger_state(paths, session_id="parent-1")
    state["stops_since_memory_review"] = 2
    write_trigger_state(paths, state)

    result = evaluate_trigger_policy(payload, SessionReflectionTriggerConfig(), home=tmp_path)

    assert result["status"] == "queued"
    assert result["decision"]["review_memory"] is True
    assert result["decision"]["review_skills"] is False
    assert result["decision"]["trigger_reasons"] == ["memory_stop_interval"]


def test_evaluate_trigger_tool_calls_queue_skill(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    transcript = Path(str(payload["transcript_path"]))
    _append_jsonl(
        transcript,
        [
            {
                "type": "response_item",
                "payload": {
                    "role": "assistant",
                    "tool_calls": [{"name": "exec_command", "arguments": {"cmd": "date"}}],
                },
            }
        ],
    )
    paths = trigger_paths_for_payload(payload, home=tmp_path)
    state = load_trigger_state(paths, session_id="parent-1")
    state["tool_calls_since_skill_review"] = 14
    write_trigger_state(paths, state)

    result = evaluate_trigger_policy(payload, SessionReflectionTriggerConfig(), home=tmp_path)

    assert result["status"] == "queued"
    assert result["decision"]["review_skills"] is True
    assert "skill_tool_call_interval" in result["decision"]["trigger_reasons"]


def test_evaluate_trigger_keyword_scans_only_user_message(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    transcript = Path(str(payload["transcript_path"]))
    _append_jsonl(
        transcript,
        [
            {"type": "response_item", "payload": {"role": "assistant", "content": "remember this"}},
            {"type": "response_item", "payload": {"role": "tool", "content": "skill"}},
            {"type": "response_item", "payload": {"role": "user", "content": "下次记住这个规则"}},
        ],
    )

    result = evaluate_trigger_policy(payload, SessionReflectionTriggerConfig(), home=tmp_path)

    assert result["status"] == "queued"
    assert result["decision"]["review_memory"] is True
    assert "high_signal_memory_keyword" in result["decision"]["trigger_reasons"]
    assert "记住" in result["decision"]["matched_keywords"]
    assert "matched_context_snippet" in result["decision"]


def test_english_keyword_uses_word_boundary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    transcript = Path(str(payload["transcript_path"]))
    _append_jsonl(
        transcript,
        [
            {"type": "response_item", "payload": {"role": "user", "content": "this is unskilled text"}},
        ],
    )

    result = evaluate_trigger_policy(payload, SessionReflectionTriggerConfig(), home=tmp_path)

    assert result["status"] == "archive_only"
    assert result["decision"]["matched_keywords"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest -q tests/test_session_reflection_trigger.py
```

Expected: fail because `evaluate_trigger_policy` does not exist.

- [ ] **Step 3: Add transcript extraction and keyword matching**

Add these helpers to `trigger.py`:

```python
import re

MEMORY_KEYWORDS = ["记住", "记录一下", "下次", "以后不要", "不要再", "规则", "约定", "偏好", "习惯", "memory"]
SKILL_KEYWORDS = ["skill", "技能", "工作流", "workflow", "复用", "沉淀", "SOP", "runbook"]
HANDOFF_KEYWORDS = ["交接", "handoff", "status", "收尾", "状态文档"]
CORRECTION_KEYWORDS = ["不是这个意思", "你理解错了", "不要改代码", "别改代码", "恢复", "回退", "过度抽象"]
ENGLISH_KEYWORDS = {"memory", "skill", "workflow", "SOP", "runbook", "handoff", "status"}


def _extract_source(entry: dict[str, Any]) -> dict[str, Any]:
    payload = entry.get("payload")
    if entry.get("type") == "response_item" and isinstance(payload, dict):
        return payload
    return entry


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    if isinstance(value, dict):
        text = value.get("text") or value.get("content") or value.get("message")
        return text if isinstance(text, str) else ""
    return ""


def _tool_call_summary(source: dict[str, Any]) -> tuple[int, str]:
    calls = source.get("tool_calls")
    if not isinstance(calls, list):
        return 0, ""
    summaries: list[str] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = call.get("name") or function.get("name") or call.get("tool_name") or "tool"
        args = call.get("arguments") or function.get("arguments") or ""
        args_text = json.dumps(args, ensure_ascii=False, sort_keys=True) if not isinstance(args, str) else args
        summaries.append(f"{name}: {args_text[:300]}")
    return len(summaries), "\n".join(summaries)


def _scan_keyword_group(text: str, keywords: list[str]) -> list[str]:
    matched: list[str] = []
    for keyword in keywords:
        if keyword in ENGLISH_KEYWORDS:
            if re.search(rf"\b{re.escape(keyword)}\b", text, flags=re.IGNORECASE):
                matched.append(keyword)
        elif keyword in text:
            matched.append(keyword)
    return matched


def _read_transcript_delta(path: Path, offset: int) -> tuple[int, int, int, list[str], int, str]:
    readable_chars = 0
    tool_calls = 0
    user_texts: list[str] = []
    message_count = 0
    last_uid = ""
    with path.open("rb") as handle:
        size = path.stat().st_size
        if offset < 0 or offset > size:
            offset = 0
        handle.seek(offset)
        for raw in handle:
            try:
                line = raw.decode("utf-8", errors="replace").strip()
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            source = _extract_source(entry)
            role = str(source.get("role") or "").lower()
            text = _extract_text(source.get("content")) or _extract_text(source.get("text")) or _extract_text(source.get("message"))
            calls_count, calls_summary = _tool_call_summary(source)
            if role in {"user", "assistant"} and text:
                readable_chars += len(text)
                message_count += 1
            if role == "user" and text:
                user_texts.append(text)
            if calls_count:
                tool_calls += calls_count
                readable_chars += len(calls_summary)
                message_count += 1
            last_uid = str(source.get("id") or entry.get("id") or last_uid)
        return handle.tell(), readable_chars, tool_calls, user_texts, message_count, last_uid
```

- [ ] **Step 4: Implement decision evaluation**

Add to `trigger.py`:

```python
def evaluate_trigger_policy(
    payload: dict[str, Any],
    config: Any,
    *,
    home: str | Path | None = None,
    active_job: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update session trigger state and return an archive/queued decision."""
    session_id = payload_session_id(payload)
    paths = trigger_paths_for_payload(payload, home=home)
    with session_trigger_lock(paths):
        state = load_trigger_state(paths, session_id=session_id)
        transcript_path = Path(str(payload.get("transcript_path") or payload.get("codex_transcript_path") or ""))
        warnings: list[str] = []
        new_offset = int(state.get("last_counted_byte_offset") or 0)
        readable_delta = 0
        tool_delta = 0
        user_texts: list[str] = []
        message_count_delta = 0
        last_uid = str(state.get("last_counted_event_uid") or "")
        if transcript_path.is_file():
            new_offset, readable_delta, tool_delta, user_texts, message_count_delta, last_uid = _read_transcript_delta(
                transcript_path,
                int(state.get("last_counted_byte_offset") or 0),
            )
        else:
            warnings.append("transcript_unreadable")

        state["stops_since_memory_review"] = int(state.get("stops_since_memory_review") or 0) + 1
        state["readable_chars_since_memory_review"] = int(state.get("readable_chars_since_memory_review") or 0) + readable_delta
        state["tool_calls_since_skill_review"] = int(state.get("tool_calls_since_skill_review") or 0) + tool_delta
        state["last_counted_byte_offset"] = new_offset
        state["last_counted_message_index"] = int(state.get("last_counted_message_index") or 0) + message_count_delta
        state["last_counted_event_uid"] = last_uid

        matched_memory: list[str] = []
        matched_skill: list[str] = []
        matched_snippet = ""
        if config.high_signal_immediate:
            for user_text in user_texts:
                mem_hits = (
                    _scan_keyword_group(user_text, MEMORY_KEYWORDS)
                    + _scan_keyword_group(user_text, HANDOFF_KEYWORDS)
                    + _scan_keyword_group(user_text, CORRECTION_KEYWORDS)
                )
                skill_hits = _scan_keyword_group(user_text, SKILL_KEYWORDS)
                if mem_hits or skill_hits:
                    matched_memory.extend(mem_hits)
                    matched_skill.extend(skill_hits)
                    matched_snippet = user_text[:160]

        reasons: list[str] = []
        review_memory = False
        review_skills = False
        if int(state["stops_since_memory_review"]) >= config.memory_stop_interval:
            review_memory = True
            reasons.append("memory_stop_interval")
        if int(state["readable_chars_since_memory_review"]) >= config.memory_context_chars:
            review_memory = True
            reasons.append("memory_context_chars")
        if int(state["tool_calls_since_skill_review"]) >= config.skill_tool_call_interval:
            review_skills = True
            reasons.append("skill_tool_call_interval")
        if matched_memory:
            review_memory = True
            reasons.append("high_signal_memory_keyword")
        if matched_skill:
            review_skills = True
            reasons.append("high_signal_skill_keyword")

        if active_job:
            decision = {
                "schema_version": 1,
                "status": "deferred_active_job",
                "review_memory": review_memory,
                "review_skills": review_skills,
                "trigger_reasons": ["active_job_running"],
                "matched_keywords": sorted(set(matched_memory + matched_skill)),
                "matched_context_snippet": matched_snippet,
                "skip_reason": "active_job_running",
                "warnings": warnings,
                "counters": _counter_snapshot(state),
            }
        elif review_memory or review_skills:
            decision = {
                "schema_version": 1,
                "status": "queued",
                "review_memory": review_memory,
                "review_skills": review_skills,
                "trigger_reasons": reasons,
                "matched_keywords": sorted(set(matched_memory + matched_skill)),
                "matched_context_snippet": matched_snippet,
                "warnings": warnings,
                "counters": _counter_snapshot(state),
            }
            state["active_job_id"] = "pending"
        else:
            decision = {
                "schema_version": 1,
                "status": "archive_only",
                "review_memory": False,
                "review_skills": False,
                "trigger_reasons": [],
                "matched_keywords": [],
                "matched_context_snippet": "",
                "skip_reason": warnings[0] if warnings else "below_threshold",
                "warnings": warnings,
                "counters": _counter_snapshot(state),
            }

        state["last_decision"] = decision
        write_trigger_state(paths, state)
        append_decision(paths, decision)
        return {"status": decision["status"], "decision": decision, "state": state, "paths": paths}


def _counter_snapshot(state: dict[str, Any]) -> dict[str, int]:
    return {
        "stops_since_memory_review": int(state.get("stops_since_memory_review") or 0),
        "readable_chars_since_memory_review": int(state.get("readable_chars_since_memory_review") or 0),
        "tool_calls_since_skill_review": int(state.get("tool_calls_since_skill_review") or 0),
    }
```

- [ ] **Step 5: Run trigger tests**

Run:

```bash
uv run pytest -q tests/test_session_reflection_trigger.py
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/codex_self_evolution/session_reflection/trigger.py tests/test_session_reflection_trigger.py
git commit -m "feat: evaluate session reflection triggers"
```

---

## Task 4: Active Job Guard And Job Schema

**Files:**
- Modify: `src/codex_self_evolution/session_reflection/state.py`
- Modify: `src/codex_self_evolution/session_reflection/guard.py`
- Modify: `tests/test_session_reflection_state.py`
- Modify: `tests/test_session_reflection_guard.py`

- [ ] **Step 1: Update failing state and guard tests**

In `tests/test_session_reflection_state.py`, replace `test_find_existing_parent_job_detects_queued_and_succeeded` with:

```python
def test_find_active_parent_job_detects_only_queued_and_running(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    created = create_job_from_payload(_payload(repo), home=tmp_path)

    assert find_active_parent_job("parent-1", home=tmp_path)["job_id"] == created["job_id"]

    update_job_status(created["job_id"], "succeeded", home=tmp_path, receipt_path="receipt.json")

    assert find_active_parent_job("parent-1", home=tmp_path) is None
```

Add import:

```python
from codex_self_evolution.session_reflection.state import find_active_parent_job
```

Add a job schema test:

```python
def test_create_job_from_payload_accepts_trigger_decision_fields(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    decision = {
        "status": "queued",
        "review_memory": True,
        "review_skills": True,
        "trigger_reasons": ["high_signal_skill_keyword"],
        "counters": {
            "stops_since_memory_review": 2,
            "readable_chars_since_memory_review": 9300,
            "tool_calls_since_skill_review": 11,
        },
    }

    job = create_job_from_payload(
        _payload(repo),
        home=tmp_path,
        trigger_decision=decision,
        covered_byte_offset=123,
        covered_message_index=4,
        covered_event_uid="event-1",
        skill_generation_mode="one_shot_active",
    )

    assert job["schema_version"] == 2
    assert job["review_memory"] is True
    assert job["review_skills"] is True
    assert job["trigger_reasons"] == ["high_signal_skill_keyword"]
    assert job["skill_generation_mode"] == "one_shot_active"
    assert job["covered_byte_offset"] == 123
    assert job["counter_snapshot"]["tool_calls_since_skill_review"] == 11
```

In `tests/test_session_reflection_guard.py`, replace `test_guard_skips_existing_parent_job` with:

```python
def test_recursion_guard_does_not_skip_existing_parent_job(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    create_job_from_payload(payload, home=tmp_path)

    decision = evaluate_recursion_guard(payload, home=tmp_path)

    assert decision.skip is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest -q tests/test_session_reflection_state.py tests/test_session_reflection_guard.py
```

Expected: fail because `find_active_parent_job` is missing and guard still skips parent jobs.

- [ ] **Step 3: Modify state job lookup and schema**

In `src/codex_self_evolution/session_reflection/state.py`, change lookup constants and add:

```python
ACTIVE_PARENT_STATUSES = {"queued", "running"}
SESSION_REFLECTION_MODEL = "gpt-5.3-codex-spark"
```

Replace `find_existing_parent_job()` with:

```python
def find_active_parent_job(parent_session_id: str, *, home: str | Path | None = None) -> dict[str, Any] | None:
    """Find queued or running reflection job for one parent session."""
    for job in reversed(list_jobs(home=home)):
        if job.get("parent_session_id") != parent_session_id:
            continue
        if job.get("status") in ACTIVE_PARENT_STATUSES:
            return job
    return None


def find_existing_parent_job(parent_session_id: str, *, home: str | Path | None = None) -> dict[str, Any] | None:
    """Find the newest persisted reflection job for diagnostics."""
    for job in reversed(list_jobs(home=home)):
        if job.get("parent_session_id") == parent_session_id:
            return job
    return None
```

Update `create_job_from_payload()` signature:

```python
def create_job_from_payload(
    payload: dict[str, Any],
    *,
    home: str | Path | None = None,
    trigger_decision: dict[str, Any] | None = None,
    covered_byte_offset: int = 0,
    covered_message_index: int = 0,
    covered_event_uid: str = "",
    skill_generation_mode: str = "one_shot_active",
) -> dict[str, Any]:
```

Inside the function, compute:

```python
    decision = trigger_decision or {}
    counters = decision.get("counters") if isinstance(decision.get("counters"), dict) else {}
```

Set these fields in the job dict:

```python
        "schema_version": 2 if decision else 1,
        "review_memory": bool(decision.get("review_memory")),
        "review_skills": bool(decision.get("review_skills")),
        "trigger_reasons": list(decision.get("trigger_reasons") or []),
        "skill_generation_mode": skill_generation_mode,
        "covered_byte_offset": int(covered_byte_offset),
        "covered_message_index": int(covered_message_index),
        "covered_event_uid": str(covered_event_uid or ""),
        "counter_snapshot": {
            "stops_since_memory_review": int(counters.get("stops_since_memory_review") or 0),
            "readable_chars_since_memory_review": int(counters.get("readable_chars_since_memory_review") or 0),
            "tool_calls_since_skill_review": int(counters.get("tool_calls_since_skill_review") or 0),
        },
        "trigger_decision": decision,
```

- [ ] **Step 4: Modify recursion guard**

In `src/codex_self_evolution/session_reflection/guard.py`, remove the parent-job lookup import and delete this block:

```python
    if session_id and find_existing_parent_job(session_id, home=home):
        return GuardDecision(True, "parent_job_exists", session_id)
```

Keep thread source, child registry, transcript marker, and global lock checks unchanged.

- [ ] **Step 5: Run state and guard tests**

Run:

```bash
uv run pytest -q tests/test_session_reflection_state.py tests/test_session_reflection_guard.py
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/codex_self_evolution/session_reflection/state.py src/codex_self_evolution/session_reflection/guard.py tests/test_session_reflection_state.py tests/test_session_reflection_guard.py
git commit -m "feat: split reflection recursion and active job guards"
```

---

## Task 5: Trigger-Aware Enqueue And Stop Hook

**Files:**
- Modify: `src/codex_self_evolution/session_reflection/runner.py`
- Modify: `src/codex_self_evolution/cli.py`
- Modify: `tests/test_session_reflection_runner.py`
- Modify: `tests/test_session_reflection_cli.py`

- [ ] **Step 1: Write failing enqueue tests**

In `tests/test_session_reflection_runner.py`, update `test_enqueue_reflection_from_payload_creates_job`:

```python
def test_enqueue_reflection_from_payload_archives_only_below_threshold(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Low-signal Stop payloads update trigger state without creating a job."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))

    result = enqueue_reflection_from_payload(_payload(repo), home=home)

    assert result["status"] == "archive_only"
    assert result["decision"]["skip_reason"] == "below_threshold"
    assert not (home / "session_reflection" / "jobs").exists()
```

Add:

```python
def test_enqueue_reflection_from_payload_queues_when_trigger_hits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """High-signal user keywords create a trigger-scoped reflection job."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    Path(str(payload["transcript_path"])).write_text(
        json.dumps({"type": "response_item", "payload": {"role": "user", "content": "请把这个工作流沉淀成 skill"}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))

    result = enqueue_reflection_from_payload(payload, home=home)

    assert result["status"] == "queued"
    job = result["job"]
    assert job["review_skills"] is True
    assert job["skill_generation_mode"] == "one_shot_active"
    assert job["trigger_decision"]["matched_keywords"] == ["skill", "工作流", "沉淀"]
```

Add active job deferral:

```python
def test_enqueue_reflection_from_payload_defers_when_active_job_exists(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Active parent jobs prevent a second fork but do not skip trigger accounting."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    create_job_from_payload(payload, home=home)
    Path(str(payload["transcript_path"])).write_text(
        json.dumps({"type": "response_item", "payload": {"role": "user", "content": "记住这个规则"}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))

    result = enqueue_reflection_from_payload(payload, home=home)

    assert result["status"] == "deferred_active_job"
    assert result["decision"]["skip_reason"] == "active_job_running"
```

- [ ] **Step 2: Write failing CLI spawn tests**

In `tests/test_session_reflection_cli.py`, update skipped enqueue test to include archive-only:

```python
def test_stop_review_from_stdin_archive_only_does_not_spawn_reflection(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Archive-only trigger decisions do not spawn session-reflect."""
    monkeypatch.setattr(
        cli,
        "enqueue_reflection_from_payload",
        lambda payload, *, home=None: {"status": "archive_only", "decision": {"skip_reason": "below_threshold"}},
    )
    monkeypatch.setattr(cli, "_spawn_session_archive_from_stop_payload", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not spawn reflection")),
    )
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(_codex_payload())))

    exit_code = cli.main(["stop-review", "--from-stdin"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {"continue": True}
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
uv run pytest -q tests/test_session_reflection_runner.py::test_enqueue_reflection_from_payload_archives_only_below_threshold tests/test_session_reflection_runner.py::test_enqueue_reflection_from_payload_queues_when_trigger_hits tests/test_session_reflection_runner.py::test_enqueue_reflection_from_payload_defers_when_active_job_exists tests/test_session_reflection_cli.py::test_stop_review_from_stdin_archive_only_does_not_spawn_reflection
```

Expected: fail because enqueue still always creates jobs for normal parent sessions.

- [ ] **Step 4: Implement trigger-aware enqueue**

In `src/codex_self_evolution/session_reflection/runner.py`, import:

```python
from .state import find_active_parent_job
from .trigger import TriggerLockBusy, evaluate_trigger_policy, payload_session_id, write_trigger_state
```

Replace `enqueue_reflection_from_payload()` body with:

```python
def enqueue_reflection_from_payload(payload: dict[str, Any], *, home: str | Path | None = None) -> dict[str, Any]:
    """Evaluate trigger policy and create a queued reflection job when needed."""
    config = load_config(home=Path(home).expanduser().resolve() if home else None).config.session_reflection
    if not config.enabled:
        return {"status": "skipped", "reason": "disabled"}

    guard_decision = evaluate_recursion_guard(payload, home=home)
    if guard_decision.skip:
        return {"status": "skipped", "reason": guard_decision.reason, "detail": guard_decision.detail}

    parent_session_id = payload_session_id(payload)
    active_job = find_active_parent_job(
        parent_session_id,
        home=home,
        stale_after_seconds=config.trigger.active_job_stale_seconds,
    )
    try:
        trigger_result = evaluate_trigger_policy(payload, config.trigger, home=home, active_job=active_job)
    except TriggerLockBusy as exc:
        return {"status": "archive_only", "reason": "trigger_lock_busy", "detail": str(exc)}

    if trigger_result["status"] != "queued":
        return trigger_result

    state = trigger_result["state"]
    decision = trigger_result["decision"]
    job = create_job_from_payload(
        payload,
        home=home,
        trigger_decision=decision,
        covered_byte_offset=int(state.get("last_counted_byte_offset") or 0),
        covered_message_index=int(state.get("last_counted_message_index") or 0),
        covered_event_uid=str(state.get("last_counted_event_uid") or ""),
        skill_generation_mode=config.trigger.skill_generation_mode,
    )
    state["active_job_id"] = job["job_id"]
    write_trigger_state(trigger_result["paths"], state)
    trigger_result["state"] = state
    return {"status": "queued", "job_id": job["job_id"], "job": job, "decision": decision}
```

If `find_active_parent_job` does not yet accept `stale_after_seconds`, add it in Task 4 or now:

```python
def find_active_parent_job(
    parent_session_id: str,
    *,
    home: str | Path | None = None,
    stale_after_seconds: int = DEFAULT_LOCK_STALE_SECONDS,
) -> dict[str, Any] | None:
```

Treat stale active jobs as not active by parsing `updated_at` and comparing to `utc_now()`.

- [ ] **Step 5: Confirm CLI already only spawns queued jobs**

`src/codex_self_evolution/cli.py` already checks:

```python
if queued.get("status") != "queued" or not job_id:
    print(json.dumps({"continue": True}))
    return 0
```

No CLI code change is required unless tests reveal naming mismatch.

- [ ] **Step 6: Run tests**

Run:

```bash
uv run pytest -q tests/test_session_reflection_runner.py tests/test_session_reflection_cli.py
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add src/codex_self_evolution/session_reflection/runner.py src/codex_self_evolution/session_reflection/state.py tests/test_session_reflection_runner.py tests/test_session_reflection_cli.py
git commit -m "feat: gate session reflection with trigger policy"
```

---

## Task 6: Prompt Scope And Runner Counter Reset

**Files:**
- Modify: `src/codex_self_evolution/session_reflection/prompt.py`
- Modify: `src/codex_self_evolution/session_reflection/runner.py`
- Modify: `tests/test_session_reflection_runner.py`
- Modify: `tests/test_session_reflection_prompt.py`

- [ ] **Step 1: Write failing prompt test**

Append to `tests/test_session_reflection_prompt.py`:

```python
def test_reflection_prompt_includes_trigger_scope_and_skill_mode(tmp_path: Path) -> None:
    """Prompt tells child which scopes to review and how to handle skills."""
    prompt = build_reflection_prompt(
        job_id="job-1",
        parent_session_id="parent-1",
        cwd=tmp_path,
        memory_user_path=tmp_path / "USER.md",
        memory_project_path=tmp_path / "MEMORY.md",
        skills_root=tmp_path / "skills",
        receipt_path=tmp_path / "receipt.json",
        review_memory=True,
        review_skills=False,
        trigger_reasons=["memory_stop_interval"],
        skill_generation_mode="one_shot_active",
    )

    assert "Review memory: true" in prompt
    assert "Review skills: false" in prompt
    assert "Trigger reasons: memory_stop_interval" in prompt
    assert "Skill generation mode: one_shot_active" in prompt
    assert "Do not evaluate skills when Review skills is false." in prompt
```

- [ ] **Step 2: Write failing runner reset test**

Append to `tests/test_session_reflection_runner.py`:

```python
def test_run_reflection_job_resets_trigger_counters_after_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Successful validation subtracts the job counter snapshot."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))
    monkeypatch.setenv("CSEP_CODEX_SKILLS_DIR", str(tmp_path / "skills"))
    payload = _payload(repo)
    decision = {
        "status": "queued",
        "review_memory": True,
        "review_skills": False,
        "trigger_reasons": ["memory_stop_interval"],
        "counters": {
            "stops_since_memory_review": 3,
            "readable_chars_since_memory_review": 9000,
            "tool_calls_since_skill_review": 0,
        },
    }
    job = create_job_from_payload(payload, home=home, trigger_decision=decision, skill_generation_mode="one_shot_active")
    from codex_self_evolution.session_reflection.trigger import (
        load_trigger_state,
        trigger_paths_for_payload,
        write_trigger_state,
    )
    trigger_paths = trigger_paths_for_payload(payload, home=home)
    state = load_trigger_state(trigger_paths, session_id="parent-1")
    state["active_job_id"] = job["job_id"]
    state["stops_since_memory_review"] = 4
    state["readable_chars_since_memory_review"] = 12000
    write_trigger_state(trigger_paths, state)

    run_reflection_job(str(job["job_id"]), home=home, client=FakeReflectionClient())

    updated = load_trigger_state(trigger_paths, session_id="parent-1")
    assert updated["stops_since_memory_review"] == 1
    assert updated["readable_chars_since_memory_review"] == 3000
    assert updated["active_job_id"] is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
uv run pytest -q tests/test_session_reflection_prompt.py tests/test_session_reflection_runner.py::test_run_reflection_job_resets_trigger_counters_after_success
```

Expected: fail because prompt signature and runner reset integration are missing.

- [ ] **Step 4: Update prompt signature and body**

In `src/codex_self_evolution/session_reflection/prompt.py`, add parameters:

```python
    review_memory: bool = True,
    review_skills: bool = True,
    trigger_reasons: list[str] | None = None,
    skill_generation_mode: str = "one_shot_active",
```

Add to prompt text after receipt path:

```python
        f"Review memory: {str(review_memory).lower()}\n"
        f"Review skills: {str(review_skills).lower()}\n"
        f"Trigger reasons: {', '.join(trigger_reasons or [])}\n"
        f"Skill generation mode: {skill_generation_mode}\n\n"
        "Do not evaluate memory when Review memory is false.\n"
        "Do not evaluate skills when Review skills is false.\n"
        "When Skill generation mode is one_shot_active, a complete workflow candidate may become an active csep-reflect-* skill in this run.\n"
        "When Skill generation mode is evidence_first, record workflow evidence or skipped candidates but do not publish an active skill.\n\n"
```

- [ ] **Step 5: Pass job trigger fields into prompt**

In `runner.py`, update `build_reflection_prompt()` call:

```python
            review_memory=bool(job.get("review_memory", True)),
            review_skills=bool(job.get("review_skills", True)),
            trigger_reasons=list(job.get("trigger_reasons") or []),
            skill_generation_mode=str(job.get("skill_generation_mode") or "one_shot_active"),
```

- [ ] **Step 6: Reset trigger counters after validation**

In `runner.py`, import:

```python
from .trigger import load_trigger_state, reset_counters_after_job, trigger_paths_for_payload, write_trigger_state
from .state import utc_timestamp
```

After `validation = validate_receipt(...)` and before `return update_job_status(...)`, add:

```python
        _reset_trigger_state_for_job(job, validation, home=resolved_home)
```

Add helper near the bottom:

```python
def _reset_trigger_state_for_job(job: dict[str, Any], validation: dict[str, Any], *, home: Path | None) -> None:
    """Apply trigger counter reset after a reflection job finishes."""
    payload = job.get("raw_payload") if isinstance(job.get("raw_payload"), dict) else {}
    if not payload:
        return
    paths = trigger_paths_for_payload(payload, home=home)
    state = load_trigger_state(paths, session_id=str(job.get("parent_session_id") or "unknown-session"))
    status = str(validation.get("status") or "")
    reason = str(validation.get("reason") or "")
    succeeded = status in {"succeeded", "skipped_empty"}
    partial = status == "partial"
    memory_succeeded = bool(job.get("review_memory")) and (succeeded or partial)
    skill_succeeded = bool(job.get("review_skills")) and (succeeded or partial) and reason != "skill_invalid"
    updated = reset_counters_after_job(
        state,
        job,
        memory_succeeded=memory_succeeded,
        skill_succeeded=skill_succeeded,
        now=utc_timestamp(),
    )
    write_trigger_state(paths, updated)
```

This helper intentionally treats `skipped_empty` as success because `Nothing to save` / no durable signal should reset the covered scope.

- [ ] **Step 7: Run tests**

Run:

```bash
uv run pytest -q tests/test_session_reflection_prompt.py tests/test_session_reflection_runner.py
```

Expected: pass.

- [ ] **Step 8: Commit**

```bash
git add src/codex_self_evolution/session_reflection/prompt.py src/codex_self_evolution/session_reflection/runner.py tests/test_session_reflection_prompt.py tests/test_session_reflection_runner.py
git commit -m "feat: pass trigger scope to reflection worker"
```

---

## Task 7: Status Surface And Diagnostics

**Files:**
- Modify: `src/codex_self_evolution/session_reflection/runner.py`
- Modify: `tests/test_session_reflection_runner.py`
- Modify: `tests/test_diagnostics.py`

- [ ] **Step 1: Write failing status test**

Append to `tests/test_session_reflection_runner.py`:

```python
def test_session_reflection_status_includes_trigger_summary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Status includes trigger sidecar summary for debugging."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = _payload(repo)
    monkeypatch.setenv("CODEX_SELF_EVOLUTION_HOME", str(home))
    from codex_self_evolution.session_reflection.trigger import (
        append_decision,
        load_trigger_state,
        trigger_paths_for_payload,
        write_trigger_state,
    )
    trigger_paths = trigger_paths_for_payload(payload, home=home)
    state = load_trigger_state(trigger_paths, session_id="parent-1")
    state["stops_since_memory_review"] = 2
    state["last_decision"] = {"status": "archive_only", "skip_reason": "below_threshold"}
    write_trigger_state(trigger_paths, state)
    append_decision(trigger_paths, state["last_decision"])

    status = session_reflection_status(home=home)

    assert status["trigger"]["session_count"] == 1
    assert status["trigger"]["latest_decision"]["status"] == "archive_only"
    assert status["trigger"]["latest_state"]["stops_since_memory_review"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest -q tests/test_session_reflection_runner.py::test_session_reflection_status_includes_trigger_summary
```

Expected: fail because `status["trigger"]` is missing.

- [ ] **Step 3: Implement trigger status summary**

In `runner.py`, add:

```python
def _trigger_status_summary(paths) -> dict[str, Any]:
    """Return compact status for session trigger sidecars."""
    triggers_dir = paths.root / "triggers"
    if not triggers_dir.is_dir():
        return {"exists": False, "session_count": 0}
    state_files = sorted(triggers_dir.glob("*/state.json"))
    latest_state: dict[str, Any] | None = None
    latest_decision: dict[str, Any] | None = None
    if state_files:
        latest_path = max(state_files, key=lambda item: item.stat().st_mtime)
        try:
            loaded = load_json(latest_path)
            if isinstance(loaded, dict):
                latest_state = {
                    "session_id": loaded.get("session_id"),
                    "stops_since_memory_review": loaded.get("stops_since_memory_review"),
                    "readable_chars_since_memory_review": loaded.get("readable_chars_since_memory_review"),
                    "tool_calls_since_skill_review": loaded.get("tool_calls_since_skill_review"),
                    "active_job_id": loaded.get("active_job_id"),
                    "last_decision": loaded.get("last_decision"),
                }
                if isinstance(loaded.get("last_decision"), dict):
                    latest_decision = loaded["last_decision"]
        except Exception:
            latest_state = {"unreadable": True, "path": str(latest_path)}
    return {
        "exists": True,
        "session_count": len(state_files),
        "latest_state": latest_state,
        "latest_decision": latest_decision,
    }
```

Then include it in `session_reflection_status()`:

```python
        "trigger": _trigger_status_summary(paths),
```

- [ ] **Step 4: Run status tests**

Run:

```bash
uv run pytest -q tests/test_session_reflection_runner.py::test_session_reflection_status_includes_trigger_summary tests/test_diagnostics.py
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/codex_self_evolution/session_reflection/runner.py tests/test_session_reflection_runner.py tests/test_diagnostics.py
git commit -m "feat: expose reflection trigger status"
```

---

## Task 8: Full Regression And Documentation Touch-Ups

**Files:**
- Modify if needed: `docs/superpowers/specs/2026-05-15-session-reflection-trigger-policy-design.md`
- Modify if needed: `docs/superpowers/plans/2026-05-15-session-reflection-trigger-policy.md`

- [ ] **Step 1: Run focused reflection suite**

Run:

```bash
uv run pytest -q tests/test_session_reflection_config.py tests/test_session_reflection_state.py tests/test_session_reflection_guard.py tests/test_session_reflection_trigger.py tests/test_session_reflection_prompt.py tests/test_session_reflection_runner.py tests/test_session_reflection_cli.py
```

Expected: pass.

- [ ] **Step 2: Run CLI and diagnostics regression**

Run:

```bash
uv run pytest -q tests/test_codex_bridge.py tests/test_config_file.py tests/test_diagnostics.py tests/test_session_recall_archive.py
```

Expected: pass.

- [ ] **Step 3: Run full suite**

Run:

```bash
uv run pytest -q
```

Expected: pass.

- [ ] **Step 4: Check docs formatting**

Run:

```bash
git diff --check -- docs/superpowers/specs/2026-05-15-session-reflection-trigger-policy-design.md docs/superpowers/plans/2026-05-15-session-reflection-trigger-policy.md
```

Expected: no output.

- [ ] **Step 5: Inspect final working tree**

Run:

```bash
git status --short --branch
```

Expected: only intentional changes for this feature are staged or committed. `tmp/status/` remains untracked and must not be staged.

- [ ] **Step 6: Commit final docs if changed**

If Task 8 modified the spec or plan, commit:

```bash
git add docs/superpowers/specs/2026-05-15-session-reflection-trigger-policy-design.md docs/superpowers/plans/2026-05-15-session-reflection-trigger-policy.md
git commit -m "docs: finalize reflection trigger policy plan"
```

If no docs changed during Task 8, skip this commit and record the skip in the implementation summary.

---

## Self-Review Checklist

- Spec coverage:
  - Per-session sidecar state: Task 1 and Task 2.
  - Per-session lock: Task 2.
  - Byte-offset high watermark: Task 3.
  - Deterministic thresholds and keywords: Task 3.
  - Parent session subsequent reviews: Task 4.
  - Active job deferral and stale behavior: Task 4 and Task 5.
  - Trigger-aware Stop hook enqueue: Task 5.
  - Skill generation mode prompt propagation: Task 6.
  - Counter snapshot reset: Task 2 and Task 6.
  - Status observability: Task 7.
  - Regression coverage: Task 8.

- Placeholder scan:
  - No `TBD`, `TODO`, or "implement later" steps.
  - Every code-changing task includes concrete tests, code snippets, commands, and expected outcomes.

- Type/name consistency:
  - `SessionReflectionTriggerConfig`, `build_session_reflection_trigger_paths`, `evaluate_trigger_policy`, `find_active_parent_job`, `reset_counters_after_job`, and `skill_generation_mode` are introduced before later tasks consume them.
  - Trigger decision statuses are `archive_only`, `queued`, and `deferred_active_job`.
  - Skip reasons include `below_threshold`, `active_job_running`, `trigger_lock_busy`, and recursion guard reasons from existing guard code.
